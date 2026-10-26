"""
XML/document_session.py — Central XML Document Session Model

Manages the active in-memory XML document state for XML Analyzer:
  • Single canonical DOM root (lxml.etree._Element) for full consumer compatibility
  • Atomic document loading (failed load leaves previous session state pristine)
  • Raw text draft separation (preserves user's draft even when syntax is invalid)
  • Explicit syntax validation and error tracking (line, column, message)
  • Ground-truth dirty-state computation (raw text comparison vs last saved text)
  • UTF-8 BOM detection and clean draft contract (BOM tracked via flag, excluded from raw_text_draft)
  • Precise W3C XML declaration detection (distinguishes XMLDecl from PIs like <?xml-stylesheet ...?>)
  • Metadata consistency (DOM metadata preserved alongside dom_root on invalid drafts)
"""

from __future__ import annotations

import codecs
import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from lxml import etree


@dataclass(frozen=True)
class SyntaxErrorInfo:
    """Detailed XML syntax error information."""
    line: int
    column: int
    message: str
    original_line: int = 0
    original_column: int = 0

    def __post_init__(self):
        if not self.original_line:
            object.__setattr__(self, "original_line", self.line)
        if not self.original_column:
            object.__setattr__(self, "original_column", self.column)


def clean_error_message(msg: str) -> str:
    """
    Cleans repetitive location substrings from parser error messages.
    e.g., "expected '>', line 48, column 16" -> "expected '>'"
    e.g., "Opening and ending tag mismatch: a line 1 and b, line 2, column 3 (<string>, line 2)"
          -> "Opening and ending tag mismatch: a line 1 and b"
    """
    cleaned = msg.strip()
    cleaned = re.sub(r'\s*\([^)]*line\s+\d+[^)]*\)', '', cleaned)
    cleaned = re.sub(r',?\s*line\s+\d+(\s*,\s*column\s+\d+)?\s*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def adjust_syntax_error_line(
    lines: list[str],
    reported_line: int,
    reported_col: int,
    raw_message: str,
) -> int:
    """
    Controlled correction of reported error line numbers when the parser
    reports the line where it discovered the error rather than where the syntax was broken.

    Rule:
      When raw_message indicates a missing '>' (e.g. "expected '>'"):
      If the parser encounters a new '<' at reported_line, but the previous non-empty line
      has an unclosed tag (a '<' not closed by a '>'), the true error is on that previous line.
      If conditions are not reliably met, reported_line is preserved.
    """
    if not lines or reported_line <= 1:
        return reported_line

    msg_lower = raw_message.lower()
    if "expected '>'" in msg_lower or "err_gt_required" in msg_lower:
        curr_idx = reported_line - 1
        if curr_idx < len(lines):
            prev_idx = curr_idx - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1

            if prev_idx >= 0:
                prev_line = lines[prev_idx].rstrip()
                last_open = prev_line.rfind("<")
                last_close = prev_line.rfind(">")
                if last_open != -1 and (last_close == -1 or last_open > last_close):
                    curr_line_stripped = lines[curr_idx].strip()
                    if curr_line_stripped.startswith("<") or curr_line_stripped.startswith("</"):
                        return prev_idx + 1

    return reported_line


def save_xml_atomic(filepath: str | Path, text: str, has_bom: bool = False) -> Path:
    """
    Atomically writes XML text to filepath in the target directory using a temporary file.
    Preserves exact textual formatting and adds UTF-8 BOM if has_bom is True.
    Guarantees target file is not corrupted if an error occurs during writing.
    """
    target = Path(filepath).resolve()
    target_dir = target.parent

    if not target_dir.exists():
        raise FileNotFoundError(f"Target directory does not exist: {target_dir}")
    if not target_dir.is_dir():
        raise NotADirectoryError(f"Target parent is not a directory: {target_dir}")

    raw_bytes = text.encode("utf-8")
    if has_bom:
        raw_bytes = codecs.BOM_UTF8 + raw_bytes

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=f".~{target.name}_",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(raw_bytes)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, target)
        return target
    except Exception as e:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise e


