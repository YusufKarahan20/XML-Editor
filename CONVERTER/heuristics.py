"""
CONVERTER/heuristics.py — Smart Array Detection & Heuristics for XML to JSON

Provides automated array detection without hardcoding domain-specific element names.
Features:
  1. Document Pre-Scan: Analyzes the XML tree to detect structural paths that repeat
     anywhere in the document, ensuring single-instance siblings of the same path
     are serialized consistently as JSON arrays.
  2. Safe Heuristics: Optional rule matching for explicit collection patterns
     (e.g., 'CustomerList' -> 'Customer') without error-prone generic plural stemming.
  3. Seamless integration with ConversionConfig.force_list callable protocol.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence
from lxml import etree

from CONVERTER.xml_to_json import ConversionConfig


# Explicit collection container suffixes considered safe for heuristic inference
_SAFE_CONTAINER_SUFFIXES = ("list", "array", "collection", "set")


def _get_element_key(element: etree._Element, config: ConversionConfig) -> str:
    """Helper matching XmlToJsonConverter._get_element_key."""
    if not isinstance(element.tag, str):
        return str(element.tag)

    qname = etree.QName(element)
    localname = qname.localname

    if config.strip_namespaces:
        return localname

    if element.prefix:
        return f"{element.prefix}:{localname}"

    return localname


def is_safe_collection_naming(parent_tag: str, child_tag: str) -> bool:
    """
    Determines if parent_tag is an explicit collection wrapper of child_tag.
    Only checks strict, unambiguous patterns like 'CustomerList' -> 'Customer'.
    Does NOT do generic English plural stemming (avoids Status/Code, Address/Street false positives).
    """
    if not parent_tag or not child_tag:
        return False

    p_local = etree.QName(parent_tag).localname.lower()
    c_local = etree.QName(child_tag).localname.lower()

    for suffix in _SAFE_CONTAINER_SUFFIXES:
        if p_local.endswith(suffix):
            prefix = p_local[: -len(suffix)].rstrip("_-")
            if prefix == c_local:
                return True

    return False


def scan_repeating_paths(
    root: etree._Element,
    config: ConversionConfig | None = None,
) -> tuple[set[str], set[str]]:
    """
    Pre-scans the XML document tree to identify all structural paths and Clark-notation paths
    where child elements repeat (count > 1) under the same parent element.

    Returns:
      (repeating_display_paths, repeating_clark_paths)
    """
    repeating_display_paths: set[str] = set()
    repeating_clark_paths: set[str] = set()

    if root is None or not isinstance(root.tag, str):
        return repeating_display_paths, repeating_clark_paths

    cfg = config or ConversionConfig()

    def _traverse(element: etree._Element, display_path: str, clark_path: str):
        children = [c for c in element if isinstance(c.tag, str)]
        if not children:
            return

        # Group children by their expanded identity (uri, localname, prefix)
        # to strictly respect namespaces
        group_counts: dict[tuple[str | None, str, str | None], int] = {}
        first_elements: dict[tuple[str | None, str, str | None], etree._Element] = {}

        for child in children:
            qname = etree.QName(child)
            key_tuple = (qname.namespace, qname.localname, child.prefix)
            group_counts[key_tuple] = group_counts.get(key_tuple, 0) + 1
            if key_tuple not in first_elements:
                first_elements[key_tuple] = child

        for key_tuple, count in group_counts.items():
            child_elem = first_elements[key_tuple]
            child_display_key = _get_element_key(child_elem, cfg)
            child_clark_key = child_elem.tag  # e.g. "{http://uri}name" or "name"

            child_display_path = (
                f"{display_path}/{child_display_key}" if display_path else child_display_key
            )
            child_clark_path = (
                f"{clark_path}/{child_clark_key}" if clark_path else child_clark_key
            )

            if count > 1:
                repeating_display_paths.add(child_display_path)
                repeating_clark_paths.add(child_clark_path)

                # Also add relative paths without root for flexible matching
                if "/" in child_display_path:
                    repeating_display_paths.add(child_display_path.split("/", 1)[1])
                if "/" in child_clark_path:
                    repeating_clark_paths.add(child_clark_path.split("/", 1)[1])

            # Recurse down all children in this group
            for child in children:
                cq = etree.QName(child)
                if (cq.namespace, cq.localname, child.prefix) == key_tuple:
                    _traverse(child, child_display_path, child_clark_path)

    root_display_key = _get_element_key(root, cfg)
    root_clark_key = root.tag
    _traverse(root, root_display_key, root_clark_key)

    return repeating_display_paths, repeating_clark_paths


class SmartArrayDetector:
    """
    Callable predicate suitable for passing as ConversionConfig.force_list.
    Combines:
      - Document pre-scan (repeating structural path frequency)
      - Safe container heuristics (e.g. List suffix matching)
      - User-provided explicit additional rules
    """

    def __init__(
        self,
        root: etree._Element | None = None,
        use_prescan: bool = True,
        use_heuristics: bool = False,
        additional_rules: set[str] | frozenset[str] | Sequence[str] | Callable[[etree._Element, str], bool] | None = None,
        config: ConversionConfig | None = None,
    ) -> None:
        self.use_prescan = use_prescan
        self.use_heuristics = use_heuristics
        self.additional_rules = additional_rules
        self.config = config or ConversionConfig()

        self.repeating_display_paths: set[str] = set()
        self.repeating_clark_paths: set[str] = set()

        if self.use_prescan and root is not None and isinstance(root, etree._Element):
            self.repeating_display_paths, self.repeating_clark_paths = scan_repeating_paths(
                root, self.config
            )

    def __call__(self, element: etree._Element, path: str) -> bool:
        """
        Invoked by XmlToJsonConverter for each element group when deciding
        whether a single-element group must be serialized as an array.
        """
        # 1. Document pre-scan match (deterministic evidence observed in the document)
        if self.use_prescan:
            if path in self.repeating_display_paths:
                return True
            if "/" in path and path.split("/", 1)[1] in self.repeating_display_paths:
                return True

            # Clark path check
            clark_path = self._build_clark_path(element)
            if clark_path in self.repeating_clark_paths:
                return True
            if "/" in clark_path and clark_path.split("/", 1)[1] in self.repeating_clark_paths:
                return True

        # 2. Safe container naming heuristics (e.g. 'CustomerList' -> 'Customer')
        if self.use_heuristics:
            parent = element.getparent()
            if parent is not None and isinstance(parent.tag, str) and isinstance(element.tag, str):
                if is_safe_collection_naming(parent.tag, element.tag):
                    return True

        # 3. Additional user-specified rules
        if self.additional_rules:
            if callable(self.additional_rules):
                if self.additional_rules(element, path):
                    return True
            else:
                qname = etree.QName(element)
                candidates = {
                    path,
                    element.tag,
                    qname.localname,
                    f"{element.prefix}:{qname.localname}" if element.prefix else qname.localname,
                }
                if "/" in path:
                    candidates.add(path.split("/", 1)[1])
                if not candidates.isdisjoint(self.additional_rules):
                    return True

        return False

    @staticmethod
    def _build_clark_path(element: etree._Element) -> str:
        """Builds full hierarchical path using Clark notation tags ({uri}tag)."""
        parts: list[str] = []
        curr: etree._Element | None = element
        while curr is not None and isinstance(curr.tag, str):
            parts.append(curr.tag)
            curr = curr.getparent()
        parts.reverse()
        return "/".join(parts)


def build_smart_array_detector(
    root: etree._Element | None = None,
    use_prescan: bool = True,
    use_heuristics: bool = False,
    additional_rules: set[str] | frozenset[str] | Sequence[str] | Callable[[etree._Element, str], bool] | None = None,
    config: ConversionConfig | None = None,
) -> SmartArrayDetector:
    """
    Factory function returning a configured SmartArrayDetector callable.
    """
    return SmartArrayDetector(
        root=root,
        use_prescan=use_prescan,
        use_heuristics=use_heuristics,
        additional_rules=additional_rules,
        config=config,
    )
