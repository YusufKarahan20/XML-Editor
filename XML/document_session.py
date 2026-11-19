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
import hashlib
import io
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from lxml import etree

from XML.history_manager import (
    HistoryManager,
    DocumentLock,
    SaveAsTransactionLock,
    get_document_id,
    HistoryLockError,
    HistoryCorruptionError,
    HistoryTransactionConflictError,
)
from XML.history_diff import compare_xml_documents
from XML.history_models import DocumentMetadata, ChangeRecord, ChangeSet, PendingJournalRecord

logger = logging.getLogger(__name__)


class HistoryIntegrityError(Exception):
    """Raised when document history metadata and physical content have diverged or cannot be reconciled."""
    pass


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

_NCNAME_START_CHAR = (
    r'[A-Za-z_]'
    r'|[\u00C0-\u00D6]|[\u00D8-\u00F6]|[\u00F8-\u02FF]'
    r'|[\u0370-\u037D]|[\u037F-\u1FFF]'
    r'|[\u200C-\u200D]|[\u2070-\u218F]|[\u2C00-\u2FEF]'
    r'|[\u3001-\uD7FF]|[\uF900-\uFDCF]|[\uFDF0-\uFFFD]'
    r'|[\U00010000-\U000EFFFF]'
)
_NCNAME_CHAR = (
    _NCNAME_START_CHAR
    + r'|[\-0-9\.]|\u00B7|[\u0300-\u036F]|[\u203F-\u2040]'
)
_NCNAME_REGEX = re.compile(rf'^(?:{_NCNAME_START_CHAR})(?:{_NCNAME_CHAR})*$', re.UNICODE)


def is_valid_ncname(name: str) -> bool:
    """
    Validates whether name conforms to W3C XML 1.0 NCName specification.
    Rejects names with colons, spaces, leading digits, or invalid characters.
    Supports valid Unicode characters (e.g. Turkish, Cyrillic, etc.).
    """
    if not isinstance(name, str) or not name or ":" in name or " " in name:
        return False
    if not _NCNAME_REGEX.match(name):
        return False
    try:
        etree.QName(name)
        return True
    except (ValueError, TypeError):
        return False