def detect_xml_declaration(text: str) -> bool:
    """
    Returns True if text begins with the XML declaration prefix ('<?xml' followed
    immediately by whitespace: #x20, #x9, #xD, #xA).
    Does NOT match processing instructions like '<?xml-stylesheet ...?>' or '<?xml-model ...?>'.
    Note: This verifies the textual prefix only; full syntax validity is guaranteed
    when parsed by lxml.etree.
    """
    stripped = text.lstrip()
    return (
        stripped.startswith("<?xml")
        and len(stripped) > 5
        and stripped[5] in (" ", "\t", "\r", "\n")
    )


def extract_document_metadata(
    tree: etree._ElementTree, raw_text: str
) -> tuple[bool, Optional[str], Optional[str], Optional[bool]]:
    """
    Extracts XML declaration presence and W3C metadata from a parsed tree and raw text.
    Returns: (has_xml_declaration, xml_version, xml_encoding, xml_standalone)

    Rules:
      • has_xml_declaration: True if raw_text begins with an XML declaration header
        and the document was successfully parsed by lxml.
      • xml_version: docinfo.xml_version if declaration is present, else None.
      • xml_encoding: docinfo.encoding if declaration is present, else None.
      • xml_standalone: True ('yes') or False ('no') if the 'standalone' attribute
        was explicitly declared in the XML declaration; None if omitted or no declaration.
    """
    has_decl = detect_xml_declaration(raw_text)
    if not has_decl:
        return False, None, None, None

    version = tree.docinfo.xml_version
    encoding = tree.docinfo.encoding

    # Separate declaration presence from the standalone attribute's explicit presence/value.
    # Do not search for a bare 'standalone' substring across the text (which could match
    # attribute values like encoding="x-standalone"). Instead, extract the exact standalone
    # attribute assignment from the declaration header: standalone="yes" or standalone="no".
    decl_end = raw_text.find("?>")
    decl_header = raw_text[:decl_end] if decl_end != -1 else ""

    standalone_match = re.search(r'\bstandalone\s*=\s*["\'](yes|no)["\']', decl_header)
    if standalone_match:
        standalone = standalone_match.group(1) == "yes"
    else:
        standalone = None

    return True, version, encoding, standalone


