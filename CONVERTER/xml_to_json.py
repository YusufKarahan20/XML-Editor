"""
CONVERTER/xml_to_json.py — Core XML to JSON Conversion Engine

Pure Python module for converting XML documents (via lxml.etree) into Python
data structures (dict/list/primitive) and JSON strings.

Design principles:
  • Zero PyQt6 dependencies (completely decoupled from GUI).
  • Immutability: Operates in a strictly read-only manner on lxml Elements.
  • Rich namespace resolution (preserving prefix, handling URI collisions, scoped xmlns).
  • Ordered token sequence for mixed-content elements (#content).
  • Support for Element, XML string/bytes, and file paths.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from lxml import etree

# Well-known W3C XML namespaces that are implicitly bound
_WELL_KNOWN_NS = {
    "http://www.w3.org/XML/1998/namespace": "xml",
    "http://www.w3.org/2000/xmlns/": "xmlns",
    "http://www.w3.org/2001/XMLSchema-instance": "xsi",
}


@dataclass(frozen=True)
class ConversionConfig:
    """Configuration options for XML to JSON conversion."""
    include_root: bool = True
    indent: int | None = 2
    ensure_ascii: bool = False
    strip_namespaces: bool = False
    empty_element_value: Any = None  # null in JSON
    mixed_content_key: str = "#content"
    text_key: str = "#text"
    attr_prefix: str = "@"
    force_list: frozenset[str] | set[str] | Sequence[str] | Callable[[etree._Element, str], bool] | None = None

    def __post_init__(self) -> None:
        if self.force_list is not None and not callable(self.force_list):
            if not isinstance(self.force_list, frozenset):
                object.__setattr__(self, "force_list", frozenset(self.force_list))


class XmlToJsonConverter:
    """Core converter for transforming XML trees and sources into JSON."""

    def __init__(self, config: ConversionConfig | None = None) -> None:
        self.config = config or ConversionConfig()

    # ------------------------------------------------------------------
    # Public High-Level API
    # ------------------------------------------------------------------
    def convert(
        self,
        source: etree._Element | str | bytes | os.PathLike,
        config: ConversionConfig | None = None,
    ) -> str:
        """
        Convert an XML source into a formatted JSON string.

        :param source: lxml Element, XML string/bytes, or file path.
        :param config: Optional override for conversion configuration.
        :return: JSON formatted string.
        """
        cfg = config or self.config
        data = self.element_to_data(source, cfg)
        return self.data_to_json(data, cfg)

    def element_to_data(
        self,
        source: etree._Element | str | bytes | os.PathLike,
        config: ConversionConfig | None = None,
    ) -> dict[str, Any] | list[Any] | str | None:
        """
        Convert an XML source into Python native data structures (dict, list, str, None).

        :param source: lxml Element, XML string/bytes, or file path.
        :param config: Optional override for conversion configuration.
        :return: Native Python representation.
        """
        cfg = config or self.config
        root = self._resolve_root(source)

        if root is None:
            raise ValueError("Root element is None; cannot convert empty source.")

        root_key = self._get_element_key(root, cfg)
        root_data = self._convert_element(root, cfg, current_path=root_key)

        if cfg.include_root:
            return {root_key: root_data}
        return root_data

    def data_to_json(
        self,
        data: Any,
        config: ConversionConfig | None = None,
    ) -> str:
        """
        Serialize Python data structure to a JSON string.
        """
        cfg = config or self.config
        return json.dumps(
            data,
            indent=cfg.indent,
            ensure_ascii=cfg.ensure_ascii,
        )

    # ------------------------------------------------------------------
    # Source Resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_root(source: etree._Element | str | bytes | os.PathLike) -> etree._Element:
        """Resolves input source into an lxml._Element without mutating anything."""
        if isinstance(source, etree._Element):
            return source

        if isinstance(source, (Path, os.PathLike)):
            tree = etree.parse(str(source))
            return tree.getroot()

        if isinstance(source, str):
            # Check if source is an existing file path
            if "\n" not in source and len(source) < 1024 and os.path.exists(source) and os.path.isfile(source):
                tree = etree.parse(source)
                return tree.getroot()
            # Otherwise parse as raw XML string
            return etree.fromstring(source.encode("utf-8"))

        if isinstance(source, bytes):
            return etree.fromstring(source)

        raise TypeError(f"Unsupported XML source type: {type(source).__name__}")

    # ------------------------------------------------------------------
    # Recursive Element Conversion
    # ------------------------------------------------------------------
    def _convert_element(
        self,
        element: etree._Element,
        config: ConversionConfig,
        current_path: str = "",
    ) -> Any:
        """Recursively converts an lxml Element into Python objects."""
        # 1. Filter out comments and processing instructions
        if not isinstance(element.tag, str):
            return None

        # 2. Extract attributes & scoped namespace declarations
        attrs = self._extract_attributes(element, config)
        ns_decls = self._extract_scoped_namespaces(element, config)
        meta_dict: dict[str, Any] = {}
        meta_dict.update(ns_decls)
        meta_dict.update(attrs)

        # 3. Check for child element nodes (excluding comments/PIs)
        children = [child for child in element if isinstance(child.tag, str)]

        # --------------------------------------------------------------
        # Case A: Mixed Content (Text + Elements intermingled)
        # --------------------------------------------------------------
        if self._is_mixed_content(element, children):
            ordered_content = self._build_mixed_content(
                element, children, config, current_path=current_path
            )
            if meta_dict:
                result = dict(meta_dict)
                result[config.mixed_content_key] = ordered_content
                return result
            return {config.mixed_content_key: ordered_content}

        # --------------------------------------------------------------
        # Case B: Leaf Element (No child element tags)
        # --------------------------------------------------------------
        if not children:
            raw_text = element.text

            # Empty element: <item/> or <item></item>
            if raw_text is None:
                if meta_dict:
                    return meta_dict
                return config.empty_element_value

            # Element containing text
            # If there are metadata (attributes or namespace declarations)
            if meta_dict:
                result = dict(meta_dict)
                if raw_text:
                    result[config.text_key] = raw_text
                return result

            # Scalar text element without attributes/namespaces
            return raw_text

        # --------------------------------------------------------------
        # Case C: Structured Element (Contains child elements, no mixed text)
        # --------------------------------------------------------------
        result_dict: dict[str, Any] = dict(meta_dict)

        # Group child elements by (prefix, uri, localname) while detecting collisions
        child_groups = self._group_children(children, config)

        for key, child_list in child_groups.items():
            first_child = child_list[0]
            child_path = f"{current_path}/{key}" if current_path else key

            if len(child_list) > 1 or self._should_force_list(
                first_child, key, child_path, config
            ):
                result_dict[key] = [
                    self._convert_element(child, config, current_path=child_path)
                    for child in child_list
                ]
            else:
                result_dict[key] = self._convert_element(
                    child_list[0], config, current_path=child_path
                )

        return result_dict

    # ------------------------------------------------------------------
    # Namespace & Key Helpers
    # ------------------------------------------------------------------
    def _get_element_key(self, element: etree._Element, config: ConversionConfig) -> str:
        """Determines the dictionary key for an element."""
        if not isinstance(element.tag, str):
            return str(element.tag)

        qname = etree.QName(element)
        localname = qname.localname

        if config.strip_namespaces:
            return localname

        if element.prefix:
            return f"{element.prefix}:{localname}"

        return localname

    def _extract_attributes(
        self, element: etree._Element, config: ConversionConfig
    ) -> dict[str, str]:
        """Extracts attributes, formatting their keys with '@' prefix and resolving namespaces."""
        attrs: dict[str, str] = {}
        for raw_key, val in element.attrib.items():
            if config.strip_namespaces:
                local_key = etree.QName(raw_key).localname
                attrs[f"{config.attr_prefix}{local_key}"] = val
                continue

            # Check if attribute has namespace (Clark notation: {uri}name)
            if raw_key.startswith("{"):
                attr_qname = etree.QName(raw_key)
                uri = attr_qname.namespace
                local_attr = attr_qname.localname

                # 1. Check well-known W3C namespaces
                prefix = _WELL_KNOWN_NS.get(uri)

                # 2. Check element's nsmap
                if not prefix:
                    for p, u in element.nsmap.items():
                        if u == uri and p is not None:
                            prefix = p
                            break

                if prefix:
                    attr_key = f"{prefix}:{local_attr}"
                else:
                    attr_key = local_attr
            else:
                attr_key = raw_key

            attrs[f"{config.attr_prefix}{attr_key}"] = val

        return attrs

    def _extract_scoped_namespaces(
        self, element: etree._Element, config: ConversionConfig
    ) -> dict[str, str]:
        """
        Extracts namespace declarations defined or overridden *specifically*
        at this element, preventing redundant repetitive @xmlns at every node.
        """
        if config.strip_namespaces:
            return {}

        parent = element.getparent()
        parent_nsmap = parent.nsmap if parent is not None else {}

        decls: dict[str, str] = {}
        for prefix, uri in element.nsmap.items():
            # Check if this prefix is newly introduced or overridden compared to parent
            if parent_nsmap.get(prefix) != uri:
                if prefix is None:
                    decls[f"{config.attr_prefix}xmlns"] = uri
                else:
                    decls[f"{config.attr_prefix}xmlns:{prefix}"] = uri

        return decls

    def _group_children(
        self, children: list[etree._Element], config: ConversionConfig
    ) -> dict[str, list[etree._Element]]:
        """
        Groups sibling elements by (prefix, uri, localname).

        Safety:
          1. Elements with distinct prefixes (e.g. a:item and b:item) are preserved
             as separate keys even if they share the same URI.
          2. If two distinct namespace URIs produce the exact same display key
             (e.g. same prefix bound to different URIs in different child scopes),
             they are disambiguated with URI qualifier so data is never merged incorrectly.
        """
        # Step 1: Map (prefix, uri, localname) -> list of elements
        expanded_groups: dict[tuple[str | None, str | None, str], list[etree._Element]] = {}
        for child in children:
            qname = etree.QName(child)
            key_tuple = (child.prefix, qname.namespace, qname.localname)
            expanded_groups.setdefault(key_tuple, []).append(child)

        # Step 2: Assign display keys and detect URI collisions on identical display keys
        display_key_to_uris: dict[str, set[str | None]] = {}
        for (prefix, uri, localname), child_list in expanded_groups.items():
            first_child = child_list[0]
            d_key = self._get_element_key(first_child, config)
            display_key_to_uris.setdefault(d_key, set()).add(uri)

        result: dict[str, list[etree._Element]] = {}
        for (prefix, uri, localname), child_list in expanded_groups.items():
            first_child = child_list[0]
            d_key = self._get_element_key(first_child, config)

            # If there's an actual URI collision on the same display key:
            if len(display_key_to_uris.get(d_key, set())) > 1 and uri is not None:
                final_key = f"{d_key}{{{uri}}}"
            else:
                final_key = d_key

            result[final_key] = child_list

        return result

    # ------------------------------------------------------------------
    # Mixed Content Handling
    # ------------------------------------------------------------------
    @staticmethod
    def _is_mixed_content(
        element: etree._Element, children: list[etree._Element]
    ) -> bool:
        """
        Detects if an element has mixed content (both child elements and
        meaningful non-whitespace text or tail).
        """
        if not children:
            return False

        # Has text before first child
        if element.text and element.text.strip():
            return True

        # Has tail text after any child
        for child in children:
            if child.tail and child.tail.strip():
                return True

        return False

    def _build_mixed_content(
        self,
        element: etree._Element,
        children: list[etree._Element],
        config: ConversionConfig,
        current_path: str = "",
    ) -> list[dict[str, Any]]:
        """
        Builds an ordered token sequence preserving the exact document order
        of text fragments and embedded elements.
        """
        tokens: list[dict[str, Any]] = []

        # 1. Text preceding the first child
        if element.text and element.text.strip():
            tokens.append({config.text_key: element.text})

        # 2. Iterate children and their respective tails
        for child in children:
            child_key = self._get_element_key(child, config)
            child_path = f"{current_path}/{child_key}" if current_path else child_key
            child_data = self._convert_element(child, config, current_path=child_path)
            tokens.append({child_key: child_data})

            if child.tail and child.tail.strip():
                tokens.append({config.text_key: child.tail})

        return tokens

    # ------------------------------------------------------------------
    # Force List Matching Helper
    # ------------------------------------------------------------------
    def _should_force_list(
        self,
        element: etree._Element,
        key: str,
        path: str,
        config: ConversionConfig,
    ) -> bool:
        """
        Determines whether an element group should be serialized as a list
        even if it contains only a single element.

        Supported matching criteria:
          1. Callable: rule(element, path) -> bool
          2. Full XML path with root: e.g. 'root/a/item'
          3. Relative XML path without root: e.g. 'a/item'
          4. Exact Clark notation: e.g. '{http://uri}item'
          5. Prefixed tag name: e.g. 'ns:item'
          6. Local tag name: e.g. 'item'
          7. Output key: e.g. 'key'
        """
        rules = config.force_list
        if not rules:
            return False

        if callable(rules):
            return bool(rules(element, path))

        qname = etree.QName(element)
        localname = qname.localname
        clark_name = element.tag  # e.g. "{http://uri}item"
        prefix_name = f"{element.prefix}:{localname}" if element.prefix else localname

        candidates = {
            path,
            clark_name,
            prefix_name,
            key,
            localname,
        }

        # Also support path without root: e.g. if path is 'root/a/item', relative is 'a/item'
        if "/" in path:
            rel_path = path.split("/", 1)[1]
            candidates.add(rel_path)

        return not candidates.isdisjoint(rules)


# ----------------------------------------------------------------------
# Module-level convenience functions
# ----------------------------------------------------------------------
def xml_to_json(
    source: etree._Element | str | bytes | os.PathLike,
    config: ConversionConfig | None = None,
    **kwargs: Any,
) -> str:
    """Convenience helper to convert XML directly to a JSON string."""
    cfg = config or ConversionConfig(**kwargs)
    converter = XmlToJsonConverter(cfg)
    return converter.convert(source, cfg)


def xml_to_dict(
    source: etree._Element | str | bytes | os.PathLike,
    config: ConversionConfig | None = None,
    **kwargs: Any,
) -> dict[str, Any] | list[Any] | str | None:
    """Convenience helper to convert XML directly to a Python native data structure."""
    cfg = config or ConversionConfig(**kwargs)
    converter = XmlToJsonConverter(cfg)
    return converter.element_to_data(source, cfg)
