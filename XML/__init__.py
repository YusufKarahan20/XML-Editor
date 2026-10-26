"""
XML package — Core XML document model and operations.
"""

from XML.document_session import (
    XmlDocumentSession,
    SyntaxErrorInfo,
    detect_xml_declaration,
    save_xml_atomic,
)

__all__ = [
    "XmlDocumentSession",
    "SyntaxErrorInfo",
    "detect_xml_declaration",
    "save_xml_atomic",
]