class XmlDocumentSession:
    """
    Central session managing the lifecycle, validation, and metadata
    of the loaded XML document.
    """

    def __init__(self, history_manager: Optional[HistoryManager] = None):
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

        # History Core Integration (Stage 2.1-B)
        self.history_manager: HistoryManager = (
            history_manager if history_manager is not None else HistoryManager()
        )
        self.last_recovery_results: list[dict[str, Any]] = []

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

        # 6. History Core Recovery (Stage 2.1-B)
        # Scan and finalize any pending journal records or Save As manifests.
        # History errors or warnings MUST NOT block successful XML document loading.
        doc_id = get_document_id(str(self.file_path))
        try:
            self.last_recovery_results = self.history_manager.recover_pending(
                doc_id, current_file_path=str(self.file_path)
            )
        except Exception as rec_err:
            logger.warning("History recovery warning for %s: %s", doc_id, rec_err)
            self.last_recovery_results = [{"status": "ERROR", "error": str(rec_err)}]

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
        Saves current XML text draft atomically to target_path or self.file_path with
        complete History Core Stage 2.1-B transactional integrity.

        Validates draft validity, BOM, and XML declaration encoding before saving.
        Dispatches to either Normal Save or Save As / Initial Save based on destination.
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

        # Determine operation type
        is_save_as = (
            target_path is not None
            and self.file_path is not None
            and dest_path != self.file_path.resolve()
        )
        is_overwrite_existing_from_new = (
            self.file_path is None and dest_path.exists()
        )

        if is_save_as or is_overwrite_existing_from_new:
            return self._execute_save_as_or_initial(dest_path, is_initial_save=(self.file_path is None))
        else:
            return self._execute_normal_save(dest_path)

    def _execute_normal_save(self, dest_path: Path) -> Path:
        """
        Executes Normal Save (same file path) under a single continuous DocumentLock.
        Enforces Origin Baseline, physical BOM byte hashing, causal gap defense,
        and transaction isolation.
        """
        # Rule 1.1: If unchanged and file exists on disk, no-op return
        if not self.is_dirty() and dest_path.exists():
            return dest_path

        doc_id = get_document_id(str(dest_path))
        doc_dir = self.history_manager.get_document_directory(doc_id)

        # Rule 2.3: Entire Normal Save runs under a single continuous DocumentLock
        with DocumentLock(doc_dir):
            # 1. Recovery
            rec_results = self.history_manager.recover_pending(doc_id, current_file_path=str(dest_path))
            self.last_recovery_results = rec_results
            for res in rec_results:
                status = res.get("status")
                if status in ("CONFLICT", "TRANSACTION_ACTIVE_LOCKED", "QUARANTINED_CORRUPT"):
                    raise HistoryIntegrityError(
                        f"Normal save blocked by history conflict or lock: {status} ({res.get('summary', '')})"
                    )

            # 2. Metadata inspection and causal gap detection (Rule 2.4 & 2.5)
            meta = self.history_manager.load_metadata(doc_id)
            history_path = os.path.join(doc_dir, "history.jsonl")
            has_history_records = os.path.exists(history_path) and os.path.getsize(history_path) > 0
            dest_exists = dest_path.exists()

            if meta is None and has_history_records:
                # Metadata missing but history records exist -> reconstruct
                meta = self._reconstruct_metadata_locked(doc_id, str(dest_path), history_path)

            if meta is not None and dest_exists:
                # Verify physical disk hash vs metadata last_saved_content_sha256
                disk_bytes = dest_path.read_bytes()
                disk_hash = hashlib.sha256(disk_bytes).hexdigest()
                if meta.last_saved_content_sha256 and disk_hash != meta.last_saved_content_sha256:
                    raise HistoryIntegrityError(
                        f"Physical XML disk hash ({disk_hash[:8]}...) diverges from history metadata "
                        f"({meta.last_saved_content_sha256[:8]}...). History chain is broken."
                    )

            # 3. Prepare bytes and hash (Rule 2.2)
            raw_text_bytes = self.raw_text_draft.encode("utf-8")
            expected_bytes = (codecs.BOM_UTF8 + raw_text_bytes) if self.has_bom else raw_text_bytes

            # Check if this is a brand-new file on disk with no prior history (Origin Baseline)
            if not dest_exists and (meta is None or not has_history_records):
                # Rule 2.1: Initial Save - Origin Baseline:
                # Do NOT create synthetic ELEMENT_INSERT ChangeSet.
                # Write XML atomically, initialize metadata with initial hash, mark as saved.
                save_xml_atomic(dest_path, self.raw_text_draft, has_bom=self.has_bom)
                now_iso = datetime.now(timezone.utc).isoformat()
                init_meta = DocumentMetadata(
                    doc_id=doc_id,
                    original_filename=dest_path.name,
                    last_known_path=str(dest_path),
                    created_at=now_iso,
                    last_saved_at=now_iso,
                    active_epoch=1,
                    last_saved_content_sha256=hashlib.sha256(expected_bytes).hexdigest(),
                )
                self.history_manager.save_metadata(init_meta)
                self.mark_as_saved(dest_path)
                return dest_path

            # If file already exists or history exists, calculate delta
            old_text = self.last_saved_text or ""
            if not old_text and dest_exists:
                try:
                    old_text = dest_path.read_text(encoding="utf-8-sig")
                except Exception:
                    old_text = ""

            old_raw_bytes = old_text.encode("utf-8")
            previous_bytes = (codecs.BOM_UTF8 + old_raw_bytes) if self.has_bom else old_raw_bytes

            changes = compare_xml_documents(old_text, self.raw_text_draft, doc_id=doc_id)

            prec = self.history_manager.prepare_pending(
                doc_id=doc_id,
                target_path=str(dest_path),
                previous_content=previous_bytes,
                expected_content=expected_bytes,
                changes=changes,
            )

            # 4. Atomic XML write (under continuous DocumentLock!)
            try:
                save_xml_atomic(dest_path, self.raw_text_draft, has_bom=self.has_bom)
            except Exception as write_err:
                try:
                    self.history_manager.abort_save(doc_id, prec.save_id)
                except Exception as abort_err:
                    logger.warning("Failed to abort pending save %s: %s", prec.save_id, abort_err)
                raise write_err

            # 5. Commit pending journal
            try:
                self.history_manager.commit_pending(
                    doc_id=doc_id,
                    save_id=prec.save_id,
                    expected_target_path=str(dest_path)
                )
            except Exception as commit_err:
                # XML is safe on disk; log warning, pending journal preserved for recovery
                logger.warning("History commit deferred for save_id %s: %s", prec.save_id, commit_err)

            self.mark_as_saved(dest_path)
            return dest_path

    def _reconstruct_metadata_locked(self, doc_id: str, target_path: str, history_path: str) -> DocumentMetadata:
        """
        Reconstructs missing DocumentMetadata from verified history.jsonl records.
        """
        last_sha = ""
        try:
            with open(history_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        record = json.loads(line_s)
                        if record.get("expected_target_sha256"):
                            last_sha = record["expected_target_sha256"]
                    except Exception:
                        continue
        except Exception as e:
            raise HistoryIntegrityError(f"Cannot reconstruct metadata; history journal read failed: {e}")

        now_iso = datetime.now(timezone.utc).isoformat()
        meta = DocumentMetadata(
            doc_id=doc_id,
            original_filename=Path(target_path).name,
            last_known_path=target_path,
            created_at=now_iso,
            last_saved_at=now_iso,
            active_epoch=1,
            last_saved_content_sha256=last_sha,
        )
        self.history_manager.save_metadata(meta)
        return meta

    def _execute_save_as_or_initial(self, dest_path: Path, is_initial_save: bool = False) -> Path:
        """
        Executes Save As or Initial Save using SaveAsTransactionLock and prepare_save_as_transaction.
        """
        target_doc_id = get_document_id(str(dest_path))

        if is_initial_save:
            source_doc_id = "__new_document__"
            source_path = ""
        else:
            source_doc_id = get_document_id(str(self.file_path)) if self.file_path else "__new_document__"
            source_path = str(self.file_path) if self.file_path else ""

        # Inspect target on disk
        dest_exists = dest_path.exists()
        previous_bytes: Optional[bytes] = None
        previous_text: str = ""
        if dest_exists:
            previous_bytes = dest_path.read_bytes()
            previous_text = previous_bytes.decode("utf-8-sig")

        # Hierarchical Locking: SaveAsTransactionLock -> DocumentLock
        with self.history_manager.save_as_transaction_lock(target_doc_id):
            if dest_exists and previous_text:
                changes = compare_xml_documents(previous_text, self.raw_text_draft, doc_id=target_doc_id)
            else:
                changes = []

            manifest, prec = self.history_manager.prepare_save_as_transaction(
                source_doc_id=source_doc_id,
                target_doc_id=target_doc_id,
                target_path=str(dest_path),
                source_path=source_path,
                previous_target_exists=dest_exists,
                previous_content=previous_bytes,
                expected_content=self.raw_text_draft,
                changes=changes,
                has_bom=self.has_bom,
            )

            try:
                save_xml_atomic(dest_path, self.raw_text_draft, has_bom=self.has_bom)
            except Exception as write_err:
                try:
                    self.history_manager.abort_save_as_transaction(target_doc_id, manifest.save_id)
                except Exception as abort_err:
                    logger.warning("Failed to abort save as transaction %s: %s", manifest.save_id, abort_err)
                raise write_err

            try:
                self.history_manager.commit_save_as_transaction(
                    target_doc_id=target_doc_id,
                    save_id=manifest.save_id,
                    expected_target_path=str(dest_path)
                )
            except Exception as commit_err:
                logger.warning("Save As commit deferred for %s: %s", manifest.save_id, commit_err)

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



    def dry_run_text_update(self, element: etree._Element, new_text: str) -> tuple[bool, str]:
        """
        Validates whether setting element.text to new_text produces serializable, valid XML.
        Does not mutate session draft, dirty state, or metadata.
        Preserves original element.text (including None) exactly in a finally block.
        Rejects XML 1.0 invalid control characters.
        """
        if self.dom_root is None:
            return False, "Açık bir XML belgesi bulunmuyor."
        if not self.is_draft_valid:
            return False, "Geçersiz Text View taslağı varken düzenleme yapılamaz."
        if element is None:
            return False, "Geçersiz element düğümü."

        # Validate XML 1.0 disallowed characters (C0 controls except \t, \n, \r)
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', new_text):
            return False, "XML 1.0 tarafından izin verilmeyen kontrol karakterleri içeriyor."

        old_text = element.text
        try:
            element.text = new_text
            tree = self.dom_root.getroottree()
            raw_bytes = etree.tostring(
                tree,
                encoding="utf-8",
                xml_declaration=self.has_xml_declaration,
                standalone=self.xml_standalone,
                pretty_print=False,
            )
            # Verify parsed tree to guarantee well-formedness
            verify_tree = etree.parse(io.BytesIO(raw_bytes))
            if verify_tree.getroot() is None:
                return False, "Geçersiz XML kök düğümü."
            return True, ""
        except Exception as e:
            return False, f"XML serileştirme hatası: {e}"
        finally:
            element.text = old_text

    def dry_run_rename_element(self, element: etree._Element, new_local_name: str) -> tuple[bool, str]:
        """
        Validates whether renaming element's local name to new_local_name produces serializable, valid XML.
        Does not mutate session draft, dirty state, or metadata.
        Preserves original element.tag exactly in a finally block.
        """
        if self.dom_root is None:
            return False, "Açık bir XML belgesi bulunmuyor."
        if not self.is_draft_valid:
            return False, "Geçersiz Text View taslağı varken düzenleme yapılamaz."
        if element is None:
            return False, "Geçersiz element düğümü."

        if not is_valid_ncname(new_local_name):
            return False, "Geçersiz XML element adı. İki nokta (:) içeremez ve XML 1.0 NCName kurallarına uygun olmalıdır."

        old_tag = element.tag
        if old_tag.startswith("{"):
            uri = old_tag[1:].split("}", 1)[0]
            target_tag = f"{{{uri}}}{new_local_name}"
        else:
            target_tag = new_local_name

        try:
            element.tag = target_tag
            tree = self.dom_root.getroottree()
            raw_bytes = etree.tostring(
                tree,
                encoding="utf-8",
                xml_declaration=self.has_xml_declaration,
                standalone=self.xml_standalone,
                pretty_print=False,
            )
            verify_tree = etree.parse(io.BytesIO(raw_bytes))
            if verify_tree.getroot() is None:
                return False, "Geçersiz XML kök düğümü."
            return True, ""
        except Exception as e:
            return False, f"XML serileştirme hatası: {e}"
        finally:
            element.tag = old_tag

    def dry_run_delete_element(self, element: etree._Element) -> tuple[bool, str]:
        """
        Validates whether deleting element produces serializable, valid XML.
        Prohibits deletion of root element.
        Does not mutate session draft, dirty state, or metadata.
        Restores element, its tail, and preceding tail in a finally block.
        """
        if self.dom_root is None:
            return False, "Açık bir XML belgesi bulunmuyor."
        if not self.is_draft_valid:
            return False, "Geçersiz Text View taslağı varken düzenleme yapılamaz."
        if element is None:
            return False, "Geçersiz element düğümü."
        if element.getparent() is None:
            return False, "Kök element silinemez."

        parent = element.getparent()
        index = parent.index(element)
        element_tail = element.tail
        prev_sibling = element.getprevious()
        orig_prev_tail = prev_sibling.tail if prev_sibling is not None else parent.text

        try:
            if prev_sibling is not None:
                prev_sibling.tail = (orig_prev_tail or "") + (element_tail or "")
            else:
                parent.text = (orig_prev_tail or "") + (element_tail or "")
            parent.remove(element)

            tree = self.dom_root.getroottree()
            raw_bytes = etree.tostring(
                tree,
                encoding="utf-8",
                xml_declaration=self.has_xml_declaration,
                standalone=self.xml_standalone,
                pretty_print=False,
            )
            verify_tree = etree.parse(io.BytesIO(raw_bytes))
            if verify_tree.getroot() is None:
                return False, "Geçersiz XML kök düğümü."
            return True, ""
        except Exception as e:
            return False, f"XML serileştirme hatası: {e}"
        finally:
            parent.insert(index, element)
            element.tail = element_tail
            if prev_sibling is not None:
                prev_sibling.tail = orig_prev_tail
            else:
                parent.text = orig_prev_tail

    def update_from_dom(self) -> bool:
        """
        Serializes the canonical dom_root tree to raw_text_draft using pretty_print=False.
        Preserves XML declaration and standalone metadata according to session contract.
        Verifies serialized output before updating raw_text_draft.
        Leaves raw_text_draft, dirty state, and session fields intact on failure.
        Returns True on success, False on failure.
        """
        if self.dom_root is None or not self.is_draft_valid:
            return False

        try:
            tree = self.dom_root.getroottree()
            raw_bytes = etree.tostring(
                tree,
                encoding="utf-8",
                xml_declaration=self.has_xml_declaration,
                standalone=self.xml_standalone,
                pretty_print=False,
            )
            # Verify parsed tree to guarantee well-formedness
            verify_tree = etree.parse(io.BytesIO(raw_bytes))
            if verify_tree.getroot() is None:
                return False

            new_text = raw_bytes.decode("utf-8")
            new_text = new_text.replace("\r\n", "\n").replace("\r", "\n")

            self.raw_text_draft = new_text
            self.is_draft_valid = True
            self.syntax_errors = []
            return True
        except Exception:
            return False

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
        self.last_recovery_results = []

    def get_history_audit(self) -> Optional[Dict[str, Any]]:
        """
        Returns read-only history audit data for the currently loaded document.
        Returns None if no document is loaded or document has no file_path.
        """
        if not self.file_path:
            return None
        doc_id = get_document_id(str(self.file_path))
        return self.history_manager.get_history_audit(doc_id)

    def delete_history_changeset(self, save_id: str) -> bool:
        """Deletes a specific history record by save_id under DocumentLock."""
        if not self.file_path:
            return False
        doc_id = get_document_id(str(self.file_path))
        return self.history_manager.delete_changeset(doc_id, save_id)

    def clear_all_history(self) -> bool:
        """Completely clears all history records and re-anchors to current file baseline."""
        if not self.file_path:
            return False
        doc_id = get_document_id(str(self.file_path))
        return self.history_manager.clear_all_history(doc_id, current_file_path=str(self.file_path))
