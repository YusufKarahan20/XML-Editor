"""
XML/history_manager.py
----------------------
Persistent storage, journaling, OS-level locking, and crash recovery
engine for XML Change History.

Stores independent, document-based JSON Lines (.jsonl) histories under
%LOCALAPPDATA%/XMLAnalyzer/logs/documents/<doc_id>/.
"""

import os
import sys
import errno
import json
import logging
import gzip
import time
import uuid
import hashlib
import threading
import codecs
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple, Union

from XML.history_models import (
    ChangeRecord,
    ChangeSet,
    PendingJournalRecord,
    DocumentMetadata,
    SaveAsManifest,
)

logger = logging.getLogger(__name__)

# Optional OS-level file locking
try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    msvcrt = None
    HAS_MSVCRT = False


class HistoryLockError(Exception):
    """Raised when an OS-level or thread-level lock cannot be acquired."""
    pass


class HistoryCorruptionError(Exception):
    """Raised when a history or archive file is corrupted."""
    pass


class HistoryTransactionConflictError(Exception):
    """Raised when a transaction conflict or isolation violation is detected."""
    pass


# ---------------------------------------------------------------------------
# Storage Directory & Path Resolution
# ---------------------------------------------------------------------------

def get_default_storage_root() -> str:
    """
    Resolves the base storage root directory:
    %LOCALAPPDATA%/XMLAnalyzer/logs/documents/ on Windows.
    Falls back to ~/.xml_analyzer/logs/documents/ if not set.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = os.path.join(local_app_data, "XMLAnalyzer", "logs", "documents")
    else:
        base_dir = os.path.join(os.path.expanduser("~"), ".xml_analyzer", "logs", "documents")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def canonical_path(file_path: str) -> str:
    """
    Returns canonical, normalized, case-folded absolute path
    compatible with Windows case-insensitive filesystem semantics.
    """
    if not file_path:
        return ""
    return os.path.normcase(os.path.realpath(os.path.abspath(file_path)))


def get_document_id(file_path: str) -> str:
    """
    Computes a deterministic, collision-resistant document ID:
    slugify(filename) + '_' + sha256(canonical_path)[:12]
    Example: orders_a81f4c29d01e
    """
    norm_path = canonical_path(file_path)
    filename = os.path.basename(norm_path)
    base_name = os.path.splitext(filename)[0]
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in base_name)
    if not slug:
        slug = "document"

    path_hash = hashlib.sha256(norm_path.encode("utf-8")).hexdigest()[:12]
    return f"{slug}_{path_hash}"


# ---------------------------------------------------------------------------
# Reentrant OS-Level & Thread-Level Lock
# ---------------------------------------------------------------------------

_DIR_LOCKS: Dict[str, Dict[str, Any]] = {}
_DIR_LOCKS_MUTEX = threading.Lock()


class DocumentLock:
    """
    Reentrant context manager providing both thread-level and OS-level locking.
    Uses msvcrt.locking on Windows to prevent concurrent process collisions.
    Maintains reentrant nesting counters so nested lock calls in the same
    thread reuse the active OS file lock without permission errors.
    Automatically releases locks on exit or process crash.
    """

    def __init__(self, doc_dir: str, timeout: float = 0.5, retry_interval: float = 0.05):
        self.doc_dir = os.path.normcase(os.path.abspath(doc_dir))
        self.lock_path = os.path.join(self.doc_dir, ".history.lock")
        self.timeout = timeout
        self.retry_interval = retry_interval

        with _DIR_LOCKS_MUTEX:
            if self.doc_dir not in _DIR_LOCKS:
                _DIR_LOCKS[self.doc_dir] = {
                    "thread_lock": threading.RLock(),
                    "count": 0,
                    "file_handle": None,
                }
            self._state = _DIR_LOCKS[self.doc_dir]

    def __enter__(self):
        # 1. Acquire thread-level lock
        acquired = self._state["thread_lock"].acquire(timeout=self.timeout)
        if not acquired:
            raise HistoryLockError(f"Thread lock timeout on {self.doc_dir}")

        # 2. If already holding the OS lock in this thread/process, increment depth
        if self._state["count"] > 0 and self._state["file_handle"] is not None:
            self._state["count"] += 1
            return self

        # 3. First-level entry: acquire OS-level file lock
        try:
            os.makedirs(self.doc_dir, exist_ok=True)
            start_time = time.time()
            while True:
                fh = None
                try:
                    fh = open(self.lock_path, "a+b")
                    if HAS_MSVCRT and msvcrt:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    self._state["file_handle"] = fh
                    self._state["count"] = 1
                    return self
                except (OSError, IOError) as e:
                    if fh:
                        try:
                            fh.close()
                        except Exception:
                            pass

                    if time.time() - start_time >= self.timeout:
                        self._state["thread_lock"].release()
                        raise HistoryLockError(f"OS-level lock timeout on {self.lock_path}: {e}")
                    time.sleep(self.retry_interval)
        except Exception:
            if self._state["count"] == 0:
                try:
                    self._state["thread_lock"].release()
                except RuntimeError:
                    pass
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._state["count"] -= 1
            if self._state["count"] <= 0:
                self._state["count"] = 0
                fh = self._state["file_handle"]
                self._state["file_handle"] = None
                if fh:
                    if HAS_MSVCRT and msvcrt:
                        try:
                            fh.seek(0)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                    try:
                        fh.close()
                    except Exception:
                        pass
        finally:
            self._state["thread_lock"].release()


# ---------------------------------------------------------------------------
# Save As Transaction Lock (OS-Level Lifecycle Lock)
# ---------------------------------------------------------------------------

_TX_LOCKS: Dict[str, Dict[str, Any]] = {}
_TX_LOCKS_MUTEX = threading.Lock()


class SaveAsTransactionLock:
    """
    OS-level transaction lock held across the entire Two-Phase Save As transaction lifecycle:
    prepare -> XML atomic write -> commit / abort.

    Uses msvcrt.locking on Windows to prevent concurrent processes from preparing or aborting
    an in-flight Save As transaction. Automatically released by the OS kernel if the process
    dies or crashes.
    """

    def __init__(self, doc_dir: str, timeout: float = 0.5, retry_interval: float = 0.05):
        self.doc_dir = os.path.normcase(os.path.abspath(doc_dir))
        self.lock_path = os.path.join(self.doc_dir, ".save_as_transaction.lock")
        self.timeout = timeout
        self.retry_interval = retry_interval

        with _TX_LOCKS_MUTEX:
            if self.doc_dir not in _TX_LOCKS:
                _TX_LOCKS[self.doc_dir] = {
                    "thread_lock": threading.RLock(),
                    "count": 0,
                    "file_handle": None,
                    "owner_thread": None,
                }
            self._state = _TX_LOCKS[self.doc_dir]

    def __enter__(self):
        # 1. Acquire thread-level lock
        acquired = self._state["thread_lock"].acquire(timeout=self.timeout)
        if not acquired:
            raise HistoryLockError(f"Save As transaction lock timeout (thread) on {self.doc_dir}")

        # 2. Reentrancy: if already holding in this thread, increment depth
        if self._state["count"] > 0 and self._state["owner_thread"] == threading.get_ident():
            self._state["count"] += 1
            return self

        # 3. Acquire OS-level file lock
        try:
            os.makedirs(self.doc_dir, exist_ok=True)
            start_time = time.time()
            while True:
                fh = None
                try:
                    fh = open(self.lock_path, "a+b")
                    if HAS_MSVCRT and msvcrt:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    self._state["file_handle"] = fh
                    self._state["count"] = 1
                    self._state["owner_thread"] = threading.get_ident()
                    return self
                except (OSError, IOError) as e:
                    if fh:
                        try:
                            fh.close()
                        except Exception:
                            pass

                    if time.time() - start_time >= self.timeout:
                        self._state["thread_lock"].release()
                        raise HistoryLockError(f"Save As transaction lock timeout (OS) on {self.lock_path}: {e}")
                    time.sleep(self.retry_interval)
        except Exception:
            if self._state["count"] == 0:
                try:
                    self._state["thread_lock"].release()
                except RuntimeError:
                    pass
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._state["count"] -= 1
            if self._state["count"] <= 0:
                self._state["count"] = 0
                self._state["owner_thread"] = None
                fh = self._state["file_handle"]
                self._state["file_handle"] = None
                if fh:
                    if HAS_MSVCRT and msvcrt:
                        try:
                            fh.seek(0)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                    try:
                        fh.close()
                    except Exception:
                        pass
        finally:
            self._state["thread_lock"].release()


def is_save_as_transaction_locked_by_other(doc_dir: str) -> bool:
    """
    Returns True if another thread or process is currently holding the Save As transaction lock
    on doc_dir. Returns False if the lock is held by the current thread or is completely free.
    """
    norm_dir = os.path.normcase(os.path.abspath(doc_dir))
    with _TX_LOCKS_MUTEX:
        if norm_dir in _TX_LOCKS:
            st = _TX_LOCKS[norm_dir]
            if st["count"] > 0:
                if st["owner_thread"] == threading.get_ident():
                    return False
                else:
                    return True

    lock_path = os.path.join(norm_dir, ".save_as_transaction.lock")
    if not os.path.exists(lock_path):
        return False

    if not (HAS_MSVCRT and msvcrt):
        return False

    probe_fh = None
    try:
        probe_fh = open(lock_path, "a+b")
        probe_fh.seek(0)
        msvcrt.locking(probe_fh.fileno(), msvcrt.LK_NBLCK, 1)
        # Succeeded: not locked by another process! Release immediately.
        msvcrt.locking(probe_fh.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    except (OSError, IOError):
        # Locked by another process!
        return True
    finally:
        if probe_fh:
            try:
                probe_fh.close()
            except Exception:
                pass


def _canonical_changeset_payload(cs: Any) -> str:
    """
    Serializes a ChangeSet or dict to a normalized, canonical JSON string
    with sorted keys for exact structural and content comparison.
    """
    if hasattr(cs, "to_dict"):
        raw = cs.to_dict()
    elif isinstance(cs, dict):
        raw = dict(cs)
    else:
        raw = {}
    return json.dumps(raw, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# History Manager Core
# ---------------------------------------------------------------------------

class HistoryManager:
    """
    Manages disk storage, pending journals, commit operations, crash recovery,
    Save As epoch branching, and manifest-backed Gzip rotation.
    """

    def __init__(self, storage_root: Optional[str] = None, rotation_threshold_bytes: int = 10 * 1024 * 1024):
        self.storage_root = storage_root or get_default_storage_root()
        self.rotation_threshold_bytes = rotation_threshold_bytes

    def get_document_directory(self, doc_id: str) -> str:
        doc_dir = os.path.join(self.storage_root, doc_id)
        os.makedirs(doc_dir, exist_ok=True)
        return doc_dir

    def save_as_transaction_lock(
        self,
        target_doc_id: str,
        timeout: float = 0.5,
        retry_interval: float = 0.05
    ) -> SaveAsTransactionLock:
        """
        Acquires an OS-level transaction lock on the target document directory.
        Must be acquired before prepare_save_as_transaction and held across XML write
        until commit or abort completes. Automatically released on crash.
        """
        doc_dir = self.get_document_directory(target_doc_id)
        return SaveAsTransactionLock(doc_dir, timeout=timeout, retry_interval=retry_interval)

    # -----------------------------------------------------------------------
    # Metadata Management
    # -----------------------------------------------------------------------

    def get_metadata_path(self, doc_id: str) -> str:
        return os.path.join(self.get_document_directory(doc_id), "metadata.json")

    def load_metadata(self, doc_id: str) -> Optional[DocumentMetadata]:
        meta_path = self.get_metadata_path(doc_id)
        if not os.path.exists(meta_path):
            return None
        with DocumentLock(self.get_document_directory(doc_id)):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return DocumentMetadata.from_dict(data)
            except Exception:
                return None

    def save_metadata(self, metadata: DocumentMetadata) -> None:
        doc_dir = self.get_document_directory(metadata.doc_id)
        meta_path = self.get_metadata_path(metadata.doc_id)
        tmp_path = meta_path + ".tmp"

        with DocumentLock(doc_dir):
            data = metadata.to_dict()
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, meta_path)

    # -----------------------------------------------------------------------
    # Pending Journal Operations
    # -----------------------------------------------------------------------

    def prepare_pending(
        self,
        doc_id: str,
        target_path: str,
        previous_content: str,
        expected_content: str,
        changes: List[ChangeRecord],
        target_epoch_id: Optional[str] = None,
        author: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> PendingJournalRecord:
        """
        Creates an atomic pending journal file (.pending_<save_id>.json) on disk
        prior to physical XML file write.
        Protected by DocumentLock. Checks for active Save As transaction isolation.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            # Isolation check: if active Save As manifest exists in doc_dir, resolve or block
            manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
            if os.path.exists(manifest_path):
                res = self._recover_save_as_locked(doc_id, target_path)
                if res.get("status") not in ("COMMITTED", "ABORTED", "NO_MANIFEST"):
                    raise HistoryLockError(f"Document {doc_id} is reserved by an active or conflicting Save As transaction.")
            return self._prepare_pending_locked(
                doc_id=doc_id,
                target_path=target_path,
                previous_content=previous_content,
                expected_content=expected_content,
                changes=changes,
                target_epoch_id=target_epoch_id,
                author=author,
                summary=summary,
            )

    def _prepare_pending_locked(
        self,
        doc_id: str,
        target_path: str,
        previous_content: str,
        expected_content: str,
        changes: List[ChangeRecord],
        target_epoch_id: Optional[str] = None,
        author: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> PendingJournalRecord:
        doc_dir = self.get_document_directory(doc_id)
        save_id = uuid.uuid4().hex
        now_utc = datetime.now(timezone.utc).isoformat()

        prev_hash = hashlib.sha256(
            previous_content.encode("utf-8") if isinstance(previous_content, str) else previous_content
        ).hexdigest()

        expected_hash = hashlib.sha256(
            expected_content.encode("utf-8") if isinstance(expected_content, str) else expected_content
        ).hexdigest()

        changeset = ChangeSet(
            save_id=save_id,
            doc_id=doc_id,
            target_path=os.path.abspath(target_path),
            timestamp=now_utc,
            previous_content_sha256=prev_hash,
            expected_target_sha256=expected_hash,
            changes=changes,
            target_epoch_id=target_epoch_id,
            summary=summary,
            author=author,
        )

        record = PendingJournalRecord(
            save_id=save_id,
            doc_id=doc_id,
            target_path=os.path.abspath(target_path),
            timestamp=now_utc,
            previous_content_sha256=prev_hash,
            expected_target_sha256=expected_hash,
            changeset=changeset,
            target_epoch_id=target_epoch_id,
            status="PENDING",
        )

        pending_filename = f".pending_{save_id}.json"
        pending_path = os.path.join(doc_dir, pending_filename)
        tmp_path = pending_path + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, pending_path)

        return record

    def _repair_torn_tail(self, history_path: str) -> None:
        """
        If the last line of history.jsonl is a partial/torn JSON line,
        discards it and rewrites only the valid lines.
        """
        if not os.path.exists(history_path) or os.path.getsize(history_path) == 0:
            return
        try:
            with open(history_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                return
            last_line = lines[-1].strip()
            if not last_line:
                return
            try:
                json.loads(last_line)
            except Exception:
                # The last line is invalid JSON; drop it atomically
                valid_lines = lines[:-1]
                tmp_repair = history_path + ".repair.tmp"
                with open(tmp_repair, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(valid_lines)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_repair, history_path)
        except Exception:
            pass

    def commit_pending(
        self,
        doc_id: str,
        save_id: str,
        expected_target_path: Optional[str] = None
    ) -> bool:
        """
        Appends the pending changes to history.jsonl, updates metadata,
        and deletes the pending file. Checks for rotation threshold.
        Idempotent: parses JSONL lines to prevent duplicate ChangeSets.
        Enforces cross-document pending isolation (doc_id and target_path validation).
        Guards against concurrent Save As transactions.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                    if mdata.get("save_id") == save_id:
                        return self._commit_save_as_transaction_locked(doc_id, save_id, expected_target_path)
                    else:
                        res = self._recover_save_as_locked(doc_id)
                        if res.get("status") not in ("COMMITTED", "ABORTED", "NO_MANIFEST"):
                            return False
                except Exception:
                    return False
            return self._commit_pending_locked(doc_id, save_id, expected_target_path)

    def _commit_pending_locked(
        self,
        doc_id: str,
        save_id: str,
        expected_target_path: Optional[str] = None
    ) -> bool:
        doc_dir = self.get_document_directory(doc_id)
        pending_path = os.path.join(doc_dir, f".pending_{save_id}.json")
        history_path = os.path.join(doc_dir, "history.jsonl")

        # 1. Clean any torn tail from an interrupted prior write
        self._repair_torn_tail(history_path)

        # 2. Check if this save_id is already committed in history.jsonl via exact JSON parsing
        already_committed = False
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            entry = json.loads(line_str)
                            if entry.get("save_id") == save_id:
                                already_committed = True
                                break
                        except Exception:
                            continue
            except Exception:
                already_committed = False

        if already_committed:
            # Already written to JSONL; clean up pending journal if still present
            if os.path.exists(pending_path):
                try:
                    os.remove(pending_path)
                except OSError:
                    pass
            return True

        # 3. If not already committed, we need the pending journal record
        if not os.path.exists(pending_path):
            return False

        with open(pending_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        record = PendingJournalRecord.from_dict(data)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # 3a. Document Identity Isolation: Verify record.doc_id matches doc_id
        if record.doc_id != doc_id:
            conflict_path = os.path.join(doc_dir, f".conflict_doc_mismatch_{save_id}_{ts}.json")
            try:
                os.replace(pending_path, conflict_path)
            except OSError:
                pass
            return False

        # 3b. Target Path Isolation: If expected_target_path is provided, verify canonical path
        if expected_target_path is not None:
            if canonical_path(record.target_path) != canonical_path(expected_target_path):
                conflict_path = os.path.join(doc_dir, f".conflict_path_mismatch_{save_id}_{ts}.json")
                try:
                    os.replace(pending_path, conflict_path)
                except OSError:
                    pass
                return False

        # 4. Write single atomic line to history.jsonl
        line = json.dumps(record.changeset.to_dict(), ensure_ascii=False) + "\n"
        with open(history_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        # 5. Update metadata
        meta = self.load_metadata(doc_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        if meta is None:
            meta = DocumentMetadata(
                doc_id=doc_id,
                original_filename=os.path.basename(record.target_path),
                last_known_path=record.target_path,
                created_at=now_iso,
                last_saved_at=now_iso,
                last_saved_content_sha256=record.expected_target_sha256,
            )
        else:
            meta.last_saved_at = now_iso
            meta.last_known_path = record.target_path
            meta.last_saved_content_sha256 = record.expected_target_sha256
        self.save_metadata(meta)

        # 6. Remove pending journal file (after JSONL write succeeds)
        pending_removed = False
        if os.path.exists(pending_path):
            try:
                os.remove(pending_path)
                pending_removed = True
            except OSError:
                pending_removed = False
        else:
            pending_removed = True

        # 7. Check rotation threshold ONLY IF pending file was completely removed
        if pending_removed and os.path.exists(history_path) and os.path.getsize(history_path) >= self.rotation_threshold_bytes:
            self._rotate_log_locked(doc_id)

        return True

    def abort_save(self, doc_id: str, save_id: str) -> bool:
        """
        Aborts a pending save, removing the journal file if save failed before XML write.
        Guards against aborting active Save As transactions directly.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                    if mdata.get("save_id") == save_id:
                        res = self._abort_save_as_transaction_locked(doc_id, save_id)
                        return bool(res.get("success", False))
                except Exception:
                    pass
            return self._abort_save_locked(doc_id, save_id)

    def _abort_save_locked(self, doc_id: str, save_id: str) -> bool:
        doc_dir = self.get_document_directory(doc_id)
        pending_path = os.path.join(doc_dir, f".pending_{save_id}.json")
        if os.path.exists(pending_path):
            try:
                os.remove(pending_path)
                return True
            except OSError:
                pass
        return False

    # -----------------------------------------------------------------------
    # Recovery & Quarantine
    # -----------------------------------------------------------------------

    def recover_pending(
        self,
        doc_id: str,
        current_file_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Scans for pending journal files (.pending_*.json) and handles them
        with triple verification.
        Recovery Order:
        1. Save As transaction recovery (_recover_save_as_locked). If CONFLICT, halts immediately.
        2. Rotation manifest recovery (_recover_rotation_locked).
        3. Orphan .pending_*.tmp cleanup.
        4. Normal pending journal recovery.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            return self._recover_pending_locked(doc_id, current_file_path)

    def _recover_pending_locked(
        self,
        doc_id: str,
        current_file_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        doc_dir = self.get_document_directory(doc_id)
        recovery_results: List[Dict[str, Any]] = []

        # 1. Recover Save As transaction FIRST!
        save_as_result = self._recover_save_as_locked(doc_id, current_file_path)
        if save_as_result.get("status") in ("CONFLICT", "TRANSACTION_ACTIVE_LOCKED"):
            recovery_results.append({
                "status": save_as_result.get("status"),
                "error": "Save As transaction in progress or in conflict. Normal recovery halted to protect data integrity.",
                "details": save_as_result,
            })
            return recovery_results

        if save_as_result.get("status") in ("COMMITTED", "ABORTED"):
            recovery_results.append(save_as_result)

        # 2. Recover rotation manifest (if safe)
        self._recover_rotation_locked(doc_id)

        # 3. Clean orphan .tmp journal files from interrupted writes before os.replace
        for f in os.listdir(doc_dir):
            if f.startswith(".pending_") and f.endswith(".tmp"):
                try:
                    os.remove(os.path.join(doc_dir, f))
                except OSError:
                    pass

        pending_files = [f for f in os.listdir(doc_dir) if f.startswith(".pending_") and f.endswith(".json")]
        if not pending_files:
            return recovery_results

        actual_content_hash = None
        target_to_check = current_file_path
        if target_to_check and os.path.exists(target_to_check):
            try:
                with open(target_to_check, "rb") as f:
                    actual_content_hash = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                actual_content_hash = None

        meta = self.load_metadata(doc_id)

        for pf in pending_files:
            p_path = os.path.join(doc_dir, pf)
            try:
                with open(p_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rec = PendingJournalRecord.from_dict(data)
            except Exception as e:
                # Corrupted pending file -> quarantine
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                conflict_path = os.path.join(doc_dir, f".conflict_corrupt_{ts}.json")
                try:
                    os.replace(p_path, conflict_path)
                except Exception:
                    pass
                recovery_results.append({"status": "QUARANTINED_CORRUPT", "error": str(e)})
                continue

            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

            # Epoch isolation defense: If pending belongs to an uncommitted or archived epoch, do not commit to active history
            if rec.target_epoch_id and meta and meta.archived_epochs:
                if any(e.get("epoch_id") == rec.target_epoch_id for e in meta.archived_epochs):
                    conflict_path = os.path.join(doc_dir, f".conflict_archived_epoch_{rec.save_id}_{ts}.json")
                    try:
                        os.replace(p_path, conflict_path)
                    except OSError:
                        pass
                    recovery_results.append({"status": "QUARANTINED_ARCHIVED_EPOCH", "save_id": rec.save_id})
                    continue

            # If no target provided or checking file from record
            file_to_probe = current_file_path or rec.target_path
            file_hash = actual_content_hash
            if file_to_probe != current_file_path and os.path.exists(file_to_probe):
                try:
                    with open(file_to_probe, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                except Exception:
                    file_hash = None

            if file_hash == rec.expected_target_sha256:
                # XML save succeeded! Commit pending history
                self._commit_pending_locked(doc_id, rec.save_id)
                recovery_results.append({
                    "save_id": rec.save_id,
                    "status": "COMMITTED",
                    "summary": "XML file confirmed on disk; pending changes committed."
                })
            elif file_hash == rec.previous_content_sha256:
                # XML save aborted / never happened -> mark aborted
                aborted_path = os.path.join(doc_dir, f".aborted_{rec.save_id}_{ts}.json")
                os.replace(p_path, aborted_path)
                recovery_results.append({
                    "save_id": rec.save_id,
                    "status": "ABORTED",
                    "summary": "XML file remained at previous hash; pending record archived as aborted."
                })
            else:
                # Mismatch -> Quarantine to prevent data loss or history contamination
                conflict_path = os.path.join(doc_dir, f".conflict_{rec.save_id}_{ts}.json")
                os.replace(p_path, conflict_path)
                recovery_results.append({
                    "save_id": rec.save_id,
                    "status": "QUARANTINED",
                    "summary": "Disk hash matched neither previous nor expected; quarantined to prevent corruption."
                })

        return recovery_results

    # -----------------------------------------------------------------------
    # Save As & Epoch Branching (Two-Phase Manifest Architecture)
    # -----------------------------------------------------------------------

    def _save_manifest_atomic(self, doc_dir: str, manifest: SaveAsManifest) -> None:
        """Atomically writes or updates the .save_as_manifest.json on disk."""
        manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
        tmp_path = manifest_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, manifest_path)

    def _quarantine_manifest_locked(self, doc_dir: str, manifest: SaveAsManifest, reason: str) -> None:
        """Atomically quarantines a conflicting manifest and pending file."""
        manifest.state = "CONFLICT"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
        conflict_path = os.path.join(doc_dir, f".conflict_save_as_manifest_{manifest.save_id}_{reason}_{ts}.json")
        try:
            if os.path.exists(manifest_path):
                os.replace(manifest_path, conflict_path)
        except OSError:
            pass
        pending_path = os.path.join(doc_dir, f".pending_{manifest.save_id}.json")
        if os.path.exists(pending_path):
            conflict_pending = os.path.join(doc_dir, f".conflict_save_as_pending_{manifest.save_id}_{reason}_{ts}.json")
            try:
                os.replace(pending_path, conflict_pending)
            except OSError:
                pass

    def _quarantine_corrupt_manifest_locked(self, doc_dir: str) -> None:
        """Quarantines an unparseable/corrupted manifest file."""
        manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        conflict_path = os.path.join(doc_dir, f".conflict_corrupt_save_as_manifest_{ts}.json")
        try:
            if os.path.exists(manifest_path):
                os.replace(manifest_path, conflict_path)
        except OSError:
            pass

    def prepare_save_as_transaction(
        self,
        source_doc_id: str,
        target_doc_id: str,
        target_path: str,
        source_path: str,
        previous_target_exists: bool,
        previous_content: Optional[Union[str, bytes]],
        expected_content: Union[str, bytes],
        changes: List[ChangeRecord],
        target_epoch_id: Optional[str] = None,
        has_bom: bool = False,
        author: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> Tuple[SaveAsManifest, PendingJournalRecord]:
        """
        Phase 1 of Save As Two-Phase Transaction:
        Prepares atomic .save_as_manifest.json and .pending_<save_id>.json on disk.
        CRITICAL: Never alters target's active history.jsonl or metadata prior to XML write.
        Protected by DocumentLock on target directory.
        """
        target_dir = self.get_document_directory(target_doc_id)
        with DocumentLock(target_dir):
            return self._prepare_save_as_transaction_locked(
                source_doc_id=source_doc_id,
                target_doc_id=target_doc_id,
                target_path=target_path,
                source_path=source_path,
                previous_target_exists=previous_target_exists,
                previous_content=previous_content,
                expected_content=expected_content,
                changes=changes,
                target_epoch_id=target_epoch_id,
                has_bom=has_bom,
                author=author,
                summary=summary,
            )

    def _prepare_save_as_transaction_locked(
        self,
        source_doc_id: str,
        target_doc_id: str,
        target_path: str,
        source_path: str,
        previous_target_exists: bool,
        previous_content: Optional[Union[str, bytes]],
        expected_content: Union[str, bytes],
        changes: List[ChangeRecord],
        target_epoch_id: Optional[str] = None,
        has_bom: bool = False,
        author: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> Tuple[SaveAsManifest, PendingJournalRecord]:
        target_dir = self.get_document_directory(target_doc_id)
        manifest_path = os.path.join(target_dir, ".save_as_manifest.json")

        # Guard: Check if transaction lock is held by another process or thread
        if is_save_as_transaction_locked_by_other(target_dir):
            raise HistoryLockError(
                f"An active Save As transaction is currently in progress for {target_doc_id} by another process/thread."
            )

        # Check if an existing Save As manifest already exists from a previous crash
        if os.path.exists(manifest_path):
            res = self._recover_save_as_locked(target_doc_id, target_path)
            if res.get("status") not in ("COMMITTED", "ABORTED", "NO_MANIFEST"):
                raise HistoryLockError(
                    f"An active or conflicting Save As transaction already exists for {target_doc_id}."
                )

        now_dt = datetime.now(timezone.utc)
        save_id = uuid.uuid4().hex
        epoch_id = target_epoch_id or f"ep_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # Inspect target's history.jsonl
        target_history = os.path.join(target_dir, "history.jsonl")
        initial_history_exists = os.path.exists(target_history)
        initial_history_has_records = False
        initial_history_sha256 = None

        if initial_history_exists:
            size = os.path.getsize(target_history)
            if size > 0:
                initial_history_has_records = True
                with open(target_history, "rb") as f:
                    initial_history_sha256 = hashlib.sha256(f.read()).hexdigest()

        # Compute previous_content_sha256
        prev_hash = None
        if previous_target_exists and os.path.exists(target_path):
            try:
                with open(target_path, "rb") as f:
                    prev_hash = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                prev_hash = None
        elif previous_content is not None:
            if isinstance(previous_content, bytes):
                prev_hash = hashlib.sha256(previous_content).hexdigest()
            else:
                prev_hash = hashlib.sha256(previous_content.encode("utf-8")).hexdigest()

        # Compute expected_target_sha256 with exact byte encoding & BOM support
        if isinstance(expected_content, bytes):
            expected_bytes = expected_content
        else:
            raw_text_bytes = expected_content.encode("utf-8")
            expected_bytes = (codecs.BOM_UTF8 + raw_text_bytes) if has_bom else raw_text_bytes
        expected_hash = hashlib.sha256(expected_bytes).hexdigest()

        # Load existing target metadata
        meta = self.load_metadata(target_doc_id)
        previous_epoch = meta.active_epoch if meta else 1

        # Build ChangeSet
        changeset = ChangeSet(
            save_id=save_id,
            doc_id=target_doc_id,
            target_path=os.path.abspath(target_path),
            timestamp=now_dt.isoformat(),
            previous_content_sha256=prev_hash or "",
            expected_target_sha256=expected_hash,
            changes=changes,
            target_epoch_id=epoch_id,
            summary=summary,
            author=author,
        )

        # Build SaveAsManifest with embedded ChangeSet backup
        manifest = SaveAsManifest(
            save_id=save_id,
            source_doc_id=source_doc_id,
            target_doc_id=target_doc_id,
            canonical_target_path=canonical_path(target_path),
            source_path=os.path.abspath(source_path),
            previous_target_exists=previous_target_exists,
            previous_content_sha256=prev_hash,
            expected_target_sha256=expected_hash,
            target_epoch_id=epoch_id,
            previous_epoch=previous_epoch,
            initial_history_exists=initial_history_exists,
            initial_history_has_records=initial_history_has_records,
            initial_history_sha256=initial_history_sha256,
            changeset=changeset.to_dict(),
            state="PREPARED",
            step="PREPARED",
            created_at=now_dt.isoformat(),
            archived_filename=f"history_epoch_{epoch_id}.jsonl",
        )

        # 1. Atomically write manifest
        self._save_manifest_atomic(target_dir, manifest)

        # 2. Atomically write pending journal
        pending_record = PendingJournalRecord(
            save_id=save_id,
            doc_id=target_doc_id,
            target_path=os.path.abspath(target_path),
            timestamp=now_dt.isoformat(),
            previous_content_sha256=prev_hash or "",
            expected_target_sha256=expected_hash,
            changeset=changeset,
            target_epoch_id=epoch_id,
            status="PENDING",
        )

        pending_filename = f".pending_{save_id}.json"
        pending_path = os.path.join(target_dir, pending_filename)
        tmp_pending = pending_path + ".tmp"
        with open(tmp_pending, "w", encoding="utf-8") as f:
            json.dump(pending_record.to_dict(), f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_pending, pending_path)

        return manifest, pending_record

    def commit_save_as_transaction(
        self,
        target_doc_id: str,
        save_id: str,
        expected_target_path: Optional[str] = None,
    ) -> bool:
        """
        Phase 2 of Save As Two-Phase Transaction:
        Called after XML file has been successfully written to disk.
        Finalizes epoch transition, updates metadata, initializes new history.jsonl,
        appends ChangeSet, and removes journal/manifest files.
        Protected by DocumentLock on target directory.
        """
        target_dir = self.get_document_directory(target_doc_id)
        with DocumentLock(target_dir):
            return self._commit_save_as_transaction_locked(
                target_doc_id=target_doc_id,
                save_id=save_id,
                expected_target_path=expected_target_path,
            )

    def _commit_save_as_transaction_locked(
        self,
        target_doc_id: str,
        save_id: str,
        expected_target_path: Optional[str] = None,
    ) -> bool:
        target_dir = self.get_document_directory(target_doc_id)
        manifest_path = os.path.join(target_dir, ".save_as_manifest.json")

        if not os.path.exists(manifest_path):
            # Check if already committed and cleaned up
            target_history = os.path.join(target_dir, "history.jsonl")
            if os.path.exists(target_history):
                try:
                    with open(target_history, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            ls = line.strip()
                            if not ls:
                                continue
                            try:
                                entry = json.loads(ls)
                                if entry.get("save_id") == save_id:
                                    return True
                            except Exception:
                                continue
                except Exception:
                    pass
            return False

        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
            manifest = SaveAsManifest.from_dict(mdata)
        except Exception:
            self._quarantine_corrupt_manifest_locked(target_dir)
            return False

        if manifest.save_id != save_id or manifest.target_doc_id != target_doc_id:
            return False

        # Verify target path isolation
        if expected_target_path is not None:
            if canonical_path(manifest.canonical_target_path) != canonical_path(expected_target_path):
                self._quarantine_manifest_locked(target_dir, manifest, "target_path_mismatch")
                return False

        # Verify physical XML content on disk
        target_path = manifest.canonical_target_path
        if not os.path.exists(target_path):
            return False

        try:
            with open(target_path, "rb") as f:
                disk_xml_hash = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return False

        if disk_xml_hash != manifest.expected_target_sha256:
            self._quarantine_manifest_locked(target_dir, manifest, "xml_hash_mismatch")
            return False

        # Load and validate ChangeSet (from pending file or manifest backup)
        pending_path = os.path.join(target_dir, f".pending_{save_id}.json")
        prec: Optional[PendingJournalRecord] = None
        pending_is_corrupt = False
        if os.path.exists(pending_path):
            try:
                with open(pending_path, "r", encoding="utf-8") as pf:
                    pdata = json.load(pf)
                prec = PendingJournalRecord.from_dict(pdata)
            except Exception:
                prec = None
                pending_is_corrupt = True

        changeset: Optional[ChangeSet] = None
        if prec is not None:
            # 1. Validate pending record core metadata
            if prec.save_id != save_id or prec.doc_id != target_doc_id:
                self._quarantine_manifest_locked(target_dir, manifest, "id_mismatch")
                return False
            if manifest.target_epoch_id and prec.target_epoch_id and prec.target_epoch_id != manifest.target_epoch_id:
                self._quarantine_manifest_locked(target_dir, manifest, "epoch_mismatch")
                return False
            if prec.expected_target_sha256 != manifest.expected_target_sha256:
                self._quarantine_manifest_locked(target_dir, manifest, "expected_sha_mismatch")
                return False

            # 2. If manifest backup exists, perform full canonical structural and content comparison
            if manifest.changeset is not None:
                try:
                    m_cs = ChangeSet.from_dict(manifest.changeset)
                    id_ok = (
                        m_cs.save_id == prec.changeset.save_id == save_id
                        and m_cs.doc_id == prec.changeset.doc_id == target_doc_id
                        and (m_cs.target_epoch_id or manifest.target_epoch_id) == (prec.changeset.target_epoch_id or manifest.target_epoch_id)
                        and (m_cs.expected_target_sha256 or manifest.expected_target_sha256) == (prec.changeset.expected_target_sha256 or manifest.expected_target_sha256)
                    )
                    content_ok = (_canonical_changeset_payload(m_cs) == _canonical_changeset_payload(prec.changeset))
                    if not (id_ok and content_ok):
                        self._quarantine_manifest_locked(target_dir, manifest, "changeset_mismatch")
                        return False
                except Exception:
                    self._quarantine_manifest_locked(target_dir, manifest, "corrupt_manifest_changeset")
                    return False
            changeset = prec.changeset
        elif manifest.changeset is not None:
            # Pending was missing or corrupted; safely recover from manifest backup
            try:
                m_cs = ChangeSet.from_dict(manifest.changeset)
                if m_cs.save_id == save_id and (m_cs.expected_target_sha256 is None or m_cs.expected_target_sha256 == manifest.expected_target_sha256):
                    changeset = m_cs
                else:
                    self._quarantine_manifest_locked(target_dir, manifest, "invalid_manifest_changeset")
                    return False
            except Exception:
                self._quarantine_manifest_locked(target_dir, manifest, "corrupt_manifest_changeset")
                return False
        elif manifest.step in ("LOG_INITIALIZED", "COMMITTED"):
            # Already appended to log before crash
            changeset = None
        else:
            # Both pending and manifest changeset are corrupt or missing
            reason = "pending_corrupt_manifest_missing" if pending_is_corrupt else "missing_changeset"
            self._quarantine_manifest_locked(target_dir, manifest, reason)
            return False

        target_history = os.path.join(target_dir, "history.jsonl")

        # Concurrency guard: verify initial history hash if it had records and step is PREPARED
        if manifest.step == "PREPARED" and manifest.initial_history_has_records:
            if not os.path.exists(target_history):
                self._quarantine_manifest_locked(target_dir, manifest, "initial_history_missing")
                return False
            with open(target_history, "rb") as f:
                curr_h_hash = hashlib.sha256(f.read()).hexdigest()
            if curr_h_hash != manifest.initial_history_sha256:
                self._quarantine_manifest_locked(target_dir, manifest, "initial_history_modified")
                return False

        # Step 1: Archive old history (if it exists and has records)
        archived_filename = manifest.archived_filename or f"history_epoch_{manifest.target_epoch_id}.jsonl"
        archived_path = os.path.join(target_dir, archived_filename)

        if manifest.step == "PREPARED":
            if manifest.initial_history_has_records and os.path.exists(target_history):
                try:
                    os.rename(target_history, archived_path)
                except FileExistsError:
                    # Target archive already exists: check if identical from prior crash
                    with open(archived_path, "rb") as af:
                        ex_hash = hashlib.sha256(af.read()).hexdigest()
                    if ex_hash != manifest.initial_history_sha256:
                        # Collision: generate unique collision-proof name
                        now_s = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        col_name = f"history_epoch_{manifest.target_epoch_id}_{now_s}_{uuid.uuid4().hex[:6]}.jsonl"
                        col_path = os.path.join(target_dir, col_name)
                        manifest.archived_filename = col_name
                        archived_filename = col_name
                        archived_path = col_path
                        os.rename(target_history, col_path)

            manifest.step = "EPOCH_ARCHIVED"
            self._save_manifest_atomic(target_dir, manifest)

        # Step 2: Update Metadata
        if manifest.step == "EPOCH_ARCHIVED":
            meta = self.load_metadata(target_doc_id)
            now_iso = datetime.now(timezone.utc).isoformat()
            if meta is None:
                meta = DocumentMetadata(
                    doc_id=target_doc_id,
                    original_filename=os.path.basename(manifest.canonical_target_path),
                    last_known_path=manifest.canonical_target_path,
                    created_at=now_iso,
                    last_saved_at=now_iso,
                    active_epoch=2 if manifest.initial_history_has_records else 1,
                    archived_epochs=[{
                        "epoch_id": manifest.target_epoch_id,
                        "archived_file": archived_filename,
                        "archived_at": now_iso,
                        "branched_from_source": manifest.source_path,
                    }] if manifest.initial_history_has_records else [],
                    last_saved_content_sha256=manifest.expected_target_sha256,
                )
            else:
                if manifest.initial_history_has_records:
                    already_archived = any(e.get("epoch_id") == manifest.target_epoch_id for e in meta.archived_epochs)
                    if not already_archived:
                        meta.active_epoch += 1
                        meta.archived_epochs.append({
                            "epoch_id": manifest.target_epoch_id,
                            "archived_file": archived_filename,
                            "archived_at": now_iso,
                            "branched_from_source": manifest.source_path,
                        })
                meta.last_saved_at = now_iso
                meta.last_known_path = manifest.canonical_target_path
                meta.last_saved_content_sha256 = manifest.expected_target_sha256

            self.save_metadata(meta)
            manifest.step = "METADATA_UPDATED"
            self._save_manifest_atomic(target_dir, manifest)

        # Step 3: Initialize new active history and append ChangeSet
        if manifest.step in ("METADATA_UPDATED", "LOG_INITIALIZED"):
            has_branch_init = False
            has_changeset = False
            self._repair_torn_tail(target_history)

            if os.path.exists(target_history) and os.path.getsize(target_history) > 0:
                try:
                    with open(target_history, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            ls = line.strip()
                            if not ls:
                                continue
                            try:
                                entry = json.loads(ls)
                                if entry.get("event") == "BRANCH_INITIALIZED" and entry.get("target_epoch_id") == manifest.target_epoch_id:
                                    has_branch_init = True
                                if entry.get("save_id") == save_id:
                                    has_changeset = True
                            except Exception:
                                continue
                except Exception:
                    pass

            if not has_branch_init:
                branch_event = {
                    "save_id": uuid.uuid4().hex,
                    "doc_id": target_doc_id,
                    "target_path": os.path.abspath(manifest.canonical_target_path),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "BRANCH_INITIALIZED",
                    "source_doc_id": manifest.source_doc_id,
                    "source_path": os.path.abspath(manifest.source_path),
                    "target_epoch_id": manifest.target_epoch_id,
                    "changes": [],
                }
                lines = [
                    json.dumps(branch_event, ensure_ascii=False) + "\n",
                ]
                if changeset is not None:
                    lines.append(json.dumps(changeset.to_dict(), ensure_ascii=False) + "\n")
                tmp_h = target_history + ".new.tmp"
                with open(tmp_h, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(lines)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_h, target_history)
                has_changeset = True
            elif not has_changeset and changeset is not None:
                cs_line = json.dumps(changeset.to_dict(), ensure_ascii=False) + "\n"
                with open(target_history, "a", encoding="utf-8", newline="\n") as f:
                    f.write(cs_line)
                    f.flush()
                    os.fsync(f.fileno())
                has_changeset = True

            manifest.step = "COMMITTED"
            manifest.state = "COMMITTED"

        # Step 4: Cleanup pending and manifest files
        if os.path.exists(pending_path):
            try:
                os.remove(pending_path)
            except OSError:
                pass

        if os.path.exists(manifest_path):
            try:
                os.remove(manifest_path)
            except OSError:
                pass

        # Step 5: Check rotation threshold
        if os.path.exists(target_history) and os.path.getsize(target_history) >= self.rotation_threshold_bytes:
            self._rotate_log_locked(target_doc_id)

        return True

    def abort_save_as_transaction(
        self,
        target_doc_id: str,
        save_id: str,
    ) -> Dict[str, Any]:
        """
        Aborts a pending Save As transaction after verifying XML status on disk.
        If XML remains at previous state (or brand new target is absent): safe ABORT.
        If XML was already written to expected content: rejects abort and commits to protect history.
        If XML is in an unexpected state: marks CONFLICT.
        Protected by DocumentLock on target directory.
        """
        target_dir = self.get_document_directory(target_doc_id)
        with DocumentLock(target_dir):
            return self._abort_save_as_transaction_locked(target_doc_id, save_id)

    def _abort_save_as_transaction_locked(
        self,
        target_doc_id: str,
        save_id: str,
    ) -> Dict[str, Any]:
        target_dir = self.get_document_directory(target_doc_id)
        manifest_path = os.path.join(target_dir, ".save_as_manifest.json")

        if not os.path.exists(manifest_path):
            return {"status": "NO_MANIFEST", "success": False}

        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
            manifest = SaveAsManifest.from_dict(mdata)
        except Exception:
            self._quarantine_corrupt_manifest_locked(target_dir)
            return {"status": "CORRUPT_MANIFEST_QUARANTINED", "success": False}

        if manifest.save_id != save_id or manifest.target_doc_id != target_doc_id:
            return {"status": "ID_MISMATCH", "success": False}

        if is_save_as_transaction_locked_by_other(target_dir):
            return {
                "status": "TRANSACTION_ACTIVE_LOCKED",
                "success": False,
                "summary": "Active Save As transaction lock held by another process; abort rejected.",
            }

        target_path = manifest.canonical_target_path
        disk_exists = os.path.exists(target_path)
        disk_hash = None
        if disk_exists:
            try:
                with open(target_path, "rb") as f:
                    disk_hash = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                disk_hash = None

        # Case 1: Safe abort
        is_safe_abort = False
        if not manifest.previous_target_exists and not disk_exists:
            is_safe_abort = True
        elif manifest.previous_target_exists and disk_exists and disk_hash == manifest.previous_content_sha256:
            is_safe_abort = True

        if is_safe_abort:
            pending_path = os.path.join(target_dir, f".pending_{save_id}.json")
            if os.path.exists(pending_path):
                try:
                    os.remove(pending_path)
                except OSError:
                    pass
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            aborted_manifest = os.path.join(target_dir, f".aborted_save_as_manifest_{save_id}_{ts}.json")
            try:
                os.replace(manifest_path, aborted_manifest)
            except OSError:
                try:
                    os.remove(manifest_path)
                except OSError:
                    pass
            return {
                "status": "ABORTED",
                "success": True,
                "summary": "XML remained at previous state; Save As transaction safely aborted.",
            }

        # Case 2: Abort rejected because XML was already written
        if disk_exists and disk_hash == manifest.expected_target_sha256:
            committed = self._commit_save_as_transaction_locked(target_doc_id, save_id, target_path)
            return {
                "status": "COMMITTED_ON_ABORT_REJECTION",
                "success": committed,
                "summary": "Abort rejected because target XML was already updated on disk; committed transaction to preserve history.",
            }

        # Case 3: Conflict
        self._quarantine_manifest_locked(target_dir, manifest, "unexpected_xml_on_abort")
        return {
            "status": "CONFLICT",
            "success": False,
            "summary": "Disk XML hash matched neither previous nor expected; quarantined to prevent data loss.",
        }

    def recover_save_as(
        self,
        target_doc_id: str,
        current_file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Recovers an incomplete Save As transaction using .save_as_manifest.json.
        Inspects physical XML and history state to safely commit, abort, or quarantine.
        Protected by DocumentLock on target directory.
        """
        target_dir = self.get_document_directory(target_doc_id)
        with DocumentLock(target_dir):
            return self._recover_save_as_locked(target_doc_id, current_file_path)

    def _recover_save_as_locked(
        self,
        target_doc_id: str,
        current_file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_dir = self.get_document_directory(target_doc_id)
        manifest_path = os.path.join(target_dir, ".save_as_manifest.json")

        if not os.path.exists(manifest_path):
            return {"status": "NO_MANIFEST"}

        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
            manifest = SaveAsManifest.from_dict(mdata)
        except Exception:
            self._quarantine_corrupt_manifest_locked(target_dir)
            return {"status": "CORRUPT_MANIFEST_QUARANTINED"}

        if manifest.state == "CONFLICT":
            return {"status": "CONFLICT", "save_id": manifest.save_id, "summary": "Manifest is in conflict state."}

        # Guard: If another process/thread holds the transaction lock, the transaction is actively running!
        # Do not abort or interfere with it!
        if is_save_as_transaction_locked_by_other(target_dir):
            return {
                "status": "TRANSACTION_ACTIVE_LOCKED",
                "save_id": manifest.save_id,
                "summary": "Active Save As transaction lock held by another process; recovery/abort blocked.",
            }

        target_path = current_file_path or manifest.canonical_target_path
        disk_exists = os.path.exists(target_path)
        disk_hash = None
        if disk_exists:
            try:
                with open(target_path, "rb") as f:
                    disk_hash = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                disk_hash = None

        # 1. Expected XML Hash -> Commit
        if disk_exists and disk_hash == manifest.expected_target_sha256:
            # Check ChangeSet availability before committing
            pending_path = os.path.join(target_dir, f".pending_{manifest.save_id}.json")
            has_cs = False
            if os.path.exists(pending_path):
                try:
                    with open(pending_path, "r", encoding="utf-8") as pf:
                        pdata = json.load(pf)
                    prec = PendingJournalRecord.from_dict(pdata)
                    if prec.save_id == manifest.save_id and prec.doc_id == target_doc_id:
                        has_cs = True
                except Exception:
                    has_cs = False

            if not has_cs and manifest.changeset:
                try:
                    m_cs = ChangeSet.from_dict(manifest.changeset)
                    if m_cs.save_id == manifest.save_id and (m_cs.expected_target_sha256 is None or m_cs.expected_target_sha256 == manifest.expected_target_sha256):
                        has_cs = True
                except Exception:
                    has_cs = False

            if not has_cs and manifest.step in ("LOG_INITIALIZED", "COMMITTED"):
                has_cs = True

            if not has_cs:
                self._quarantine_manifest_locked(target_dir, manifest, "missing_or_corrupt_changeset")
                return {
                    "status": "CONFLICT",
                    "save_id": manifest.save_id,
                    "error": "ChangeSet is missing; commit blocked to prevent data loss.",
                }

            committed = self._commit_save_as_transaction_locked(target_doc_id, manifest.save_id, target_path)
            if committed:
                return {
                    "save_id": manifest.save_id,
                    "status": "COMMITTED",
                    "summary": "XML file confirmed on disk; Save As transaction successfully committed.",
                }
            else:
                return {
                    "save_id": manifest.save_id,
                    "status": "CONFLICT",
                    "summary": "Commit verification failed during Save As recovery.",
                }

        # 2. Previous XML Hash or brand-new target absent -> Abort
        is_abort = False
        if not manifest.previous_target_exists and not disk_exists:
            is_abort = True
        elif manifest.previous_target_exists and disk_exists and disk_hash == manifest.previous_content_sha256:
            is_abort = True

        if is_abort:
            aborted_res = self._abort_save_as_transaction_locked(target_doc_id, manifest.save_id)
            return {
                "save_id": manifest.save_id,
                "status": "ABORTED",
                "summary": "XML remained at previous state; Save As transaction safely aborted.",
                "details": aborted_res,
            }

        # 3. Disk hash matched neither -> Conflict
        self._quarantine_manifest_locked(target_dir, manifest, "unexpected_xml_hash")
        return {
            "save_id": manifest.save_id,
            "status": "CONFLICT",
            "summary": "Disk XML hash matched neither previous nor expected; quarantined to prevent corruption.",
        }

    def initialize_save_as_epoch(
        self,
        source_doc_id: str,
        target_doc_id: str,
        target_path: str,
        source_path: str,
        target_epoch_id: Optional[str] = None
    ) -> str:
        """
        Legacy helper maintained for backward compatibility.
        """
        target_dir = self.get_document_directory(target_doc_id)
        now = datetime.now(timezone.utc)
        epoch_id = target_epoch_id or f"ep_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        canon_target_path = canonical_path(target_path)

        with DocumentLock(target_dir):
            target_history = os.path.join(target_dir, "history.jsonl")
            meta = self.load_metadata(target_doc_id)

            # 1. Inspect and recover old-epoch pending journals before freezing history
            pending_files = [f for f in os.listdir(target_dir) if f.startswith(".pending_") and f.endswith(".json")]
            for pf in pending_files:
                p_path = os.path.join(target_dir, pf)
                try:
                    with open(p_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    p_rec = PendingJournalRecord.from_dict(data)
                except Exception:
                    continue

                ts = now.strftime("%Y%m%d_%H%M%S")

                # 1a. Identity isolation: Verify p_rec.doc_id == target_doc_id
                if p_rec.doc_id != target_doc_id:
                    conflict_path = os.path.join(target_dir, f".conflict_foreign_doc_{p_rec.save_id}_{ts}.json")
                    try:
                        os.replace(p_path, conflict_path)
                    except OSError:
                        pass
                    continue

                # 1b. Path isolation: Verify p_rec.target_path matches canonical target_path
                if canonical_path(p_rec.target_path) != canon_target_path:
                    conflict_path = os.path.join(target_dir, f".conflict_path_mismatch_{p_rec.save_id}_{ts}.json")
                    try:
                        os.replace(p_path, conflict_path)
                    except OSError:
                        pass
                    continue

                # 1c. Epoch isolation: If pending belongs to the new epoch being branched,
                # leave it in place for the new epoch (do not finalize to old history)
                if p_rec.target_epoch_id == epoch_id:
                    continue

                # 1d. Finalize valid old-epoch pending record
                current_hash = None
                if os.path.exists(target_path):
                    try:
                        with open(target_path, "rb") as f:
                            current_hash = hashlib.sha256(f.read()).hexdigest()
                    except Exception:
                        pass

                if current_hash == p_rec.expected_target_sha256:
                    self.commit_pending(target_doc_id, p_rec.save_id, expected_target_path=target_path)
                elif current_hash == p_rec.previous_content_sha256:
                    aborted_path = os.path.join(target_dir, f".aborted_{p_rec.save_id}_{ts}.json")
                    try:
                        os.replace(p_path, aborted_path)
                    except OSError:
                        pass
                else:
                    conflict_path = os.path.join(target_dir, f".conflict_{p_rec.save_id}_{ts}.json")
                    try:
                        os.replace(p_path, conflict_path)
                    except OSError:
                        pass

            # 2. Check if this epoch was already archived / initialized
            already_archived = False
            if meta and meta.archived_epochs:
                already_archived = any(e.get("epoch_id") == epoch_id for e in meta.archived_epochs)

            # Check if active target_history is already initialized with this epoch's BRANCH_INITIALIZED event
            already_initialized_branch = False
            if os.path.exists(target_history) and os.path.getsize(target_history) > 0:
                try:
                    with open(target_history, "r", encoding="utf-8", errors="replace") as f:
                        first_line = f.readline().strip()
                        if first_line:
                            ev = json.loads(first_line)
                            if ev.get("event") == "BRANCH_INITIALIZED" and ev.get("target_epoch_id") == epoch_id:
                                already_initialized_branch = True
                except Exception:
                    pass

            archived_filename = f"history_epoch_{epoch_id}.jsonl"
            archived_path = os.path.join(target_dir, archived_filename)

            # Freeze existing history ONLY IF not already initialized for this epoch
            if not already_initialized_branch and os.path.exists(target_history) and os.path.getsize(target_history) > 0:
                if not os.path.exists(archived_path):
                    os.replace(target_history, archived_path)

                if not already_archived:
                    if meta is None:
                        meta = DocumentMetadata(
                            doc_id=target_doc_id,
                            original_filename=os.path.basename(target_path),
                            last_known_path=target_path,
                            created_at=now.isoformat(),
                            last_saved_at=now.isoformat(),
                            active_epoch=2,
                            archived_epochs=[{
                                "epoch_id": epoch_id,
                                "archived_file": archived_filename,
                                "archived_at": now.isoformat(),
                                "branched_from_source": source_path,
                            }]
                        )
                    else:
                        meta.active_epoch += 1
                        meta.archived_epochs.append({
                            "epoch_id": epoch_id,
                            "archived_file": archived_filename,
                            "archived_at": now.isoformat(),
                            "branched_from_source": source_path,
                        })
                    self.save_metadata(meta)

            # 3. Ensure fresh history.jsonl with BRANCH_INITIALIZED event
            if not already_initialized_branch:
                branch_event = {
                    "save_id": uuid.uuid4().hex,
                    "doc_id": target_doc_id,
                    "target_path": os.path.abspath(target_path),
                    "timestamp": now.isoformat(),
                    "event": "BRANCH_INITIALIZED",
                    "source_doc_id": source_doc_id,
                    "source_path": os.path.abspath(source_path),
                    "target_epoch_id": epoch_id,
                    "changes": [],
                }
                branch_tmp = target_history + ".init.tmp"
                with open(branch_tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(branch_event, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(branch_tmp, target_history)

        return epoch_id

    # -----------------------------------------------------------------------
    # Gzip Rotation & Manifest Recovery
    # -----------------------------------------------------------------------

    def rotate_log(self, doc_id: str) -> Optional[str]:
        """
        Compresses active history.jsonl into a timestamped, unique Gzip archive
        using a manifest-backed atomic rotation sequence.
        Protected by DocumentLock. Postpones if Save As transaction is active.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            return self._rotate_log_locked(doc_id)

    def _rotate_log_locked(self, doc_id: str) -> Optional[str]:
        doc_dir = self.get_document_directory(doc_id)
        history_path = os.path.join(doc_dir, "history.jsonl")
        manifest_path = os.path.join(doc_dir, ".rotation_manifest.json")

        if not os.path.exists(history_path) or os.path.getsize(history_path) == 0:
            return None

        # Safety check: postpone rotation if any pending journal, temp write, or Save As manifest is active
        save_as_manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
        save_as_manifest_tmp = os.path.join(doc_dir, ".save_as_manifest.json.tmp")
        if os.path.exists(save_as_manifest_path) or os.path.exists(save_as_manifest_tmp):
            return None

        active_pending = [
            f for f in os.listdir(doc_dir)
            if f.startswith(".pending_") and (f.endswith(".json") or f.endswith(".tmp"))
        ]
        if active_pending:
            return None

        now = datetime.now(timezone.utc)
        rot_id = uuid.uuid4().hex[:6]
        timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
        archive_filename = f"history_{timestamp_str}_{rot_id}.jsonl.gz"
        archive_path = os.path.join(doc_dir, archive_filename)
        tmp_archive_path = archive_path + ".tmp"

        # 1. Compute source hash
        with open(history_path, "rb") as f:
            source_data = f.read()
        source_sha256 = hashlib.sha256(source_data).hexdigest()

        # 2. Write initial manifest (COMPRESSING)
        manifest = {
            "rotation_id": rot_id,
            "state": "COMPRESSING",
            "tmp_archive": tmp_archive_path,
            "target_archive": archive_path,
            "source_sha256": source_sha256,
        }
        tmp_manifest = manifest_path + ".tmp"
        with open(tmp_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_manifest, manifest_path)

        # 3. Compress to .tmp
        with gzip.open(tmp_archive_path, "wb", compresslevel=9) as gz:
            gz.write(source_data)
            gz.flush()

        # Verify gzip integrity
        decomp_sha = None
        try:
            with gzip.open(tmp_archive_path, "rb") as gz:
                decompressed = gz.read()
            decomp_sha = hashlib.sha256(decompressed).hexdigest()
        except Exception as e:
            # Corrupt compression! Quarantine or remove tmp, clean manifest, raise error
            if os.path.exists(tmp_archive_path):
                ts = now.strftime("%Y%m%d_%H%M%S")
                corrupt_path = os.path.join(doc_dir, f".corrupt_archive_{ts}_{rot_id}.gz")
                try:
                    os.replace(tmp_archive_path, corrupt_path)
                except OSError:
                    pass
            try:
                os.remove(manifest_path)
            except OSError:
                pass
            raise HistoryCorruptionError(f"Gzip compression verification failed: {e}")

        if decomp_sha != source_sha256:
            if os.path.exists(tmp_archive_path):
                ts = now.strftime("%Y%m%d_%H%M%S")
                corrupt_path = os.path.join(doc_dir, f".corrupt_archive_{ts}_{rot_id}.gz")
                try:
                    os.replace(tmp_archive_path, corrupt_path)
                except OSError:
                    pass
            try:
                os.remove(manifest_path)
            except OSError:
                pass
            raise HistoryCorruptionError("Gzip rotation verification failed: CRC/hash mismatch.")

        # 4. Update manifest (ARCHIVED)
        manifest["state"] = "ARCHIVED"
        with open(tmp_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_manifest, manifest_path)

        # 5. Atomically rename .tmp -> target_archive
        os.replace(tmp_archive_path, archive_path)

        # 6. Safely clear active history.jsonl
        new_history_tmp = history_path + ".new"
        with open(new_history_tmp, "w", encoding="utf-8", newline="\n") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(new_history_tmp, history_path)

        # 7. Rotation complete, remove manifest
        try:
            os.remove(manifest_path)
        except OSError:
            pass

        return archive_filename

    def recover_rotation(self, doc_id: str) -> Dict[str, Any]:
        """
        Recovers from crashes during Gzip rotation using .rotation_manifest.json.
        Ensures active history.jsonl is NEVER cleared unless archive verification succeeds.
        Supports:
        - Case A (Exact hash match): Safely clears active history, cleans manifest.
        - Case B (Appended valid tail): Preserves only verified new tail records in active
          history, removes archived records from active log, cleans manifest.
        - Case C (Prefix mismatch or corrupt tail): Preserves active history byte-for-byte,
          retains manifest with conflict state, prevents duplicate archives or data loss.
        Postpones if an active Save As transaction is in flight.
        Idempotent on repeated calls.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            return self._recover_rotation_locked(doc_id)

    def _recover_rotation_locked(self, doc_id: str) -> Dict[str, Any]:
        doc_dir = self.get_document_directory(doc_id)
        manifest_path = os.path.join(doc_dir, ".rotation_manifest.json")
        history_path = os.path.join(doc_dir, "history.jsonl")

        if not os.path.exists(manifest_path):
            return {"status": "NO_MANIFEST"}

        # Guard: If Save As manifest is active, postpone rotation recovery to protect active log
        save_as_manifest_path = os.path.join(doc_dir, ".save_as_manifest.json")
        if os.path.exists(save_as_manifest_path):
            return {
                "status": "SAVE_AS_ACTIVE_POSTPONED",
                "summary": "Save As transaction is active; rotation recovery postponed to protect history."
            }

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            try:
                os.remove(manifest_path)
            except OSError:
                pass
            return {"status": "CORRUPT_MANIFEST_REMOVED"}

        state = manifest.get("state")
        tmp_archive = manifest.get("tmp_archive")
        target_archive = manifest.get("target_archive")
        source_sha256 = manifest.get("source_sha256")
        now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # If manifest is already in conflict state from a prior run,
        # do not re-archive or duplicate; leave intact.
        if state in ("RECOVERY_CONFLICT", "CONFLICT_PREFIX_MISMATCH", "CONFLICT_TAIL_CORRUPT"):
            return {"status": state, "summary": "Manifest is in conflict state; active log and archive preserved."}

        if state == "COMPRESSING":
            # Compression was in progress; delete partial .tmp and cancel rotation
            if tmp_archive and os.path.exists(tmp_archive):
                try:
                    os.remove(tmp_archive)
                except OSError:
                    pass
            try:
                os.remove(manifest_path)
            except OSError:
                pass
            return {"status": "CANCELLED_COMPRESSING"}

        elif state == "ARCHIVED":
            # State was ARCHIVED: .tmp was verified before crash
            if tmp_archive and os.path.exists(tmp_archive):
                if target_archive and not os.path.exists(target_archive):
                    try:
                        os.replace(tmp_archive, target_archive)
                    except OSError:
                        pass
                else:
                    try:
                        os.remove(tmp_archive)
                    except OSError:
                        pass

            # If target archive does not exist, clean manifest and return
            if not target_archive or not os.path.exists(target_archive):
                try:
                    os.remove(manifest_path)
                except OSError:
                    pass
                return {"status": "ARCHIVE_MISSING"}

            archive_valid = False
            decompressed_bytes = None
            try:
                with gzip.open(target_archive, "rb") as gz:
                    decompressed_bytes = gz.read()
                if hashlib.sha256(decompressed_bytes).hexdigest() == source_sha256:
                    archive_valid = True
            except Exception:
                archive_valid = False

            if not archive_valid or decompressed_bytes is None:
                # Archive is corrupt! Quarantine it.
                corrupt_path = os.path.join(doc_dir, f".corrupt_archive_{now_ts}.gz")
                try:
                    os.replace(target_archive, corrupt_path)
                except OSError:
                    pass
                # NEVER touch history.jsonl! It retains all original data.
                try:
                    os.remove(manifest_path)
                except OSError:
                    pass
                return {"status": "CORRUPT_ARCHIVE_QUARANTINED"}

            # Archive is valid. Now inspect active history.jsonl
            current_h_bytes = b""
            if os.path.exists(history_path):
                try:
                    with open(history_path, "rb") as f:
                        current_h_bytes = f.read()
                except Exception:
                    current_h_bytes = b""

            current_h_sha = hashlib.sha256(current_h_bytes).hexdigest()

            # Case A: Exact hash match (active log unmodified since rotation began)
            if current_h_sha == source_sha256:
                new_tmp = history_path + ".new"
                try:
                    with open(new_tmp, "w", encoding="utf-8", newline="\n") as f:
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(new_tmp, history_path)
                    # Clean manifest ONLY after atomic replace succeeds
                    os.remove(manifest_path)
                    return {"status": "SUCCESS_EXACT_MATCH_CLEARED"}
                except Exception as e:
                    if os.path.exists(new_tmp):
                        try:
                            os.remove(new_tmp)
                        except OSError:
                            pass
                    return {"status": "REPLACE_FAILED", "error": str(e)}

            # Case B: Active JSONL starts with decompressed_bytes (new records appended after rotation began)
            elif current_h_bytes.startswith(decompressed_bytes) and len(current_h_bytes) > len(decompressed_bytes):
                tail_bytes = current_h_bytes[len(decompressed_bytes):]
                tail_valid = False
                tail_lines = []
                try:
                    tail_str = tail_bytes.decode("utf-8")
                    lines = [l.strip() for l in tail_str.splitlines() if l.strip()]
                    for l in lines:
                        json.loads(l)  # Validate each line is valid JSON
                        tail_lines.append(l)
                    if tail_lines:
                        tail_valid = True
                except Exception:
                    tail_valid = False

                if tail_valid:
                    # Write only the new tail records to active history
                    new_tmp = history_path + ".new"
                    try:
                        with open(new_tmp, "w", encoding="utf-8", newline="\n") as f:
                            for l in tail_lines:
                                f.write(l + "\n")
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(new_tmp, history_path)
                        # Clean manifest ONLY after atomic replace succeeds
                        os.remove(manifest_path)
                        return {
                            "status": "SUCCESS_TAIL_PRESERVED",
                            "preserved_record_count": len(tail_lines),
                        }
                    except Exception as e:
                        if os.path.exists(new_tmp):
                            try:
                                os.remove(new_tmp)
                            except OSError:
                                pass
                        return {"status": "REPLACE_FAILED", "error": str(e)}
                else:
                    # Tail contains invalid or corrupt JSON!
                    # Do NOT touch active history! Do NOT delete manifest!
                    manifest["state"] = "CONFLICT_TAIL_CORRUPT"
                    manifest["conflict_at"] = now_ts
                    try:
                        tmp_m = manifest_path + ".tmp"
                        with open(tmp_m, "w", encoding="utf-8") as f:
                            json.dump(manifest, f)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp_m, manifest_path)
                    except OSError:
                        pass
                    return {
                        "status": "CONFLICT_TAIL_CORRUPT",
                        "summary": "Tail records in active history are invalid JSON; active history preserved.",
                    }

            # Case C: Prefix mismatch (active history was modified at the beginning or completely changed)
            else:
                # Do NOT touch active history! Do NOT delete manifest!
                manifest["state"] = "CONFLICT_PREFIX_MISMATCH"
                manifest["conflict_at"] = now_ts
                manifest["current_history_sha256"] = current_h_sha
                try:
                    tmp_m = manifest_path + ".tmp"
                    with open(tmp_m, "w", encoding="utf-8") as f:
                        json.dump(manifest, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_m, manifest_path)
                except OSError:
                    pass
                return {
                    "status": "CONFLICT_PREFIX_MISMATCH",
                    "summary": "Active history prefix does not match source archive; active history preserved.",
                }
        else:
            try:
                os.remove(manifest_path)
            except OSError:
                pass
            return {"status": "UNKNOWN_STATE_REMOVED"}

    # -----------------------------------------------------------------------
    # Query & Storage Metrics
    # -----------------------------------------------------------------------

    def load_history(self, doc_id: str, max_records: Optional[int] = None) -> List[ChangeSet]:
        """
        Reads committed ChangeSets from history.jsonl. Recovers automatically
        from torn lines at the end of the file.
        """
        doc_dir = self.get_document_directory(doc_id)
        history_path = os.path.join(doc_dir, "history.jsonl")

        if not os.path.exists(history_path):
            return []

        records: List[ChangeSet] = []
        with DocumentLock(doc_dir):
            with open(history_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

        valid_lines: List[str] = []
        corrupted_tail = False

        for idx, line in enumerate(lines):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                # Ignore non-changeset metadata events or parse into ChangeSet
                if data.get("event") == "BRANCH_INITIALIZED":
                    continue
                records.append(ChangeSet.from_dict(data))
                valid_lines.append(line)
            except Exception:
                # If this is the last line and invalid, it's a torn write
                if idx == len(lines) - 1:
                    corrupted_tail = True
                else:
                    # Non-tail corrupted line
                    continue

            if max_records and len(records) >= max_records:
                break

        # Repair torn tail if detected
        if corrupted_tail:
            with DocumentLock(doc_dir):
                with open(history_path, "w", encoding="utf-8") as f:
                    f.writelines(valid_lines)
                    f.flush()
                    os.fsync(f.fileno())

        return records

    def get_storage_stats(self, doc_id: str) -> Dict[str, Any]:
        """
        Returns active log size, archive size, archive count, and total bytes.
        Never auto-deletes archives.
        """
        doc_dir = self.get_document_directory(doc_id)
        history_path = os.path.join(doc_dir, "history.jsonl")

        active_bytes = os.path.getsize(history_path) if os.path.exists(history_path) else 0

        archive_bytes = 0
        archive_count = 0
        if os.path.exists(doc_dir):
            for fname in os.listdir(doc_dir):
                if fname.endswith(".jsonl.gz") or fname.startswith("history_epoch_"):
                    fpath = os.path.join(doc_dir, fname)
                    try:
                        archive_bytes += os.path.getsize(fpath)
                        archive_count += 1
                    except OSError:
                        pass

        return {
            "doc_id": doc_id,
            "active_history_bytes": active_bytes,
            "archived_history_bytes": archive_bytes,
            "archive_count": archive_count,
            "total_storage_bytes": active_bytes + archive_bytes,
        }

    @staticmethod
    def _check_in_process_lock(doc_dir: str) -> bool:
        """
        Thread-safely checks if a DocumentLock or SaveAsTransactionLock is currently
        active within this process for the given doc_dir.
        Checks both reentrant locks held by the same thread and locks held by other threads.
        Never alters counters, never acquires or releases lock objects.
        """
        norm_dir = os.path.normcase(os.path.abspath(doc_dir))

        # 1. DocumentLock in-process check
        with _DIR_LOCKS_MUTEX:
            d_state = _DIR_LOCKS.get(norm_dir)
            if d_state:
                if d_state.get("count", 0) > 0 or d_state.get("file_handle") is not None:
                    return True
                # Check if another thread is currently holding the thread_lock
                acquired = d_state["thread_lock"].acquire(blocking=False)
                if not acquired:
                    return True
                d_state["thread_lock"].release()

        # 2. SaveAsTransactionLock in-process check
        with _TX_LOCKS_MUTEX:
            tx_state = _TX_LOCKS.get(norm_dir)
            if tx_state:
                if tx_state.get("count", 0) > 0 or tx_state.get("file_handle") is not None:
                    return True
                acquired = tx_state["thread_lock"].acquire(blocking=False)
                if not acquired:
                    return True
                tx_state["thread_lock"].release()

        return False

    @staticmethod
    def _check_os_file_lock(lock_path: str) -> Tuple[bool, Optional[str]]:
        """
        Strictly read-only, non-mutating check for OS-level byte lock contention.
        Guarantees:
          • Never creates lock_path if it does not exist (zero filesystem writes).
          • Uses 'rb' mode, never 'a+b'.
          • Differentiates real OS lock contention (Errno 13 / EACCES / EDEADLK from msvcrt.locking)
            from file access / PermissionError / general IO errors.
        Returns (is_locked: bool, error_msg: Optional[str]).
        """
        if not os.path.exists(lock_path):
            return False, None

        if not HAS_MSVCRT or not msvcrt:
            return False, None

        fh = None
        try:
            fh = open(lock_path, "rb")
        except PermissionError as pe:
            # File access denied or unreadable; not a verified lock contention
            return False, f"Lock file open permission denied: {pe}"
        except OSError as oe:
            return False, f"Lock file open error: {oe}"

        try:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                # Success: the byte was NOT locked by any other process!
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                return False, None
            except OSError as lock_err:
                # Lock contention in Windows C runtime produces EACCES (13) or EDEADLK (36)
                if lock_err.errno in (errno.EACCES, errno.EDEADLK, 13, 36):
                    return True, None
                return False, f"Unexpected lock syscall error (errno {lock_err.errno}): {lock_err}"
        finally:
            try:
                fh.close()
            except Exception:
                pass

    def get_history_audit(self, doc_id: str) -> Dict[str, Any]:
        """
        Purely read-only audit inspection of a document's history directory.
        Guarantees:
          • Does NOT modify or write any files on disk.
          • Does NOT repair torn lines or trigger pending recovery.
          • Does NOT interfere with active Save or Save As transaction locks.
        Returns a structured dictionary:
          - doc_id: str
          - metadata: Optional[DocumentMetadata]
          - records: List[ChangeSet] (valid committed changesets in chronological order)
          - corrupted_lines: int (count of malformed lines detected in history.jsonl)
          - quarantine_files: List[str] (names of any .conflict_* files in doc_dir)
          - has_active_lock: bool (True if an active in-process or OS lock is held)
          - lock_check_error: Optional[str] (None if clean, or description if file access failed)
          - has_pending_transaction: bool (True if .save_as_manifest.json or .pending_*.json exists)
          - history_file_exists: bool
          - history_file_size: int
        """
        doc_dir = self.get_document_directory(doc_id)
        history_path = os.path.join(doc_dir, "history.jsonl")

        # 1. Zero-side-effect in-process lock check (including same-thread reentrancy)
        has_active_lock = self._check_in_process_lock(doc_dir)
        lock_error: Optional[str] = None

        # 2. Zero-side-effect cross-process OS lock check (only if not already locked in-process)
        if not has_active_lock and os.path.exists(doc_dir):
            doc_lock_file = os.path.join(doc_dir, ".history.lock")
            is_locked, err = self._check_os_file_lock(doc_lock_file)
            if is_locked:
                has_active_lock = True
            elif err:
                lock_error = err

            if not has_active_lock:
                tx_lock_file = os.path.join(doc_dir, ".save_as_transaction.lock")
                is_tx_locked, tx_err = self._check_os_file_lock(tx_lock_file)
                if is_tx_locked:
                    has_active_lock = True
                elif tx_err and not lock_error:
                    lock_error = tx_err

        if lock_error:
            logger.debug("Lock check access warning for %s: %s", doc_id, lock_error)

        meta: Optional[DocumentMetadata] = None
        meta_path = self.get_metadata_path(doc_id)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                meta = DocumentMetadata.from_dict(data)
            except Exception:
                pass

        has_manifest = os.path.exists(os.path.join(doc_dir, ".save_as_manifest.json"))

        quarantine_files: List[str] = []
        has_pending = False
        if os.path.exists(doc_dir):
            try:
                for fname in os.listdir(doc_dir):
                    if fname.startswith(".conflict_"):
                        quarantine_files.append(fname)
                    elif fname.startswith(".pending_") and fname.endswith(".json"):
                        has_pending = True
            except OSError:
                pass

        records: List[ChangeSet] = []
        corrupted_lines = 0
        history_exists = os.path.exists(history_path)
        history_size = os.path.getsize(history_path) if history_exists else 0

        if history_exists and history_size > 0:
            try:
                with open(history_path, "rb") as f:
                    for line_bytes in f:
                        line_trimmed = line_bytes.strip()
                        if not line_trimmed:
                            continue

                        # 1. Strict UTF-8 validation
                        try:
                            line_str = line_trimmed.decode("utf-8")
                        except UnicodeDecodeError:
                            corrupted_lines += 1
                            continue

                        # 2. Strict JSON & ChangeSet model validation
                        try:
                            data = json.loads(line_str)
                            if isinstance(data, dict) and data.get("event") == "BRANCH_INITIALIZED":
                                continue
                            records.append(ChangeSet.from_dict(data))
                        except Exception:
                            corrupted_lines += 1
            except Exception as e:
                logger.warning("Error reading history.jsonl during audit of %s: %s", doc_id, e)

        return {
            "doc_id": doc_id,
            "metadata": meta,
            "records": records,
            "corrupted_lines": corrupted_lines,
            "quarantine_files": quarantine_files,
            "has_active_lock": has_active_lock,
            "lock_check_error": lock_error,
            "has_pending_journal": has_pending,
            "has_save_as_manifest": has_manifest,
            "has_pending_transaction": has_manifest or has_pending,
            "history_file_exists": history_exists,
            "history_file_size": history_size,
        }

    def delete_changeset(self, doc_id: str, save_id: str) -> bool:
        """
        Deletes a specific ChangeSet record by save_id from history.jsonl under DocumentLock.
        Guarantees atomic update and preserves physical XML disk hash in metadata.
        Returns True if deleted, False otherwise.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            history_path = os.path.join(doc_dir, "history.jsonl")
            if not os.path.exists(history_path):
                return False

            lines_to_keep: List[str] = []
            deleted = False
            last_remaining_ts: Optional[str] = None

            with open(history_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                        if isinstance(data, dict):
                            if data.get("save_id") == save_id:
                                deleted = True
                                continue
                            if "save_id" in data and data.get("timestamp"):
                                last_remaining_ts = data.get("timestamp")
                    except Exception:
                        pass
                    lines_to_keep.append(line_str + "\n")

            if not deleted:
                return False

            tmp_path = history_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines_to_keep)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, history_path)

            # Update metadata timestamp while keeping last_saved_content_sha256 aligned with physical XML
            meta = self.load_metadata(doc_id)
            if meta:
                if last_remaining_ts:
                    meta.last_saved_at = last_remaining_ts
                else:
                    meta.last_saved_at = meta.created_at

                if meta.last_known_path and os.path.exists(meta.last_known_path):
                    try:
                        with open(meta.last_known_path, "rb") as df:
                            meta.last_saved_content_sha256 = hashlib.sha256(df.read()).hexdigest()
                    except Exception:
                        pass
                self.save_metadata(meta)

            return True

    def clear_all_history(self, doc_id: str, current_file_path: Optional[str] = None) -> bool:
        """
        Completely clears all history records, journals, and quarantine files for a document.
        Resets metadata to establish the current physical document as a fresh Origin Baseline.
        Guarantees:
          • Protected under DocumentLock.
          • Removes history.jsonl, pending journals, conflict files.
          • Re-anchors metadata to current physical XML file content hash.
        """
        doc_dir = self.get_document_directory(doc_id)
        with DocumentLock(doc_dir):
            history_path = os.path.join(doc_dir, "history.jsonl")
            if os.path.exists(history_path):
                try:
                    os.remove(history_path)
                except OSError:
                    with open(history_path, "w", encoding="utf-8") as f:
                        pass

            # Remove pending files, manifests, and conflicts
            if os.path.exists(doc_dir):
                for fname in os.listdir(doc_dir):
                    if fname.startswith((".pending_", ".conflict_", ".save_as_manifest", ".rotate_manifest")):
                        try:
                            os.remove(os.path.join(doc_dir, fname))
                        except OSError:
                            pass

            meta = self.load_metadata(doc_id)
            target_path = current_file_path or (meta.last_known_path if meta else "")
            now_iso = datetime.now(timezone.utc).isoformat()

            current_sha = ""
            if target_path and os.path.exists(target_path):
                try:
                    with open(target_path, "rb") as f:
                        current_sha = hashlib.sha256(f.read()).hexdigest()
                except Exception:
                    pass

            if not current_sha and meta:
                current_sha = meta.last_saved_content_sha256

            new_meta = DocumentMetadata(
                doc_id=doc_id,
                original_filename=os.path.basename(target_path) if target_path else (meta.original_filename if meta else "document.xml"),
                last_known_path=target_path or (meta.last_known_path if meta else ""),
                created_at=now_iso,
                last_saved_at=now_iso,
                last_saved_content_sha256=current_sha,
                active_epoch=1,
                archived_epochs=[],
            )
            self.save_metadata(new_meta)
            return True