class XmlDocumentSession:
    """
    Central session managing the lifecycle, validation, and metadata
    of the loaded XML document.
    """

    def __init__(self):
        self.file_path: Optional[Path] = None
        self.raw_text_draft: str = ""
        self.last_saved_text: str = ""
        self.dom_root: Optional[etree._Element] = None
        self.is_draft_valid: bool = True
        self.syntax_errors: list[SyntaxErrorInfo] = []

        # Metadata (reflects the state of the canonical dom_root)
        self.has_bom: bool = False
        self.has_xml_declaration: bool = False
        self.xml_version: Optional[str] = None
        self.xml_encoding: Optional[str] = None
        self.xml_standalone: Optional[bool] = None

    @property
    def syntax_error(self) -> Optional[SyntaxErrorInfo]:
        """Backwards-compatible accessor for the primary/first syntax error."""
        return self.syntax_errors[0] if self.syntax_errors else None

    @syntax_error.setter
    def syntax_error(self, value: Optional[SyntaxErrorInfo]):
        """Backwards-compatible setter for syntax_error."""
        if value is None:
            self.syntax_errors = []
        else:
            self.syntax_errors = [value]

    # ------------------------------------------------------------------
    # Loading (Atomic)
    # ------------------------------------------------------------------
    def load_file(self, filepath: str | Path) -> etree._Element:
        """
        Loads an XML file from disk atomically.
        Detects encoding/BOM, parses the DOM, and sets clean baseline state.
        Encoding Contract:
          • Exclusively supports UTF-8 and UTF-8 BOM.
          • Non-UTF-8 bytes (e.g. Windows-1254, ISO-8859-9, UTF-16) raise UnicodeDecodeError.
          • If file reading, decoding, or parsing fails, existing session state is left untouched.
        Returns the root lxml.etree._Element.
        """
        path = Path(filepath)
        with open(path, "rb") as f:
            raw_bytes = f.read()

        # 1. Detect UTF-8 BOM
        new_has_bom = raw_bytes.startswith(codecs.BOM_UTF8)

        # 2. Decode text using utf-8-sig (Contract: UTF-8 / UTF-8 BOM only).
        #    Non-UTF-8 bytes raise UnicodeDecodeError, aborting load before session modification.
        new_raw_text = raw_bytes.decode("utf-8-sig")

        # 3. Parse XML using lxml on decoded UTF-8 text (identical to load_text)
        tree = etree.parse(io.BytesIO(new_raw_text.encode("utf-8")))
        new_dom_root = tree.getroot()

        # 4. Extract declaration presence and metadata
        (
            new_has_decl,
            new_version,
            new_encoding,
            new_standalone,
        ) = extract_document_metadata(tree, new_raw_text)

        # 5. All operations succeeded; atomically apply to session
        self.file_path = path
        self.raw_text_draft = new_raw_text
        self.last_saved_text = new_raw_text
        self.dom_root = new_dom_root
        self.is_draft_valid = True
        self.syntax_error = None
        self.has_bom = new_has_bom
        self.has_xml_declaration = new_has_decl
        self.xml_version = new_version
        self.xml_encoding = new_encoding
        self.xml_standalone = new_standalone

        return self.dom_root

    def load_text(
        self,
        raw_text: str,
        source_path: Optional[str | Path] = None,
        has_bom: bool = False,
    ) -> etree._Element:
        """
        Loads XML from an in-memory string atomically.
        Clean BOM character from string if present, tracking BOM status via flag.
        If parsing fails, existing session state is left untouched.
        Returns the root lxml.etree._Element.
        """
        if raw_text.startswith("\ufeff"):
            new_has_bom = True
            new_raw_text = raw_text[1:]
        else:
            new_has_bom = has_bom
            new_raw_text = raw_text

        tree = etree.parse(io.BytesIO(new_raw_text.encode("utf-8")))
        new_dom_root = tree.getroot()

        (
            new_has_decl,
            new_version,
            new_encoding,
            new_standalone,
        ) = extract_document_metadata(tree, new_raw_text)

        new_path = Path(source_path) if source_path else None

        # All operations succeeded; atomically apply to session
        self.file_path = new_path
        self.raw_text_draft = new_raw_text
        self.last_saved_text = new_raw_text
        self.dom_root = new_dom_root
        self.is_draft_valid = True
        self.syntax_error = None
        self.has_bom = new_has_bom
        self.has_xml_declaration = new_has_decl
        self.xml_version = new_version
        self.xml_encoding = new_encoding
        self.xml_standalone = new_standalone

        return self.dom_root

    # ------------------------------------------------------------------
    # Draft Validation & Update
    # ------------------------------------------------------------------
    def update_text_draft(self, new_text: str) -> bool:
        """
        Updates the raw text draft.
        If valid XML:
          • updates dom_root and associated metadata
          • marks valid, clears syntax_errors, returns True
        If invalid XML:
          • preserves existing dom_root and its metadata unmodified
          • preserves raw_text_draft as typed
          • marks invalid, records syntax_errors, returns False
        """
        self.raw_text_draft = new_text

        # Empty or whitespace text cannot be parsed as valid XML
        if not new_text.strip():
            self.is_draft_valid = False
            self.syntax_errors = [
                SyntaxErrorInfo(
                    line=1,
                    column=1,
                    message="XML content is empty.",
                    original_line=1,
                    original_column=1,
                )
            ]
            return False

        parser = etree.XMLParser()
        try:
            tree = etree.parse(io.BytesIO(new_text.encode("utf-8")), parser)
            new_root = tree.getroot()
            self.dom_root = new_root
            self.is_draft_valid = True
            self.syntax_errors = []

            # Update XML declaration presence and metadata for valid DOM
            (
                self.has_xml_declaration,
                self.xml_version,
                self.xml_encoding,
                self.xml_standalone,
            ) = extract_document_metadata(tree, new_text)

            return True
        except etree.XMLSyntaxError as e:
            self.is_draft_valid = False
            lines = new_text.splitlines()
            errors: list[SyntaxErrorInfo] = []
            seen_keys: set[tuple[int, str]] = set()

            entries = list(parser.error_log) if parser.error_log else list(getattr(e, "error_log", []))
            if not entries:
                orig_line = getattr(e, "lineno", 1) or 1
                orig_col = getattr(e, "offset", 1) or 1
                raw_msg = getattr(e, "msg", str(e))
                clean_msg = clean_error_message(raw_msg)
                adj_line = adjust_syntax_error_line(lines, orig_line, orig_col, raw_msg)
                errors.append(
                    SyntaxErrorInfo(
                        line=adj_line,
                        column=orig_col,
                        message=clean_msg,
                        original_line=orig_line,
                        original_column=orig_col,
                    )
                )
            else:
                for entry in entries:
                    orig_line = entry.line or 1
                    orig_col = entry.column or 1
                    raw_msg = entry.message or ""
                    clean_msg = clean_error_message(raw_msg)
                    adj_line = adjust_syntax_error_line(lines, orig_line, orig_col, raw_msg)
                    key = (adj_line, clean_msg)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        errors.append(
                            SyntaxErrorInfo(
                                line=adj_line,
                                column=orig_col,
                                message=clean_msg,
                                original_line=orig_line,
                                original_column=orig_col,
                            )
                        )
            self.syntax_errors = errors
            return False
        except Exception as e:
            self.is_draft_valid = False
            self.syntax_errors = [
                SyntaxErrorInfo(
                    line=1,
                    column=1,
                    message=clean_error_message(str(e)),
                    original_line=1,
                    original_column=1,
                )
            ]
            return False

    # ------------------------------------------------------------------
    # State Inspection
    # ------------------------------------------------------------------
    def is_dirty(self) -> bool:
        """
        Returns True if the document has unsaved modifications.
        Priority Rules:
          1. Invalid draft -> always dirty (unsaved work in progress)
          2. Exact text equality to last_saved_text -> clean (whether via undo or manual typing)
          3. Different text -> dirty
        """
        if not self.is_draft_valid:
            return True
        if self.raw_text_draft == self.last_saved_text:
            return False
        return True

    def get_root(self) -> Optional[etree._Element]:
        """Returns the canonical lxml.etree._Element root."""
        return self.dom_root

    def get_tree(self) -> Optional[etree._ElementTree]:
        """Returns the underlying lxml.etree._ElementTree if root exists."""
        if self.dom_root is not None:
            return self.dom_root.getroottree()
        return None

    def save_file(self, target_path: Optional[str | Path] = None) -> Path:
        """
        Saves current XML text draft atomically to target_path or self.file_path.
        Validates draft validity, BOM, and XML declaration encoding before saving.
        On success, atomically writes file, updates last_saved_text and file_path,
        and returns the resolved destination Path.
        On failure, preserves all session state and raises the exception.
        """
        if not self.is_draft_valid or self.dom_root is None:
            raise ValueError("Cannot save an invalid or empty XML draft.")

        dest = target_path if target_path is not None else self.file_path
        if not dest:
            raise ValueError("No file path specified for saving.")

        dest_path = Path(dest).resolve()

        # Validate encoding declaration consistency if XML declaration is present
        if self.has_xml_declaration and self.xml_encoding:
            enc_clean = self.xml_encoding.strip().lower()
            if enc_clean not in ("utf-8", "utf8", "utf_8"):
                raise ValueError(
                    f"Unsupported XML declaration encoding: '{self.xml_encoding}'. "
                    "Only UTF-8 and UTF-8 BOM encodings are supported."
                )

        # Atomic write
        save_xml_atomic(dest_path, self.raw_text_draft, has_bom=self.has_bom)

        # Update saved baseline state
        self.mark_as_saved(dest_path)
        return dest_path

    def mark_as_saved(self, target_path: Optional[str | Path] = None):
        """
        Marks current raw text draft as the saved baseline and optionally updates path.
        Raises ValueError if current draft is invalid or session has no loaded document.
        Does NOT perform file writing (saving file I/O belongs to Stage 3).
        """
        if not self.is_draft_valid or self.dom_root is None:
            raise ValueError("Cannot mark an invalid or empty XML draft as saved baseline.")
        self.last_saved_text = self.raw_text_draft
        if target_path:
            self.file_path = Path(target_path)

    def reset(self):
        """Resets the session to initial unloaded state."""
        self.file_path = None
        self.raw_text_draft = ""
        self.last_saved_text = ""
        self.dom_root = None
        self.is_draft_valid = True
        self.syntax_error = None
        self.has_bom = False
        self.has_xml_declaration = False
        self.xml_version = None
        self.xml_encoding = None
        self.xml_standalone = None
