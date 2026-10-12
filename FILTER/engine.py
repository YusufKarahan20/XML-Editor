"""
FILTER/engine.py — Filter Logic Engine

Extracts dynamic hierarchical path options from the XML tree and applies filter conditions.
Operates strictly on original lxml element references without modifying the tree.
"""

def _strip_ns(tag: str) -> str:
    """Helper to remove namespace from tag names."""
    if tag and "}" in tag:
        return tag.split("}", 1)[1]
    return tag

def get_targets(root) -> list[str]:
    """
    Returns the names of the direct children of the XML root as Targets.
    (e.g. CustomerList, EmployeeList, Summary).
    """
    if root is None:
        return []
        
    targets = []
    for child in root:
        if isinstance(child.tag, str):
            tag = _strip_ns(child.tag)
            if tag not in targets:
                targets.append(tag)
                
    return targets

def _find_all_targets(root, target_identity: str) -> list:
    """
    Finds all direct children of the root that match the target_identity.
    """
    if root is None or not target_identity:
        return []
        
    targets = []
    for child in root:
        if isinstance(child.tag, str) and _strip_ns(child.tag) == target_identity:
            targets.append(child)
            
    return targets

def _get_nodes_for_path(root, target_identity: str, path: list[str]) -> list:
    """
    Internal helper to find all descendant nodes for a specific target and sub-path.
    Returns a list of lxml elements matching the exact path structure.
    """
    if root is None or not target_identity:
        return []
        
    current_nodes = _find_all_targets(root, target_identity)
            
    # 2. Traverse down the path
    for level_name in path:
        next_nodes = []
        for node in current_nodes:
            for child in node:
                if isinstance(child.tag, str) and _strip_ns(child.tag) == level_name:
                    next_nodes.append(child)
        current_nodes = next_nodes
        
    return current_nodes

def get_next_level(root, target_identity: str, path: list[str]) -> list[str]:
    """
    Given a target and a path beneath it, finds all available unique child tag names 
    across all matching elements.
    If the elements matching the path have NO children (they are leaf nodes), returns [].
    """
    nodes = _get_nodes_for_path(root, target_identity, path)
    
    # Check if this is a leaf level (no children anywhere among these nodes)
    # A node is a container if it has at least one child element.
    has_children = False
    for node in nodes:
        if len(node) > 0:
            has_children = True
            break
            
    if not has_children:
        return [] # Leaf node!
        
    # It's a container node level, return unique child tags
    next_levels = set()
    for node in nodes:
        for child in node:
            if isinstance(child.tag, str):
                next_levels.add(_strip_ns(child.tag))
                
    return sorted(list(next_levels))

def get_values_for_path(root, target_identity: str, path: list[str]) -> list[str]:
    """
    Returns a sorted list of unique text values for a given Target -> Path combination.
    Empty values are ignored.
    """
    if not path:
        return []
        
    nodes = _get_nodes_for_path(root, target_identity, path)
    values = set()
    
    for node in nodes:
        text = node.text
        if text and text.strip():
            values.add(text.strip())
            
    return sorted(list(values))

def apply_filter(root, target_identity: str, path: list[str], condition: str = "", value: str = "") -> tuple[list, int]:
    """
    Returns a tuple of (matched_elements, total_match_count).
    matched_elements is a list of original lxml Elements that contain the matches.
    Does not copy or modify the XML.

    Supports:
    1. Unconditional Target selection (empty path and no condition): returns all records of the target.
    2. Unconditional Field selection (path provided, no condition): returns all records containing the path.
    3. Conditional filtering (condition and value provided): filters matching records.
    """
    if root is None or not target_identity:
        return [], 0

    target_nodes = _find_all_targets(root, target_identity)
    if not target_nodes:
        return [], 0

    has_condition = bool(condition and condition not in ("", "(Tümü / Filtresiz)", "(All / None)", "None") and value is not None and str(value).strip() != "")

    # Case 1 & 2: No active condition
    if not has_condition:
        if not path:
            # Return all direct child records of the target (e.g. all Customers in CustomerList)
            all_records = []
            for target_node in target_nodes:
                for child in target_node:
                    if isinstance(child.tag, str):
                        all_records.append(child)
            if all_records:
                return all_records, len(all_records)
            return target_nodes, len(target_nodes)

        entity_name = path[0]
        matched_elements = []
        total_items_count = 0
        for target_node in target_nodes:
            for entity_node in target_node:
                if not isinstance(entity_node.tag, str) or _strip_ns(entity_node.tag) != entity_name:
                    continue

                if len(path) > 1:
                    current_level_nodes = [entity_node]
                    has_path = True
                    for level_name in path[1:]:
                        next_level_nodes = []
                        for node in current_level_nodes:
                            for child in node:
                                if isinstance(child.tag, str) and _strip_ns(child.tag) == level_name:
                                    next_level_nodes.append(child)
                        if not next_level_nodes:
                            has_path = False
                            break
                        current_level_nodes = next_level_nodes
                    if has_path:
                        matched_elements.append(entity_node)
                        total_items_count += len(current_level_nodes)
                else:
                    matched_elements.append(entity_node)
                    total_items_count += 1

        final_count = total_items_count if total_items_count > 0 else len(matched_elements)
        return matched_elements, final_count

    # Case 3: Condition and Value are active
    matched_elements = []
    total_matches = 0

    if not path:
        # If the path is empty (e.g. Summary), filter the target itself
        for target_node in target_nodes:
            text = (target_node.text or "").strip()
            match = False

            if condition == "Equals" and text == value: match = True
            elif condition == "Not Equals" and text != value: match = True
            elif condition == "Contains" and value in text: match = True
            elif condition == "Starts With" and text.startswith(value): match = True
            elif condition == "Ends With" and text.endswith(value): match = True

            if match:
                matched_elements.append(target_node)
                total_matches += 1
        return matched_elements, total_matches

    entity_name = path[0]

    for target_node in target_nodes:
        for entity_node in target_node:
            if not isinstance(entity_node.tag, str) or _strip_ns(entity_node.tag) != entity_name:
                continue

            current_level_nodes = [entity_node]

            for level_name in path[1:]:
                next_level_nodes = []
                for node in current_level_nodes:
                    for child in node:
                        if isinstance(child.tag, str) and _strip_ns(child.tag) == level_name:
                            next_level_nodes.append(child)
                current_level_nodes = next_level_nodes

            entity_match_count = 0
            for leaf_node in current_level_nodes:
                text = (leaf_node.text or "").strip()

                match = False
                if condition == "Equals" and text == value: match = True
                elif condition == "Not Equals" and text != value: match = True
                elif condition == "Contains" and value in text: match = True
                elif condition == "Starts With" and text.startswith(value): match = True
                elif condition == "Ends With" and text.endswith(value): match = True

                if match:
                    entity_match_count += 1

            if entity_match_count > 0:
                matched_elements.append(entity_node)
                total_matches += entity_match_count

    return matched_elements, total_matches

