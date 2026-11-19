"""
XML/path_utils.py
-----------------
Independent XPath and structural addressing utilities for XML Analyzer.

Provides deterministic, canonical XPath construction for elements, attributes,
and text nodes across in-memory and serialized XML trees without external dependencies.
Compatible with GUI/grid_view.py:get_element_xml_path().
"""

from typing import Optional
from lxml import etree


def resolve_display_tag(element: Optional[etree._Element]) -> str:
    """
    Resolves the human-readable display tag for an element.
    If the element has a bound prefix, returns 'prefix:localname',
    otherwise returns 'localname'. If invalid, returns empty string.
    """
    if element is None or not isinstance(getattr(element, "tag", None), str):
        return ""
    try:
        qname = etree.QName(element)
        return f"{element.prefix}:{qname.localname}" if element.prefix else qname.localname
    except Exception:
        return str(getattr(element, "tag", ""))


def get_element_xml_path(
    element: Optional[etree._Element],
    expected_root: Optional[etree._Element] = None
) -> str:
    """
    Constructs a canonical, human-readable XML path for the given lxml element.
    Format: /Root/Customer/Address/City
    If identical tag siblings (same expanded tag in DOM) exist under a parent,
    a 1-based index is appended: e.g. /Root/Orders/Order[2]/Items/Item[1]/Price
    Namespace prefixes are preserved using the element's bound prefix if present:
    e.g. /soapenv:Envelope/soapenv:Body/ns1:Order
    If expected_root is provided, verifies that element is connected to expected_root.
    Returns empty string if element is None, detached from expected_root, or invalid.

    100% equivalent to GUI.grid_view.get_element_xml_path().
    """
    if element is None or not isinstance(getattr(element, "tag", None), str):
        return ""

    path_parts = []
    current = element
    reached_root = False

    while current is not None and isinstance(getattr(current, "tag", None), str):
        # 1. Resolve display tag with prefix if bound, else localname
        qname = etree.QName(current)
        display_tag = f"{current.prefix}:{qname.localname}" if current.prefix else qname.localname

        # 2. Sibling indexing: only compare elements with the EXACT same expanded tag (child.tag == current.tag)
        parent = current.getparent()
        if parent is not None:
            same_tag_siblings = [
                ch for ch in parent
                if isinstance(getattr(ch, "tag", None), str) and ch.tag == current.tag
            ]
            if len(same_tag_siblings) > 1:
                index = same_tag_siblings.index(current) + 1
                display_tag = f"{display_tag}[{index}]"

        path_parts.append(display_tag)

        if expected_root is not None and current is expected_root:
            reached_root = True
            break

        current = parent

    if expected_root is not None and not reached_root:
        return ""

    path_parts.reverse()
    return "/" + "/".join(path_parts) if path_parts else ""


def build_element_xpath(
    element: Optional[etree._Element],
    expected_root: Optional[etree._Element] = None
) -> str:
    """
    Alias for get_element_xml_path for standard naming.
    """
    return get_element_xml_path(element, expected_root=expected_root)


def build_attribute_xpath(
    element: Optional[etree._Element],
    attr_name: str,
    expected_root: Optional[etree._Element] = None
) -> str:
    """
    Constructs an XPath addressing a specific attribute on an element.
    Example: /Root/Customer/@id
    """
    elem_path = get_element_xml_path(element, expected_root=expected_root)
    if not elem_path or not attr_name:
        return ""
    return f"{elem_path}/@{attr_name}"


def build_text_xpath(
    element: Optional[etree._Element],
    expected_root: Optional[etree._Element] = None
) -> str:
    """
    Constructs a standard XPath addressing the immediate text node of an element.
    Example: /Root/Description/text()[1]
    """
    elem_path = get_element_xml_path(element, expected_root=expected_root)
    if not elem_path:
        return ""
    return f"{elem_path}/text()[1]"


def build_tail_xpath(
    element: Optional[etree._Element],
    expected_root: Optional[etree._Element] = None
) -> str:
    """
    Constructs a canonical locator addressing the tail text following an element.
    Uses fragment representation: /Root/p/b#tail
    """
    elem_path = get_element_xml_path(element, expected_root=expected_root)
    if not elem_path:
        return ""
    return f"{elem_path}#tail"

