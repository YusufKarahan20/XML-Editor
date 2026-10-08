from lxml import etree
from SEARCH.result_list import SearchResult

def get_display_tag(element):
    if hasattr(element, "tag") and isinstance(element.tag, str):
        qname = etree.QName(element)
        return f"{element.prefix}:{qname.localname}" if element.prefix else qname.localname
    return str(element.tag)

def build_human_readable_location(element):
    path_parts = []
    current = element
    while current is not None:
        tag = get_display_tag(current)
        
        # Calculate sibling index if needed
        parent = current.getparent()
        if parent is not None:
            # find how many siblings have the same tag
            same_tag_siblings = []
            for child in parent:
                if get_display_tag(child) == tag:
                    same_tag_siblings.append(child)
            
            if len(same_tag_siblings) > 1:
                # 1-based index
                index = same_tag_siblings.index(current) + 1
                tag = f"{tag}[{index}]"
                
        path_parts.append(tag)
        current = parent
        
    path_parts.reverse()
    return " > ".join(path_parts)

def search_xml(root_element, query: str) -> list[SearchResult]:
    results = []
    if not query or root_element is None:
        return results
        
    query_lower = query.lower()
    
    # We will do a full recursive traversal
    # to maintain document order easily
    def traverse(el):
        line_no = el.sourceline if hasattr(el, 'sourceline') else None
        
        # 1. Check Element tag match
        tag_str = get_display_tag(el)
        if query_lower in tag_str.lower():
            location = build_human_readable_location(el)
            results.append(SearchResult(
                matched_text=tag_str,
                tag=tag_str,
                match_type="Element",
                line_number=line_no,
                location=location,
                element=el
            ))
            
        # 2. Check Element value match
        if el.text and el.text.strip():
            text_str = el.text.strip()
            if query_lower in text_str.lower():
                location = build_human_readable_location(el)
                results.append(SearchResult(
                    matched_text=text_str,
                    tag=tag_str,
                    match_type="Value",
                    line_number=line_no,
                    location=location,
                    element=el
                ))
                
        # Traverse children
        for child in el:
            if isinstance(child.tag, str): # Skip comments/PIs
                traverse(child)

    traverse(root_element)
    return results
