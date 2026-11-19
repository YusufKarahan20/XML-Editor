"""
XML/history_models.py
---------------------
Data models and serialization schema for the XML Change History system.

All models are fully serializable to and from standard JSON/dict structures.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Any, Dict, List
import json


class ChangeType(str, Enum):
    VALUE_UPDATE = "VALUE_UPDATE"
    ATTR_UPDATE = "ATTR_UPDATE"
    ATTR_INSERT = "ATTR_INSERT"
    ATTR_DELETE = "ATTR_DELETE"
    ELEMENT_INSERT = "ELEMENT_INSERT"
    ELEMENT_DELETE = "ELEMENT_DELETE"
    ELEMENT_RENAME = "ELEMENT_RENAME"
    ELEMENT_MOVE = "ELEMENT_MOVE"
    ORDER_CHANGED = "ORDER_CHANGED"


class NodeType(str, Enum):
    ELEMENT = "element"
    TEXT = "text"
    TAIL = "tail"
    ATTRIBUTE = "attribute"


@dataclass
class ChangeRecord:
    """
    Represents an atomic, fine-grained change within an XML document.
    """
    change_type: ChangeType
    xpath: str
    node_type: NodeType = NodeType.ELEMENT
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_position: Optional[int] = None
    new_position: Optional[int] = None
    attr_name: Optional[str] = None
    tag: Optional[str] = None
    old_tag: Optional[str] = None
    new_tag: Optional[str] = None
    line_number: Optional[int] = None
    old_line_number: Optional[int] = None
    order_changed: bool = False
    from_xpath: Optional[str] = None
    to_xpath: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "change_type": self.change_type.value if isinstance(self.change_type, Enum) else str(self.change_type),
            "xpath": self.xpath,
            "node_type": self.node_type.value if isinstance(self.node_type, Enum) else str(self.node_type),
        }
        if self.old_value is not None:
            data["old_value"] = self.old_value
        if self.new_value is not None:
            data["new_value"] = self.new_value
        if self.old_position is not None:
            data["old_position"] = self.old_position
        if self.new_position is not None:
            data["new_position"] = self.new_position
        if self.attr_name is not None:
            data["attr_name"] = self.attr_name
        if self.tag is not None:
            data["tag"] = self.tag
        if self.old_tag is not None:
            data["old_tag"] = self.old_tag
        if self.new_tag is not None:
            data["new_tag"] = self.new_tag
        if self.line_number is not None:
            data["line_number"] = self.line_number
        if self.old_line_number is not None:
            data["old_line_number"] = self.old_line_number
        if self.order_changed:
            data["order_changed"] = True
        if self.from_xpath is not None:
            data["from_xpath"] = self.from_xpath
        if self.to_xpath is not None:
            data["to_xpath"] = self.to_xpath
        if self.details is not None:
            data["details"] = self.details
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChangeRecord":
        c_type = ChangeType(data["change_type"])
        n_type = NodeType(data.get("node_type", "element"))
        return cls(
            change_type=c_type,
            xpath=data.get("xpath", ""),
            node_type=n_type,
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
            old_position=data.get("old_position"),
            new_position=data.get("new_position"),
            attr_name=data.get("attr_name"),
            tag=data.get("tag"),
            old_tag=data.get("old_tag"),
            new_tag=data.get("new_tag"),
            line_number=data.get("line_number"),
            old_line_number=data.get("old_line_number"),
            order_changed=data.get("order_changed", False),
            from_xpath=data.get("from_xpath"),
            to_xpath=data.get("to_xpath"),
            details=data.get("details"),
        )


@dataclass
class ChangeSet:
    """
    Represents a full transaction/commit of changes committed during a single Save operation.
    """
    save_id: str
    doc_id: str
    target_path: str = ""
    timestamp: str = ""
    previous_content_sha256: Optional[str] = None
    expected_target_sha256: Optional[str] = None
    changes: List[ChangeRecord] = field(default_factory=list)
    target_epoch_id: Optional[str] = None
    summary: Optional[str] = None
    author: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "save_id": self.save_id,
            "doc_id": self.doc_id,
            "target_path": self.target_path,
            "timestamp": self.timestamp,
            "previous_content_sha256": self.previous_content_sha256,
            "expected_target_sha256": self.expected_target_sha256,
            "changes": [c.to_dict() for c in self.changes],
        }
        if self.target_epoch_id:
            data["target_epoch_id"] = self.target_epoch_id
        if self.summary:
            data["summary"] = self.summary
        if self.author:
            data["author"] = self.author
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChangeSet":
        raw_changes = data.get("changes", [])
        changes = [ChangeRecord.from_dict(c) for c in raw_changes]
        return cls(
            save_id=data["save_id"],
            doc_id=data.get("doc_id", ""),
            target_path=data.get("target_path", ""),
            timestamp=data.get("timestamp", ""),
            previous_content_sha256=data.get("previous_content_sha256"),
            expected_target_sha256=data.get("expected_target_sha256"),
            changes=changes,
            target_epoch_id=data.get("target_epoch_id"),
            summary=data.get("summary"),
            author=data.get("author"),
        )


@dataclass
class PendingJournalRecord:
    """
    Represents a pending journal file stored on disk before an XML save completes.
    Used for crash recovery and audit integrity.
    """
    save_id: str
    doc_id: str
    target_path: str
    timestamp: str
    previous_content_sha256: str
    expected_target_sha256: str
    changeset: ChangeSet
    target_epoch_id: Optional[str] = None
    status: str = "PENDING"

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "save_id": self.save_id,
            "doc_id": self.doc_id,
            "target_path": self.target_path,
            "timestamp": self.timestamp,
            "previous_content_sha256": self.previous_content_sha256,
            "expected_target_sha256": self.expected_target_sha256,
            "changeset": self.changeset.to_dict(),
            "status": self.status,
        }
        if self.target_epoch_id:
            data["target_epoch_id"] = self.target_epoch_id
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingJournalRecord":
        changeset = ChangeSet.from_dict(data["changeset"])
        return cls(
            save_id=data["save_id"],
            doc_id=data["doc_id"],
            target_path=data["target_path"],
            timestamp=data["timestamp"],
            previous_content_sha256=data["previous_content_sha256"],
            expected_target_sha256=data["expected_target_sha256"],
            changeset=changeset,
            target_epoch_id=data.get("target_epoch_id"),
            status=data.get("status", "PENDING"),
        )


@dataclass
class DocumentMetadata:
    """
    Tracks metadata for a specific document's history directory.
    """
    doc_id: str
    original_filename: str
    last_known_path: str
    created_at: str
    last_saved_at: str
    active_epoch: int = 1
    archived_epochs: List[Dict[str, Any]] = field(default_factory=list)
    pending_epoch_id: Optional[str] = None
    last_saved_content_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "doc_id": self.doc_id,
            "original_filename": self.original_filename,
            "last_known_path": self.last_known_path,
            "created_at": self.created_at,
            "last_saved_at": self.last_saved_at,
            "active_epoch": self.active_epoch,
            "archived_epochs": self.archived_epochs,
            "last_saved_content_sha256": self.last_saved_content_sha256,
        }
        if self.pending_epoch_id:
            data["pending_epoch_id"] = self.pending_epoch_id
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentMetadata":
        return cls(
            doc_id=data["doc_id"],
            original_filename=data.get("original_filename", ""),
            last_known_path=data.get("last_known_path", ""),
            created_at=data.get("created_at", ""),
            last_saved_at=data.get("last_saved_at", ""),
            active_epoch=data.get("active_epoch", 1),
            archived_epochs=data.get("archived_epochs", []),
            pending_epoch_id=data.get("pending_epoch_id"),
            last_saved_content_sha256=data.get("last_saved_content_sha256", ""),
        )


@dataclass
class SaveAsManifest:
    """
    Two-Phase Save As Transaction State Manifest.
    Stored on disk as .save_as_manifest.json in the target document's history directory.
    Guarantees target history is never altered prior to physical XML write.
    """
    save_id: str
    source_doc_id: str
    target_doc_id: str
    canonical_target_path: str
    source_path: str
    previous_target_exists: bool
    previous_content_sha256: Optional[str]
    expected_target_sha256: str
    target_epoch_id: str
    previous_epoch: int
    initial_history_exists: bool
    initial_history_has_records: bool  # True strictly if physical size > 0
    initial_history_sha256: Optional[str] = None
    changeset: Optional[Dict[str, Any]] = None  # Embedded ChangeSet backup
    state: str = "PREPARED"  # PREPARED, COMMITTED, ABORTED, CONFLICT
    step: str = "PREPARED"   # PREPARED, EPOCH_ARCHIVED, METADATA_UPDATED, LOG_INITIALIZED, COMMITTED
    created_at: str = ""
    archived_filename: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SaveAsManifest":
        return cls(
            save_id=data["save_id"],
            source_doc_id=data["source_doc_id"],
            target_doc_id=data["target_doc_id"],
            canonical_target_path=data["canonical_target_path"],
            source_path=data["source_path"],
            previous_target_exists=data.get("previous_target_exists", True),
            previous_content_sha256=data.get("previous_content_sha256"),
            expected_target_sha256=data["expected_target_sha256"],
            target_epoch_id=data["target_epoch_id"],
            previous_epoch=data.get("previous_epoch", 1),
            initial_history_exists=data.get("initial_history_exists", False),
            initial_history_has_records=data.get("initial_history_has_records", False),
            initial_history_sha256=data.get("initial_history_sha256"),
            changeset=data.get("changeset"),
            state=data.get("state", "PREPARED"),
            step=data.get("step", "PREPARED"),
            created_at=data.get("created_at", ""),
            archived_filename=data.get("archived_filename"),
        )


