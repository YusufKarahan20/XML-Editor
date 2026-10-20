"""
CONVERTER/soap.py — Specialized SOAP XML to JSON Input Converter

Pure Python module for transforming SOAP 1.1 / 1.2 request and response
XML envelopes into clean JSON input structures for backend services.

Design principles:
  • Zero PyQt6 dependencies (fully decoupled from GUI).
  • Immutability: Operates in a strictly read-only manner on lxml Elements.
  • Dynamic operation unwrapping: Extracts business parameters under SOAP Body
    regardless of operation naming (e.g. getAcikIsEmriBBK / getAcikIsEmriBBKResponse).
  • Namespace prefix stripping: Produces clean JSON keys matching service schemas.
  • Controlled type conversions: Preserves string values by default; allows
    explicit configuration of numeric fields without uncontrolled guessing.
  • Faithful xsi:nil handling: Emits null (None) for genuinely nil leaf elements
    while preserving other attributes and child elements without silent loss.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Sequence

from lxml import etree

SOAP_11_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
SOAP_12_ENV_NS = "http://www.w3.org/2003/05/soap-envelope"
SOAP_ENV_NAMESPACES = frozenset({SOAP_11_ENV_NS, SOAP_12_ENV_NS})
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Known numeric fields for service responses (available as an explicit preset)
DEFAULT_SOAP_NUMERIC_FIELDS = frozenset({"ReturnCode", "hataKod", "svuId"})


def is_soap_envelope(element: Any) -> bool:
    """
    Checks if an XML element is a SOAP 1.1 or SOAP 1.2 Envelope.
    """
    if element is None or not hasattr(element, "tag"):
        return False
    if not isinstance(element.tag, str):
        return False
    try:
        qname = etree.QName(element)
        return qname.localname == "Envelope" and qname.namespace in SOAP_ENV_NAMESPACES
    except (ValueError, TypeError):
        return False


# Standard service operation message contract suffixes (case-insensitive)
_SERVICE_MESSAGE_SUFFIXES = (
    "response",
    "request",
    "dsl",
    "ebm",
    "ebo",
    "fault",
)

# Recognized action verbs indicating an RPC / service operation method (case-insensitive)
_SERVICE_OPERATION_VERBS = (
    "get",
    "set",
    "create",
    "update",
    "delete",
    "query",
    "check",
    "find",
    "fetch",
    "search",
    "sync",
    "cancel",
    "execute",
    "process",
    "validate",
    "calculate",
    "inquire",
)

# Strong structural service envelope blocks (composite service headers / request-response envelopes)
_STRONG_SERVICE_CHILD_BLOCKS = {
    "requestinfo",
    "responseinfo",
    "preqinfo",
    "preqres",
    "ticketdetails",
}

# Specific namespace URI / prefix fragments indicating enterprise web service contracts
_SERVICE_NS_FRAGMENTS = (
    "/ebm",
    "/ebo",
    "/extsystem",
    "/services",
    "/ws",
    "/wsdl",
)
_SERVICE_NS_PREFIXES = {"ebm", "ebo", "tt", "ws", "srv"}


def _starts_with_service_verb(name: str) -> bool:
    """
    Checks if a name starts with an operation verb followed by camelCase/PascalCase
    (e.g., 'getHat...', 'createOrder...', 'checkLine...') or an underscore ('get_hat...').
    Prevents false positives on plain words like 'getter', 'gadget', 'creation'.
    """
    name_lower = name.lower()
    for verb in _SERVICE_OPERATION_VERBS:
        if name_lower.startswith(verb) and len(name) > len(verb):
            next_char = name[len(verb)]
            if next_char.isupper() or next_char == "_":
                return True
    return False


def _has_service_namespace(namespace: str, prefix: str) -> bool:
    """
    Checks if the element's namespace URI or prefix specifically indicates
    an enterprise web service contract (e.g. Etiya EBM/EBO, WSDL, WS).
    Avoids matching generic paths like '/schemas' or '/schema'.
    """
    ns_lower = namespace.lower() if namespace else ""
    pref_lower = prefix.lower() if prefix else ""
    return (
        any(frag in ns_lower for frag in _SERVICE_NS_FRAGMENTS)
        or pref_lower in _SERVICE_NS_PREFIXES
    )


def is_service_payload(element: Any) -> bool:
    """
    Checks if an XML element represents a service request or response operation
    payload (e.g. tt:getTroubleTicketStatusResponse, ebm:getHatDondurmaYapilabilirlikDSL,
    ser:createOrderRequest).

    Strict Criteria:
      1. Must be a valid element with child elements (not a leaf or empty tag).
      2. Must have a namespace or namespace prefix (contract-driven schema).
      3. Must satisfy contract-aware service payload criteria without false positives:
         a) Starts with a service operation verb (e.g. 'get...', 'create...', 'check...')
            AND ends with a service suffix ('Response', 'Request', 'DSL', 'EBM', 'EBO', 'Fault')
            OR contains strong service children ('RequestInfo', etc.)
            OR has a dedicated service contract namespace.
         b) Ends with an explicit enterprise message suffix ('EBM', 'EBO', 'Fault')
            with length > 3 AND has service contract namespace or strong service children.
         c) Contains strong composite service envelope blocks (e.g. 'RequestInfo', 'ResponseInfo')
            paired with input/output/details AND has a service contract namespace.

      If evidence is insufficient, defaults to False to preserve the root element.
    """
    if element is None or not hasattr(element, "tag"):
        return False
    if not isinstance(element.tag, str):
        return False

    children = [c for c in element if isinstance(c.tag, str)]
    if not children:
        return False

    try:
        qname = etree.QName(element)
        localname = qname.localname
        namespace = qname.namespace or ""
        prefix = element.prefix or ""
        has_namespace = bool(namespace or prefix)
        if not has_namespace:
            return False

        lname_lower = localname.lower()
        is_verb_op = _starts_with_service_verb(localname)
        has_service_ns = _has_service_namespace(namespace, prefix)

        # Inspect child elements
        child_localnames = {etree.QName(c).localname.lower() for c in children}
        has_strong_child_block = bool(child_localnames & _STRONG_SERVICE_CHILD_BLOCKS)
        has_paired_io = (
            ("input" in child_localnames or "output" in child_localnames)
            and ("requestinfo" in child_localnames or "header" in child_localnames or has_strong_child_block)
        ) or ("result" in child_localnames and len(children) > 1 and (has_strong_child_block or has_service_ns))

        # Rule 1: Operation verb (e.g. getTroubleTicketStatusResponse, getHatDondurmaYapilabilirlikDSL, createOrderRequest, queryResponse)
        if is_verb_op:
            # 1a. Ends with a recognized service message suffix (Response, Request, DSL, EBM, EBO, Fault)
            if lname_lower.endswith(_SERVICE_MESSAGE_SUFFIXES) and len(localname) > 3:
                return True
            # 1b. Has strong service children (RequestInfo, Result, etc.) or service namespace contract
            if has_strong_child_block or has_paired_io or has_service_ns:
                return True

        # Rule 2: Non-verb root ending with explicit Enterprise Business Message / Fault suffixes
        # (e.g. TroubleTicketEBM, ServiceFault) accompanied by service namespace or strong child blocks
        if lname_lower.endswith(("ebm", "ebo", "fault")) and len(localname) > 3:
            if has_service_ns or has_strong_child_block or has_paired_io:
                return True

        # Rule 3: Root containing unambiguous composite service envelope blocks
        # (e.g. RequestInfo + input, or pReqInfo + input) accompanied by service namespace
        if (has_strong_child_block or has_paired_io) and has_service_ns:
            return True

        return False
    except (ValueError, TypeError):
        return False


def extract_soap_payload_elements(
    root: etree._Element, unwrap_operation: bool = True
) -> list[etree._Element]:
    """
    Finds the SOAP Body inside the Envelope and extracts payload elements.

    If unwrap_operation is True and the Body contains a single operation wrapper
    element (e.g. getAcikIsEmriBBK or getAcikIsEmriBBKResponse) that has children,
    unwraps it and returns its child elements. Standard SOAP Fault elements are
    never unwrapped.

    If unwrap_operation is False, returns the direct children of SOAP Body without
    unwrapping the operation element.
    """
    body: etree._Element | None = None
    for child in root:
        if not isinstance(child.tag, str):
            continue
        try:
            q = etree.QName(child)
            if q.localname == "Body" and q.namespace in SOAP_ENV_NAMESPACES:
                body = child
                break
        except (ValueError, TypeError):
            continue

    if body is None:
        raise ValueError("No SOAP Body found in Envelope")

    body_children = [c for c in body if isinstance(c.tag, str)]
    if not unwrap_operation:
        return body_children

    if len(body_children) == 1:
        wrapper = body_children[0]
        try:
            q = etree.QName(wrapper)
            # Do not unwrap standard SOAP Fault
            if q.localname == "Fault" and q.namespace in SOAP_ENV_NAMESPACES:
                return body_children
        except (ValueError, TypeError):
            pass

        wrapper_children = [c for c in wrapper if isinstance(c.tag, str)]
        if wrapper_children:
            return wrapper_children

    return body_children


def _is_xsi_nil(element: etree._Element) -> bool:
    """
    Checks if an element is explicitly nilled via xsi:nil="true" or "1".
    Handles Clark notation ({uri}nil) and URI variations.
    """
    for k, v in element.attrib.items():
        if k.startswith("{"):
            try:
                q = etree.QName(k)
                if q.localname == "nil" and (q.namespace == XSI_NS or "XMLSchema-instance" in (q.namespace or "")):
                    return v.strip().lower() in ("true", "1")
            except (ValueError, TypeError):
                pass
        elif k == "xsi:nil" or k == "nil":
            return v.strip().lower() in ("true", "1")
    return False


def _should_force_list(
    element: etree._Element,
    localname: str,
    path: str,
    force_list: Any,
) -> bool:
    """
    Determines whether a single element should be wrapped in a list.
    Supports set/list/tuple/frozenset and callables.
    """
    if force_list is None:
        return False
    if callable(force_list):
        return bool(force_list(element, path))
    if isinstance(force_list, (set, frozenset, list, tuple)):
        return localname in force_list or path in force_list
    return False


def _group_elements_by_key(
    elements: list[etree._Element],
) -> dict[str, list[etree._Element]]:
    """
    Groups sibling elements by localname while preserving prefixes only
    in case of namespace collisions across different namespace URIs.

    Rules:
      1. If a localname is unique across namespaces, the prefix is stripped
         (clean JSON key, e.g. "ActionDate", "item").
      2. If multiple elements share the same localname but have DIFFERENT
         namespace URIs, each distinct namespace group preserves its prefix
         (e.g. "ns1:detail", "ns2:detail") to prevent semantic merging.
      3. If an element has no explicit prefix or if prefixes collide across
         different URIs, URI disambiguation is used (e.g. "detail{http://uri}")
         so no data is ever silently overwritten.
      4. Repeated siblings with the SAME namespace URI and localname are
         grouped together into the same list (array in JSON).
    """
    groups_by_ns: dict[tuple[str | None, str], list[etree._Element]] = {}
    for elem in elements:
        qname = etree.QName(elem)
        key = (qname.namespace, qname.localname)
        groups_by_ns.setdefault(key, []).append(elem)

    localname_to_uris: dict[str, set[str | None]] = {}
    for (uri, localname) in groups_by_ns:
        localname_to_uris.setdefault(localname, set()).add(uri)

    assigned_keys: dict[str, str | None] = {}
    result: dict[str, list[etree._Element]] = {}

    for (uri, localname), elem_list in groups_by_ns.items():
        first_elem = elem_list[0]
        prefix = first_elem.prefix

        if len(localname_to_uris[localname]) == 1:
            display_key = localname
        else:
            # Collision across different namespace URIs!
            if prefix:
                candidate_key = f"{prefix}:{localname}"
            else:
                candidate_key = f"{localname}{{{uri}}}" if uri else localname

            if candidate_key in assigned_keys and assigned_keys[candidate_key] != uri:
                display_key = f"{candidate_key}{{{uri}}}" if uri else f"{candidate_key}{{default}}"
            else:
                display_key = candidate_key

        assigned_keys[display_key] = uri
        result[display_key] = elem_list

    return result


def _is_soap_mixed_content(element: etree._Element, children: list[etree._Element]) -> bool:
    """
    Detects if an element has mixed content (both child elements and
    meaningful non-whitespace text or tail).
    """
    if not children:
        return False

    if element.text and element.text.strip():
        return True

    for child in children:
        if child.tail and child.tail.strip():
            return True

    return False


def _convert_soap_element(
    element: etree._Element,
    numeric_fields: set[str] | Sequence[str] | None = None,
    force_list: Any = None,
    path: str = "",
) -> Any:
    """
    Recursively transforms a SOAP payload XML element into Python dict/list/primitive.
    """
    localname = etree.QName(element).localname
    children = [c for c in element if isinstance(c.tag, str)]
    has_nil = _is_xsi_nil(element)

    # Extract non-namespace, non-nil attributes
    attrs: dict[str, str] = {}
    for k, v in element.attrib.items():
        if k.startswith("xmlns") or k.startswith("{http://www.w3.org/2000/xmlns/}"):
            continue
        if k.startswith("{"):
            try:
                q = etree.QName(k)
                if q.localname == "nil" and (q.namespace == XSI_NS or "XMLSchema-instance" in (q.namespace or "")):
                    continue
                attr_local = q.localname
            except (ValueError, TypeError):
                attr_local = k.split("}")[-1]
        else:
            if k == "xsi:nil" or k == "nil":
                continue
            attr_local = k.split(":")[-1]
        attrs[f"@{attr_local}"] = v

    # Case A: Mixed Content (Text + Elements intermingled)
    if _is_soap_mixed_content(element, children):
        tokens: list[dict[str, Any]] = []

        # 1. Text preceding the first child (preserve original whitespace without cropping)
        if element.text and element.text.strip():
            tokens.append({"#text": element.text})

        # Precompute keys for children using existing _group_elements_by_key logic
        child_groups = _group_elements_by_key(children)
        elem_to_key: dict[etree._Element, str] = {}
        for k, elem_list in child_groups.items():
            for elem in elem_list:
                elem_to_key[elem] = k

        # 2. Iterate children and their respective tails in document order
        for child in children:
            child_key = elem_to_key.get(child, etree.QName(child).localname)
            child_path = f"{path}/{child_key}" if path else child_key
            child_data = _convert_soap_element(
                child, numeric_fields=numeric_fields, force_list=force_list, path=child_path
            )
            tokens.append({child_key: child_data})

            if child.tail and child.tail.strip():
                tokens.append({"#text": child.tail})

        result: dict[str, Any] = dict(attrs)
        if has_nil:
            result["@nil"] = "true"
        if result:
            result["#content"] = tokens
            return result
        return {"#content": tokens}

    # Case 1: Leaf element (no children)
    if not children:
        if has_nil:
            if attrs:
                # Do NOT silently drop other attributes on nil element
                res = dict(attrs)
                res["@nil"] = "true"
                res["#text"] = None
                return res
            return None

        text = element.text
        is_empty = (text is None) or (isinstance(text, str) and not text.strip())
        if is_empty:
            if attrs:
                return attrs
            return None

        # Scalar text
        val: Any = text
        if numeric_fields and localname in numeric_fields:
            cleaned = text.strip()
            try:
                val = int(cleaned)
            except ValueError:
                try:
                    val = float(cleaned)
                except ValueError:
                    val = text

        if attrs:
            res = dict(attrs)
            res["#text"] = val
            return res
        return val

    # Case 2: Structured element (contains child elements)
    result: dict[str, Any] = dict(attrs)
    if has_nil:
        # Preserve nil attribute indicator if element erroneously contains children
        result["@nil"] = "true"

    child_groups = _group_elements_by_key(children)

    for c_key, c_list in child_groups.items():
        c_local = etree.QName(c_list[0]).localname
        c_path = f"{path}/{c_key}" if path else c_key
        if len(c_list) > 1 or _should_force_list(c_list[0], c_local, c_path, force_list):
            result[c_key] = [
                _convert_soap_element(c, numeric_fields=numeric_fields, force_list=force_list, path=c_path)
                for c in c_list
            ]
        else:
            result[c_key] = _convert_soap_element(
                c_list[0], numeric_fields=numeric_fields, force_list=force_list, path=c_path
            )

    return result


def _parse_source(source: etree._Element | str | bytes | Path | os.PathLike) -> etree._Element:
    """Helper to parse various source formats into an lxml root Element."""
    if isinstance(source, etree._Element):
        return source

    if isinstance(source, (str, Path, os.PathLike)):
        try:
            p = Path(source)
            if p.is_file():
                parser = etree.XMLParser(remove_blank_text=False)
                return etree.parse(str(p), parser).getroot()
        except (OSError, ValueError):
            pass

    if isinstance(source, str):
        return etree.fromstring(source.encode("utf-8"))
    elif isinstance(source, bytes):
        return etree.fromstring(source)

    raise TypeError(f"Unsupported source type: {type(source)}")


def soap_to_dict(
    source: etree._Element | str | bytes | Path,
    numeric_fields: set[str] | Sequence[str] | None = None,
    force_list: Any = None,
    unwrap_operation: bool = True,
) -> dict[str, Any]:
    """
    Transforms a SOAP XML Envelope into a clean Python dictionary.

    Args:
        source: lxml root Element, XML string/bytes, or file path.
        numeric_fields: Explicit set or sequence of field names to cast to int/float.
                        Defaults to None (all values remain strings).
        force_list: Set, sequence, or callable matching paths/localnames to serialize as lists.
        unwrap_operation: Whether to unwrap the operation element under SOAP Body.
                          Defaults to True.

    Returns:
        dict containing the payload elements under the unwrapped operation.
    """
    root = _parse_source(source)
    if not is_soap_envelope(root):
        raise ValueError("Source root element is not a SOAP Envelope")

    payload_elements = extract_soap_payload_elements(root, unwrap_operation=unwrap_operation)

    # Group top-level payload elements by local name (disambiguating on namespace collision)
    groups = _group_elements_by_key(payload_elements)

    result: dict[str, Any] = {}
    for key, elems in groups.items():
        local = etree.QName(elems[0]).localname
        if len(elems) > 1 or _should_force_list(elems[0], local, key, force_list):
            result[key] = [
                _convert_soap_element(e, numeric_fields=numeric_fields, force_list=force_list, path=key)
                for e in elems
            ]
        else:
            result[key] = _convert_soap_element(
                elems[0], numeric_fields=numeric_fields, force_list=force_list, path=key
            )

    return result


def soap_to_json(
    source: etree._Element | str | bytes | Path,
    numeric_fields: set[str] | Sequence[str] | None = None,
    force_list: Any = None,
    unwrap_operation: bool = True,
    indent: int | None = 2,
    ensure_ascii: bool = False,
) -> str:
    """
    Transforms a SOAP XML Envelope into a clean JSON string.

    Args:
        source: lxml root Element, XML string/bytes, or file path.
        numeric_fields: Explicit set or sequence of field names to cast to int/float.
                        Defaults to None (all values remain strings).
        force_list: Set, sequence, or callable matching paths/localnames to serialize as lists.
        unwrap_operation: Whether to unwrap the operation element under SOAP Body.
                          Defaults to True.
        indent: JSON indentation spaces (defaults to 2).
        ensure_ascii: Whether to escape non-ASCII characters (defaults to False).

    Returns:
        Formatted JSON string.
    """
    data = soap_to_dict(
        source,
        numeric_fields=numeric_fields,
        force_list=force_list,
        unwrap_operation=unwrap_operation,
    )
    return json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)


def build_soap_array_detector(
    source: etree._Element | str | bytes | Path,
    unwrap_operation: bool = True,
    additional_rules: Any = None,
) -> Callable[[etree._Element, str], bool]:
    """
    Builds a SmartArrayDetector callable tailored specifically for SOAP payloads.

    Instead of scanning Envelope/Header/Body structural layers, unwraps the payload
    first and scans repeating paths matching the exact paths traversed during
    SOAP conversion (e.g. 'InvoiceList/InvoiceItem').

    Args:
        source: SOAP XML Envelope element, string/bytes, or file path.
        unwrap_operation: Whether to unwrap the operation element under SOAP Body.
        additional_rules: Optional additional set, sequence, or callable rules.

    Returns:
        Callable predicate matching (element, path) -> bool for force_list.
    """
    root = _parse_source(source)
    if not is_soap_envelope(root):
        from CONVERTER.heuristics import build_smart_array_detector
        return build_smart_array_detector(root, additional_rules=additional_rules)

    payload_elements = extract_soap_payload_elements(root, unwrap_operation=unwrap_operation)

    from CONVERTER.heuristics import scan_repeating_paths
    from CONVERTER.xml_to_json import ConversionConfig

    cfg = ConversionConfig(strip_namespaces=True)
    repeating_display_paths: set[str] = set()

    for elem in payload_elements:
        d_paths, _ = scan_repeating_paths(elem, config=cfg)
        repeating_display_paths.update(d_paths)

    def _soap_detector(element: etree._Element, path: str) -> bool:
        if additional_rules:
            if callable(additional_rules):
                if additional_rules(element, path):
                    return True
            elif isinstance(additional_rules, (set, frozenset, list, tuple)):
                qname = etree.QName(element)
                if qname.localname in additional_rules or path in additional_rules:
                    return True

        if path in repeating_display_paths:
            return True
        if "/" in path and path.split("/", 1)[1] in repeating_display_paths:
            return True

        return False

    return _soap_detector
