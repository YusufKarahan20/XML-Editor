"""
XML/history_diff.py
-------------------
Core XML Diff and DOM matching engine for XML Analyzer.

Implements the four-tier matching pipeline, Hirschberg space-optimized LCS
reconstruction, exact whitespace preservation, move/reorder detection,
and fine-grained change record generation.
"""

from typing import Optional, List, Dict, Tuple, Set, Any
from collections import Counter
from bisect import bisect_left
import time
import tracemalloc
import gc
import sys
import ctypes
from ctypes import wintypes
from lxml import etree

from XML.path_utils import (
    get_element_xml_path,
    build_element_xpath,
    build_attribute_xpath,
    build_text_xpath,
    build_tail_xpath,
    resolve_display_tag,
)
from XML.history_models import (
    ChangeRecord,
    ChangeType,
    NodeType,
)


def get_diff_parser() -> etree.XMLParser:
    """
    Returns an lxml XMLParser configured to strictly preserve whitespace,
    comments, and CDATA blocks without normalization.
    """
    return etree.XMLParser(
        remove_blank_text=False,
        strip_cdata=False,
        resolve_entities=False,
        recover=True,
    )


# ---------------------------------------------------------------------------
# Key Identification & Signatures
# ---------------------------------------------------------------------------

CANDIDATE_KEY_ATTRS = ("id", "ID", "name", "code", "key")


def precompute_attribute_frequencies(siblings: List[etree._Element]) -> Dict[str, Counter]:
    """
    Precomputes the frequency of all candidate key attribute values across a sibling list in O(N).
    """
    counters: Dict[str, Counter] = {attr: Counter() for attr in CANDIDATE_KEY_ATTRS}
    for s in siblings:
        if hasattr(s, "attrib") and s.attrib:
            for attr in CANDIDATE_KEY_ATTRS:
                val = s.attrib.get(attr)
                if val is not None and str(val).strip() != "":
                    counters[attr][str(val)] += 1
    return counters


def find_verified_unique_id(
    element: etree._Element,
    siblings_or_counters: Any
) -> Optional[Tuple[str, str]]:
    """
    Checks candidate key attributes on an element. Returns (attr_name, attr_val)
    if and only if the attribute value is non-empty and unique among all siblings.
    Accepts either the sibling list or precomputed frequency counters for O(1) checks.
    """
    if not hasattr(element, "attrib") or not element.attrib:
        return None

    if isinstance(siblings_or_counters, dict):
        counters = siblings_or_counters
    else:
        counters = precompute_attribute_frequencies(siblings_or_counters)

    for attr in CANDIDATE_KEY_ATTRS:
        val = element.attrib.get(attr)
        if val is not None and str(val).strip() != "":
            if counters.get(attr, {}).get(str(val)) == 1:
                return (attr, str(val))
    return None


def get_identity_signature(
    element: etree._Element,
    verified_id: Optional[Tuple[str, str]] = None
) -> Tuple:
    """
    Constructs the immutable Identity Signature for an element:
    (tag, verified_unique_key)
    """
    tag = getattr(element, "tag", "")
    key = verified_id[1] if verified_id else None
    return (tag, key)


def get_content_signature(element: etree._Element) -> Tuple:
    """
    Constructs the mutable Content Signature for structural and content comparisons:
    (tag, sorted_attributes, text, tail, child_signatures)
    """
    tag = getattr(element, "tag", "")
    attrs = tuple(sorted(getattr(element, "attrib", {}).items()))
    text = getattr(element, "text", None)
    tail = getattr(element, "tail", None)
    children = tuple(get_content_signature(c) for c in element if isinstance(getattr(c, "tag", None), str))
    return (tag, attrs, text, tail, children)


def is_exact_content_equal(e1: etree._Element, e2: etree._Element) -> bool:
    """
    Checks literal content equality of two elements including attributes,
    text, tail, and recursive child structure with fast early exits.
    """
    if e1.tag != e2.tag or e1.attrib != e2.attrib or e1.text != e2.text or e1.tail != e2.tail:
        return False
    if len(e1) != len(e2):
        return False
    if len(e1) == 0:
        return True
    return get_content_signature(e1) == get_content_signature(e2)


# ---------------------------------------------------------------------------
# Confidence Score Calculation
# ---------------------------------------------------------------------------

def calculate_confidence_score(
    old_elem: etree._Element,
    new_elem: etree._Element,
    old_idx: int,
    new_idx: int,
    max_len: int
) -> float:
    """
    Calculates the matching confidence score (0.0 to 1.0) between two candidate elements.
    Score = 0.40 * S_tag + 0.30 * S_attr + 0.15 * S_structure + 0.15 * S_pos
    """
    # 1. Tag match (0.40)
    if old_elem.tag != new_elem.tag:
        return 0.0
    s_tag = 1.0

    # 2. Attribute match (0.30)
    attrs_old = getattr(old_elem, "attrib", {})
    attrs_new = getattr(new_elem, "attrib", {})
    if not attrs_old and not attrs_new:
        s_attr = 1.0
    elif not attrs_old or not attrs_new:
        s_attr = 0.0
    else:
        keys_old = set(attrs_old.keys())
        keys_new = set(attrs_new.keys())
        keys_union = keys_old | keys_new
        keys_intersect = keys_old & keys_new
        jaccard_keys = len(keys_intersect) / len(keys_union) if keys_union else 1.0

        if keys_intersect:
            val_match_count = sum(1 for k in keys_intersect if attrs_old[k] == attrs_new[k])
            val_match_ratio = val_match_count / len(keys_intersect)
        else:
            val_match_ratio = 0.0
        # 50% key schema overlap + 50% value match on common keys
        s_attr = 0.5 * jaccard_keys + 0.5 * val_match_ratio

    # 3. Structural skeleton match (0.15)
    children_old = [c.tag for c in old_elem if isinstance(getattr(c, "tag", None), str)]
    children_new = [c.tag for c in new_elem if isinstance(getattr(c, "tag", None), str)]
    if not children_old and not children_new:
        # Both are leaf nodes
        s_structure = 1.0
    elif not children_old or not children_new:
        # One is leaf, one has children -> structural mismatch
        s_structure = 0.0
    else:
        # Dice coefficient on child tag sets/sequences
        common_count = sum(1 for tag in children_old if tag in children_new)
        s_structure = (2.0 * common_count) / (len(children_old) + len(children_new))

    # 4. Relative position proximity (0.15)
    denom = max(1, max_len)
    pos_diff = abs(old_idx - new_idx)
    s_pos = max(0.0, 1.0 - (pos_diff / denom))

    return 0.40 * s_tag + 0.30 * s_attr + 0.15 * s_structure + 0.15 * s_pos


# ---------------------------------------------------------------------------
# Space-Optimized Hirschberg LCS Reconstruction (O(min(M, N)) Space)
# ---------------------------------------------------------------------------

def _lcs_score_vector(
    seq_a: List[etree._Element],
    seq_b: List[etree._Element],
    a_start: int,
    a_end: int,
    b_start: int,
    b_end: int,
    equality_fn,
    hashes_a: Optional[List[int]] = None,
    hashes_b: Optional[List[int]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[int]:
    """
    Computes the last row of the DP matrix for seq_a[a_start:a_end] and
    seq_b[b_start:b_end] using O(len(B)) space.
    Uses ephemeral hashes as O(1) pre-filters with full equality_fn verification.
    """
    n = b_end - b_start
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    len_a = a_end - a_start

    if stats is not None:
        stats["dp_cells_evaluated"] = stats.get("dp_cells_evaluated", 0) + (len_a * n)

    has_hashes = (hashes_a is not None and hashes_b is not None)

    for i in range(a_start, a_end):
        elem_a = seq_a[i]
        h_a = hashes_a[i] if has_hashes else None
        for j in range(b_start, b_end):
            col = j - b_start + 1
            # Hash safety: hash match is only a fast pre-filter;
            # actual content equality is strictly verified by equality_fn.
            if has_hashes:
                is_equal = (h_a == hashes_b[j] and equality_fn(elem_a, seq_b[j]))
            else:
                is_equal = equality_fn(elem_a, seq_b[j])

            if is_equal:
                curr[col] = prev[col - 1] + 1
            else:
                curr[col] = max(curr[col - 1], prev[col])
        prev[:] = curr

    return curr


def hirschberg_align(
    seq_a: List[etree._Element],
    seq_b: List[etree._Element],
    equality_fn,
    hashes_a: Optional[List[int]] = None,
    hashes_b: Optional[List[int]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Tuple[int, int]]:
    """
    True Hirschberg divide-and-conquer LCS alignment.
    Guarantees O(min(M, N)) auxiliary memory by swapping sequences when M < N
    and using index intervals instead of list slices.
    Returns matched pairs as a list of strictly monotonically increasing (index_in_a, index_in_b).
    """
    m = len(seq_a)
    n = len(seq_b)

    if m == 0 or n == 0:
        return []

    if stats is not None:
        stats["hirschberg_calls"] = stats.get("hirschberg_calls", 0) + 1

    # Precompute ephemeral 64-bit content signature hashes for fast DP row iteration
    if hashes_a is None:
        hashes_a = [hash(get_content_signature(e)) for e in seq_a]
    if hashes_b is None:
        hashes_b = [hash(get_content_signature(e)) for e in seq_b]

    swapped = False
    if m < n:
        # Swap sequences to ensure the DP vector length is O(min(M, N))
        seq_a, seq_b = seq_b, seq_a
        hashes_a, hashes_b = hashes_b, hashes_a
        m, n = n, m
        swapped = True

        def swapped_equality(x, y):
            return equality_fn(y, x)
        eq_fn = swapped_equality
    else:
        eq_fn = equality_fn

    matches: List[Tuple[int, int]] = []

    def _recurse(a_start: int, a_end: int, b_start: int, b_end: int):
        len_a = a_end - a_start
        len_b = b_end - b_start

        if len_a == 0 or len_b == 0:
            return

        if len_a == 1:
            elem_a = seq_a[a_start]
            h_a = hashes_a[a_start]
            for j in range(b_start, b_end):
                if h_a == hashes_b[j] and eq_fn(elem_a, seq_b[j]):
                    matches.append((a_start, j))
                    break
            return

        # Divide seq_a at midpoint
        a_mid = a_start + len_a // 2

        # Forward DP on first half
        score_fwd = _lcs_score_vector(
            seq_a, seq_b, a_start, a_mid, b_start, b_end, eq_fn,
            hashes_a=hashes_a, hashes_b=hashes_b, stats=stats
        )

        # Backward DP on second half (simulated by passing reversed ranges)
        len_b_slice = b_end - b_start
        prev = [0] * (len_b_slice + 1)
        curr = [0] * (len_b_slice + 1)

        if stats is not None:
            stats["dp_cells_evaluated"] = stats.get("dp_cells_evaluated", 0) + ((a_end - a_mid) * len_b_slice)

        for i in range(a_end - 1, a_mid - 1, -1):
            elem_a = seq_a[i]
            h_a = hashes_a[i]
            for j in range(b_end - 1, b_start - 1, -1):
                col = b_end - j
                elem_b = seq_b[j]
                if h_a == hashes_b[j] and eq_fn(elem_a, elem_b):
                    curr[col] = prev[col - 1] + 1
                else:
                    curr[col] = max(curr[col - 1], prev[col])
            prev[:] = curr

        score_bwd = curr

        # Find split point b_split that maximizes score_fwd[j] + score_bwd[n - j]
        best_sum = -1
        best_split_idx = 0
        for j in range(len_b_slice + 1):
            s = score_fwd[j] + score_bwd[len_b_slice - j]
            if s > best_sum:
                best_sum = s
                best_split_idx = j

        b_split = b_start + best_split_idx

        # Free temporary vectors
        del score_fwd
        del score_bwd
        del prev
        del curr

        # Conquer
        _recurse(a_start, a_mid, b_start, b_split)
        _recurse(a_mid, a_end, b_split, b_end)

    _recurse(0, m, 0, n)

    if swapped:
        res = [(j, i) for (i, j) in matches]
    else:
        res = matches

    res.sort(key=lambda p: (p[0], p[1]))
    return res


# ---------------------------------------------------------------------------
# Process Memory Inspection (Win32 / POSIX Peak Working Set Size)
# ---------------------------------------------------------------------------

def get_process_peak_rss_mb() -> float:
    """
    Returns the peak working set size (peak RSS) of the current process in MB.
    Uses Win32 GetProcessMemoryInfo on Windows, or resource.getrusage on Unix.
    """
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi

            kernel32.GetCurrentProcess.restype = wintypes.HANDLE

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return round(counters.PeakWorkingSetSize / (1024 * 1024), 3)
        except Exception:
            pass
    else:
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            if sys.platform == "darwin":
                return round(usage.ru_maxrss / (1024 * 1024), 3)
            return round(usage.ru_maxrss / 1024, 3)
        except Exception:
            pass
    return 0.0


# ---------------------------------------------------------------------------
# Bounded Greedy Confidence Scoring (Top-K + Positional Banding + Dual Margin)
# ---------------------------------------------------------------------------

def _score_candidates_bounded(
    cand_old_indices: List[int],
    cand_new_indices: List[int],
    old_siblings: List[etree._Element],
    new_siblings: List[etree._Element],
    max_len: int,
    band_size: int = 50,
    top_k: int = 3,
    score_threshold: float = 0.70,
    margin_threshold: float = 0.15,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Tuple[int, int]]:
    """
    Bounded Greedy Heuristic for candidate matching (NOT Maximum Weight Bipartite Matching).

    Prevents M x N combinatorial explosion on large pools of remaining unmatched candidates:
    - If len(old) * len(new) > 10,000, applies a positional band (W=band_size) centered
      around the expected position including global index offset.
    - Limits candidate edges to Top-K (K=top_k) per old node.
    - Strictly validates ambiguity margin (Delta >= margin_threshold) from BOTH old
      element perspective and new element perspective.
    - Enforces strict 1-to-1 matching via used_old and used_new sets.
    """
    if not cand_old_indices or not cand_new_indices:
        return []

    m = len(cand_old_indices)
    n = len(cand_new_indices)
    apply_banding = (m * n > 10000)

    # Compute global offset between old and new siblings
    global_offset = len(new_siblings) - len(old_siblings)

    scores_by_old: Dict[int, Dict[int, float]] = {}
    scores_by_new: Dict[int, Dict[int, float]] = {}
    all_scored_edges: List[Tuple[float, int, int]] = []

    pairs_evaluated = 0

    for oi in cand_old_indices:
        # Expected position anchors in new siblings accounting for:
        # 1. Proportional offset: oi + (global_offset * oi) // max_len
        # 2. Block shift at head: oi + global_offset
        # 3. In-place modifications: oi
        center_prop = oi + (global_offset * oi) // max(max_len, 1)
        center_block = oi + global_offset

        candidate_scores_for_oi: List[Tuple[float, int]] = []
        elem_old = old_siblings[oi]

        for nj in cand_new_indices:
            if apply_banding:
                # Check positional distance within band against all alignment anchors
                min_dist = min(abs(nj - center_prop), abs(nj - center_block), abs(nj - oi))
                if min_dist > band_size:
                    continue

            pairs_evaluated += 1
            elem_new = new_siblings[nj]
            score = calculate_confidence_score(elem_old, elem_new, oi, nj, max_len)
            if score >= score_threshold:
                candidate_scores_for_oi.append((score, nj))

        # Keep Top-K candidates for this old element
        candidate_scores_for_oi.sort(key=lambda x: x[0], reverse=True)
        top_candidates = candidate_scores_for_oi[:top_k]

        scores_by_old[oi] = {}
        for score, nj in top_candidates:
            scores_by_old[oi][nj] = score
            scores_by_new.setdefault(nj, {})[oi] = score
            all_scored_edges.append((score, oi, nj))

    if stats is not None:
        stats["confidence_pairs_evaluated"] = stats.get("confidence_pairs_evaluated", 0) + pairs_evaluated

    # Sort all candidate edges descending by score (Greedy heuristic)
    all_scored_edges.sort(key=lambda x: x[0], reverse=True)

    used_old: Set[int] = set()
    used_new: Set[int] = set()
    matched_pairs: List[Tuple[int, int]] = []

    for score, oi, nj in all_scored_edges:
        if oi in used_old or nj in used_new:
            continue

        # 1. Old element perspective: check competing new candidates
        competing_new = [
            s for other_j, s in scores_by_old.get(oi, {}).items()
            if other_j != nj and other_j not in used_new
        ]
        if competing_new:
            second_best_old = max(competing_new)
            if (score - second_best_old) < margin_threshold:
                # Ambiguity margin not met from old perspective
                continue

        # 2. New element perspective: check competing old candidates
        competing_old = [
            s for other_i, s in scores_by_new.get(nj, {}).items()
            if other_i != oi and other_i not in used_old
        ]
        if competing_old:
            second_best_new = max(competing_old)
            if (score - second_best_new) < margin_threshold:
                # Ambiguity margin not met from new perspective
                continue

        # Both perspectives pass ambiguity margin
        matched_pairs.append((oi, nj))
        used_old.add(oi)
        used_new.add(nj)

    if stats is not None:
        stats["confidence_matched_count"] = stats.get("confidence_matched_count", 0) + len(matched_pairs)

    return matched_pairs


# ---------------------------------------------------------------------------
# Sibling Matcher Pipeline (Tiers 1, 2, 3, 4 + Move Detection)
# ---------------------------------------------------------------------------

class SiblingMatcher:
    """
    Executes the four-tier sibling matching pipeline under a given parent node:
    - Tier 1: Verified Unique ID match
    - Tier 2: Contextual Uniqueness (single tag candidate)
    - Tier 3: Multiple ID-less candidates with Hirschberg LCS & Bounded Confidence Scoring
    - Tier 4 / Move Detection: Order change identification and unmatched element assignment
    """

    def __init__(
        self,
        old_siblings: List[etree._Element],
        new_siblings: List[etree._Element],
        stats: Optional[Dict[str, Any]] = None
    ):
        self.old_siblings = old_siblings
        self.new_siblings = new_siblings
        self.old_len = len(old_siblings)
        self.new_len = len(new_siblings)
        self.max_len = max(self.old_len, self.new_len, 1)
        self.stats = stats

        # Precompute verified IDs using O(N) frequency tables
        old_counters = precompute_attribute_frequencies(old_siblings)
        self.old_ids: Dict[int, Tuple[str, str]] = {}
        for i, elem in enumerate(old_siblings):
            vid = find_verified_unique_id(elem, old_counters)
            if vid:
                self.old_ids[i] = vid

        new_counters = precompute_attribute_frequencies(new_siblings)
        self.new_ids: Dict[int, Tuple[str, str]] = {}
        for j, elem in enumerate(new_siblings):
            vid = find_verified_unique_id(elem, new_counters)
            if vid:
                self.new_ids[j] = vid

        # Tracking pools
        self.matched_pairs: List[Tuple[int, int]] = []
        self.unmatched_old: Set[int] = set(range(self.old_len))
        self.unmatched_new: Set[int] = set(range(self.new_len))

    def run(self) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        """
        Runs the matching pipeline with prefix/suffix trimming. Returns:
        - matched_pairs: list of (old_idx, new_idx)
        - deleted_indices: list of old_idx
        - inserted_indices: list of new_idx
        """
        if self.stats is not None:
            if "pre_trimming_count" not in self.stats:
                self.stats["pre_trimming_old"] = self.old_len
                self.stats["pre_trimming_new"] = self.new_len
                self.stats["pre_trimming_count"] = self.old_len

        # Step 0: Fast O(N) Prefix and Suffix Trimming
        head = 0
        while head < self.old_len and head < self.new_len and is_exact_content_equal(self.old_siblings[head], self.new_siblings[head]):
            self.matched_pairs.append((head, head))
            self.unmatched_old.discard(head)
            self.unmatched_new.discard(head)
            head += 1

        tail_old = self.old_len - 1
        tail_new = self.new_len - 1
        while tail_old >= head and tail_new >= head and is_exact_content_equal(self.old_siblings[tail_old], self.new_siblings[tail_new]):
            self.matched_pairs.append((tail_old, tail_new))
            self.unmatched_old.discard(tail_old)
            self.unmatched_new.discard(tail_new)
            tail_old -= 1
            tail_new -= 1

        if self.stats is not None:
            if "post_trimming_count" not in self.stats:
                self.stats["trimming_head_count"] = head
                self.stats["trimming_tail_count"] = (self.old_len - 1 - tail_old)
                self.stats["post_trimming_old"] = len(self.unmatched_old)
                self.stats["post_trimming_new"] = len(self.unmatched_new)
                self.stats["post_trimming_count"] = len(self.unmatched_old)

        # Tier 1: Verified Unique ID Matching
        self._match_tier1_verified_ids()

        # Tier 2: Contextual Uniqueness (ID-less single tag candidate)
        self._match_tier2_contextual_uniqueness()

        # Tier 3: Multiple ID-less Candidates (Hirschberg LCS + Bounded Confidence Scoring)
        self._match_tier3_multiple_candidates()

        # Tier 4: Move Detection between remaining unmatched candidates
        self._detect_moves_and_reorders()

        deleted = sorted(list(self.unmatched_old))
        inserted = sorted(list(self.unmatched_new))
        self.matched_pairs.sort(key=lambda p: (p[0], p[1]))
        return self.matched_pairs, deleted, inserted

    def _match_tier1_verified_ids(self):
        """
        Pairs elements with matching (tag, verified_id).
        Elements with different verified IDs are strictly forbidden from pairing.
        """
        new_id_map: Dict[Tuple[str, str], int] = {
            (self.new_siblings[j].tag, vid[1]): j
            for j, vid in self.new_ids.items()
            if j in self.unmatched_new
        }

        for i, vid in list(self.old_ids.items()):
            if i not in self.unmatched_old:
                continue
            tag = self.old_siblings[i].tag
            key = (tag, vid[1])
            if key in new_id_map:
                j = new_id_map[key]
                self.matched_pairs.append((i, j))
                self.unmatched_old.discard(i)
                self.unmatched_new.discard(j)

    def _match_tier2_contextual_uniqueness(self):
        """
        If a tag appears exactly once in remaining old siblings AND exactly once
        in remaining new siblings, pair them directly (even if content changed).
        """
        old_tag_indices: Dict[str, List[int]] = {}
        for i in self.unmatched_old:
            if i in self.old_ids:
                continue  # Has verified ID that failed to match
            tag = self.old_siblings[i].tag
            old_tag_indices.setdefault(tag, []).append(i)

        new_tag_indices: Dict[str, List[int]] = {}
        for j in self.unmatched_new:
            if j in self.new_ids:
                continue
            tag = self.new_siblings[j].tag
            new_tag_indices.setdefault(tag, []).append(j)

        for tag, old_list in old_tag_indices.items():
            if len(old_list) == 1 and tag in new_tag_indices and len(new_tag_indices[tag]) == 1:
                i = old_list[0]
                j = new_tag_indices[tag][0]
                self.matched_pairs.append((i, j))
                self.unmatched_old.discard(i)
                self.unmatched_new.discard(j)

    def _match_tier3_multiple_candidates(self):
        """
        Matches multiple ID-less candidates using:
        - Tier 3A: True Hirschberg LCS Backbone Alignment on exact-content elements
        - Tier 3B: Bounded Confidence Scoring on remaining candidate pool
        """
        remaining_old_tags = {self.old_siblings[i].tag for i in self.unmatched_old if i not in self.old_ids}
        remaining_new_tags = {self.new_siblings[j].tag for j in self.unmatched_new if j not in self.new_ids}
        common_tags = remaining_old_tags & remaining_new_tags

        for tag in common_tags:
            cand_old = [i for i in sorted(list(self.unmatched_old)) if self.old_siblings[i].tag == tag and i not in self.old_ids]
            cand_new = [j for j in sorted(list(self.unmatched_new)) if self.new_siblings[j].tag == tag and j not in self.new_ids]

            if not cand_old or not cand_new:
                continue

            # Tier 3A: True Hirschberg LCS Backbone Matching (O(min(M, N)) auxiliary space)
            cand_old_elems = [self.old_siblings[i] for i in cand_old]
            cand_new_elems = [self.new_siblings[j] for j in cand_new]

            lcs_pairs = hirschberg_align(
                cand_old_elems,
                cand_new_elems,
                equality_fn=is_exact_content_equal,
                stats=self.stats
            )

            for rel_oi, rel_ni in lcs_pairs:
                oi = cand_old[rel_oi]
                ni = cand_new[rel_ni]
                self.matched_pairs.append((oi, ni))
                self.unmatched_old.discard(oi)
                self.unmatched_new.discard(ni)

            if self.stats is not None:
                self.stats["hirschberg_matched_count"] = self.stats.get("hirschberg_matched_count", 0) + len(lcs_pairs)

            # Tier 3B: Bounded Confidence Scoring on remaining unmatched candidates of this tag
            remaining_cand_old = [i for i in cand_old if i in self.unmatched_old]
            remaining_cand_new = [j for j in cand_new if j in self.unmatched_new]

            if not remaining_cand_old or not remaining_cand_new:
                continue

            conf_pairs = _score_candidates_bounded(
                remaining_cand_old,
                remaining_cand_new,
                self.old_siblings,
                self.new_siblings,
                self.max_len,
                band_size=50,
                top_k=3,
                score_threshold=0.70,
                margin_threshold=0.15,
                stats=self.stats
            )

            for oi, ni in conf_pairs:
                self.matched_pairs.append((oi, ni))
                self.unmatched_old.discard(oi)
                self.unmatched_new.discard(ni)

    def _detect_moves_and_reorders(self):
        """
        Move Detection: If remaining unmatched elements have identical content
        and are unique in the pool, pair them as moved. Verified ID changes
        remain strictly delete + insert.
        """
        for i in list(self.unmatched_old):
            if i in self.old_ids:
                continue  # Verified ID changed; strictly delete + insert
            elem_old = self.old_siblings[i]

            # Look for an identical content partner in unmatched_new
            identical_candidates = [
                j for j in self.unmatched_new
                if j not in self.new_ids and is_exact_content_equal(elem_old, self.new_siblings[j])
            ]
            if len(identical_candidates) == 1:
                j = identical_candidates[0]
                self.matched_pairs.append((i, j))
                self.unmatched_old.discard(i)
                self.unmatched_new.discard(j)


# ---------------------------------------------------------------------------
# Core Diff Engine & Document Comparison
# ---------------------------------------------------------------------------

def compare_xml_elements(
    old_elem: Optional[etree._Element],
    new_elem: Optional[etree._Element],
    parent_xpath: str = "",
    expected_root: Optional[etree._Element] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[ChangeRecord]:
    """
    Recursively compares two parsed XML elements and produces a list of ChangeRecord items.
    """
    changes: List[ChangeRecord] = []

    if old_elem is None and new_elem is None:
        return changes

    # Element insertion
    if old_elem is None and new_elem is not None:
        xpath = build_element_xpath(new_elem, expected_root=expected_root)
        changes.append(ChangeRecord(
            change_type=ChangeType.ELEMENT_INSERT,
            xpath=xpath,
            node_type=NodeType.ELEMENT,
            new_value=etree.tostring(new_elem, encoding="unicode", with_tail=False),
            tag=getattr(new_elem, "tag", None),
            line_number=getattr(new_elem, "sourceline", None)
        ))
        return changes

    # Element deletion
    if old_elem is not None and new_elem is None:
        xpath = build_element_xpath(old_elem)
        changes.append(ChangeRecord(
            change_type=ChangeType.ELEMENT_DELETE,
            xpath=xpath,
            node_type=NodeType.ELEMENT,
            old_value=etree.tostring(old_elem, encoding="unicode", with_tail=False),
            tag=getattr(old_elem, "tag", None),
            line_number=getattr(old_elem, "sourceline", None)
        ))
        return changes

    assert old_elem is not None and new_elem is not None

    elem_xpath = build_element_xpath(new_elem, expected_root=expected_root) or parent_xpath
    line_no = getattr(new_elem, "sourceline", None)
    old_line_no = getattr(old_elem, "sourceline", None)

    # 1. Element Rename check
    if old_elem.tag != new_elem.tag:
        changes.append(ChangeRecord(
            change_type=ChangeType.ELEMENT_RENAME,
            xpath=elem_xpath,
            node_type=NodeType.ELEMENT,
            old_tag=old_elem.tag,
            new_tag=new_elem.tag,
            line_number=line_no,
            old_line_number=old_line_no,
        ))

    # 2. Attribute comparison
    attrs_old = getattr(old_elem, "attrib", {})
    attrs_new = getattr(new_elem, "attrib", {})

    all_keys = set(attrs_old.keys()) | set(attrs_new.keys())
    for key in sorted(all_keys):
        val_old = attrs_old.get(key)
        val_new = attrs_new.get(key)
        attr_xpath = build_attribute_xpath(new_elem, key, expected_root=expected_root)

        if val_old is None and val_new is not None:
            changes.append(ChangeRecord(
                change_type=ChangeType.ATTR_INSERT,
                xpath=attr_xpath,
                node_type=NodeType.ATTRIBUTE,
                attr_name=key,
                new_value=val_new,
                line_number=line_no
            ))
        elif val_old is not None and val_new is None:
            changes.append(ChangeRecord(
                change_type=ChangeType.ATTR_DELETE,
                xpath=attr_xpath,
                node_type=NodeType.ATTRIBUTE,
                attr_name=key,
                old_value=val_old,
                line_number=line_no
            ))
        elif val_old != val_new:
            changes.append(ChangeRecord(
                change_type=ChangeType.ATTR_UPDATE,
                xpath=attr_xpath,
                node_type=NodeType.ATTRIBUTE,
                attr_name=key,
                old_value=val_old,
                new_value=val_new,
                line_number=line_no
            ))

    # 3. Text comparison (Exact literal equality, no strip)
    text_old = getattr(old_elem, "text", None)
    text_new = getattr(new_elem, "text", None)
    if text_old != text_new:
        is_leaf = (len(old_elem) == 0 and len(new_elem) == 0)
        changes.append(ChangeRecord(
            change_type=ChangeType.VALUE_UPDATE,
            xpath=elem_xpath if is_leaf else build_text_xpath(new_elem, expected_root=expected_root),
            node_type=NodeType.ELEMENT if is_leaf else NodeType.TEXT,
            old_value=text_old,
            new_value=text_new,
            line_number=line_no,
            old_line_number=old_line_no
        ))

    # 4. Tail comparison (Exact literal equality, no strip)
    tail_old = getattr(old_elem, "tail", None)
    tail_new = getattr(new_elem, "tail", None)
    if tail_old != tail_new:
        changes.append(ChangeRecord(
            change_type=ChangeType.VALUE_UPDATE,
            xpath=elem_xpath,
            node_type=NodeType.TAIL,
            old_value=tail_old,
            new_value=tail_new,
            line_number=line_no,
            old_line_number=old_line_no
        ))

    # 5. Sibling children comparison
    children_old = [c for c in old_elem if isinstance(getattr(c, "tag", None), str)]
    children_new = [c for c in new_elem if isinstance(getattr(c, "tag", None), str)]

    if children_old or children_new:
        matcher = SiblingMatcher(children_old, children_new, stats=stats)
        matched_pairs, deleted_indices, inserted_indices = matcher.run()

        # Emit deletes
        for oi in deleted_indices:
            del_child = children_old[oi]
            child_xpath = build_element_xpath(del_child)
            changes.append(ChangeRecord(
                change_type=ChangeType.ELEMENT_DELETE,
                xpath=child_xpath,
                node_type=NodeType.ELEMENT,
                old_value=etree.tostring(del_child, encoding="unicode", with_tail=False),
                old_position=oi,
                tag=getattr(del_child, "tag", None),
                line_number=getattr(del_child, "sourceline", None)
            ))

        # Emit inserts
        for ni in inserted_indices:
            ins_child = children_new[ni]
            child_xpath = build_element_xpath(ins_child, expected_root=expected_root)
            changes.append(ChangeRecord(
                change_type=ChangeType.ELEMENT_INSERT,
                xpath=child_xpath,
                node_type=NodeType.ELEMENT,
                new_value=etree.tostring(ins_child, encoding="unicode", with_tail=False),
                new_position=ni,
                tag=getattr(ins_child, "tag", None),
                line_number=getattr(ins_child, "sourceline", None)
            ))

        # Relative order tracking: Longest Increasing Subsequence on new indices
        matched_sorted = sorted(matched_pairs, key=lambda p: p[0])
        new_indices = [p[1] for p in matched_sorted]
        lis_indices = _compute_lis_indices(new_indices)

        for rank, (oi, ni) in enumerate(matched_sorted):
            child_old = children_old[oi]
            child_new = children_new[ni]
            is_order_changed = (rank not in lis_indices)

            exact_equal = is_exact_content_equal(child_old, child_new)
            if exact_equal and not is_order_changed:
                continue

            child_changes = []
            if not exact_equal:
                child_changes = compare_xml_elements(
                    child_old, child_new,
                    parent_xpath=elem_xpath,
                    expected_root=expected_root,
                    stats=stats
                )

            if is_order_changed:
                child_xpath = build_element_xpath(child_new, expected_root=expected_root)
                # If child had a value update, flag order_changed on it, or emit an ORDER_CHANGED record
                marked = False
                for cr in child_changes:
                    if cr.xpath == child_xpath and cr.change_type == ChangeType.VALUE_UPDATE:
                        cr.order_changed = True
                        cr.old_position = oi
                        cr.new_position = ni
                        marked = True
                        break

                if not marked:
                    changes.append(ChangeRecord(
                        change_type=ChangeType.ORDER_CHANGED,
                        xpath=child_xpath,
                        node_type=NodeType.ELEMENT,
                        old_position=oi,
                        new_position=ni,
                        order_changed=True,
                        line_number=getattr(child_new, "sourceline", None),
                        old_line_number=getattr(child_old, "sourceline", None)
                    ))

            changes.extend(child_changes)

    return changes


def _compute_lis_indices(seq: List[int]) -> Set[int]:
    """
    Computes indices of seq that form the Longest Increasing Subsequence
    in O(N log N) time using binary search / patience sorting.
    """
    if not seq:
        return set()
    n = len(seq)
    tails_val: List[int] = []
    tails_idx: List[int] = []
    parent: List[int] = [-1] * n

    for i, x in enumerate(seq):
        idx = bisect_left(tails_val, x)
        if idx == len(tails_val):
            tails_val.append(x)
            tails_idx.append(i)
        else:
            tails_val[idx] = x
            tails_idx[idx] = i

        if idx > 0:
            parent[i] = tails_idx[idx - 1]

    lis = set()
    if tails_idx:
        curr = tails_idx[-1]
        while curr != -1:
            lis.add(curr)
            curr = parent[curr]
    return lis


def compare_xml_documents(
    old_xml: str,
    new_xml: str,
    doc_id: str = "",
    save_id: str = "",
    stats: Optional[Dict[str, Any]] = None,
) -> List[ChangeRecord]:
    """
    Compares two XML strings or bytes and returns the full list of atomic ChangeRecords.
    """
    if old_xml == new_xml:
        return []

    parser = get_diff_parser()
    old_bytes = old_xml.encode("utf-8") if isinstance(old_xml, str) else old_xml
    new_bytes = new_xml.encode("utf-8") if isinstance(new_xml, str) else new_xml

    try:
        old_root = etree.fromstring(old_bytes, parser=parser)
    except Exception as e:
        raise ValueError(f"Failed to parse old XML document: {e}")

    try:
        new_root = etree.fromstring(new_bytes, parser=parser)
    except Exception as e:
        raise ValueError(f"Failed to parse new XML document: {e}")

    return compare_xml_elements(old_root, new_root, expected_root=new_root, stats=stats)


# ---------------------------------------------------------------------------
# Benchmark Utility
# ---------------------------------------------------------------------------

def benchmark_diff(
    scenario: str = "A",
    num_elements: int = 10000,
    modified_count: int = 100,
) -> Dict[str, Any]:
    """
    Benchmarks XML Diff execution time, peak tracemalloc memory, and peak process RSS.
    Supports Scenarios A, B, C, D, E with detailed algorithmic telemetry.
    """
    scenario = scenario.upper()

    if scenario == "A":
        # Scenario A: 10k items with verified ID, distributed updates (trimming bypassed)
        old_items = [f'<Item id="item_{i}"><Value>val_{i}</Value></Item>' for i in range(num_elements)]
        new_items = list(old_items)

        mod_indices = {0, num_elements - 1} if num_elements > 1 else {0}
        effective_mod = min(modified_count, num_elements)
        if effective_mod > 2 and num_elements > 2:
            step = max(1, (num_elements - 2) // (effective_mod - 2))
            for k in range(1, effective_mod - 1):
                mod_indices.add(min(num_elements - 2, 1 + k * step))
            curr = 1
            while len(mod_indices) < effective_mod:
                mod_indices.add(curr)
                curr += 1

        for idx in mod_indices:
            new_items[idx] = f'<Item id="item_{idx}"><Value>val_{idx}_updated</Value></Item>'

    elif scenario == "B":
        # Scenario B: 10k items with verified ID, 1-shift cyclic rotation (trimming bypassed)
        old_items = [f'<Item id="item_{i}"><Value>val_{i}</Value></Item>' for i in range(num_elements)]
        if num_elements > 1:
            new_items = old_items[1:] + old_items[:1]
        else:
            new_items = list(old_items)

    elif scenario == "C":
        # Scenario C: 10k ID-less siblings with distributed updates (trimming bypassed)
        old_items = [f'<Item><Value>val_{i}</Value></Item>' for i in range(num_elements)]
        new_items = list(old_items)

        mod_indices = {0, num_elements - 1} if num_elements > 1 else {0}
        effective_mod = min(modified_count, num_elements)
        if effective_mod > 2 and num_elements > 2:
            step = max(1, (num_elements - 2) // (effective_mod - 2))
            for k in range(1, effective_mod - 1):
                mod_indices.add(min(num_elements - 2, 1 + k * step))
            curr = 1
            while len(mod_indices) < effective_mod:
                mod_indices.add(curr)
                curr += 1

        for idx in mod_indices:
            new_items[idx] = f'<Item><Value>val_{idx}_updated</Value></Item>'

    elif scenario == "D":
        # Scenario D: 10k items with verified ID, only first and last modified (trimming bypassed)
        old_items = [f'<Item id="item_{i}"><Value>val_{i}</Value></Item>' for i in range(num_elements)]
        new_items = list(old_items)
        if num_elements > 0:
            new_items[0] = f'<Item id="item_0"><Value>val_0_updated</Value></Item>'
        if num_elements > 1:
            new_items[-1] = f'<Item id="item_{num_elements - 1}"><Value>val_{num_elements - 1}_updated</Value></Item>'

    elif scenario == "E":
        # Scenario E: 1k ID-less items with interspersed insertions and deletions
        n = min(num_elements, 1000)
        old_items = [f'<Item><Value>val_{i}</Value></Item>' for i in range(n)]
        new_items = []
        for i in range(n):
            if i % 10 == 0:
                new_items.append(f'<Item><Value>val_{i}_inserted</Value></Item>')
            elif i % 20 != 5:  # skip some to simulate deletions
                new_items.append(f'<Item><Value>val_{i}</Value></Item>')
    else:
        raise ValueError(f"Unknown benchmark scenario: {scenario}")

    old_xml = "<Catalog>" + "".join(old_items) + "</Catalog>"
    new_xml = "<Catalog>" + "".join(new_items) + "</Catalog>"

    stats: Dict[str, Any] = {
        "hirschberg_calls": 0,
        "dp_cells_evaluated": 0,
        "hirschberg_matched_count": 0,
        "confidence_pairs_evaluated": 0,
        "confidence_matched_count": 0,
    }

    gc.collect()
    tracemalloc.start()
    start_time = time.perf_counter()

    changes = compare_xml_documents(old_xml, new_xml, stats=stats)

    duration = time.perf_counter() - start_time
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss = get_process_peak_rss_mb()

    changes_by_type: Dict[str, int] = {}
    for c in changes:
        ct_name = c.change_type.value if hasattr(c.change_type, "value") else str(c.change_type)
        changes_by_type[ct_name] = changes_by_type.get(ct_name, 0) + 1

    return {
        "scenario": scenario,
        "num_elements": len(old_items),
        "modified_count": modified_count,
        "changes_detected": len(changes),
        "changes_by_type": changes_by_type,
        "duration_seconds": round(duration, 4),
        "peak_memory_mb": round(peak_mem / (1024 * 1024), 3),
        "tracemalloc_peak_mb": round(peak_mem / (1024 * 1024), 3),
        "current_memory_mb": round(current_mem / (1024 * 1024), 3),
        "process_peak_rss_mb": peak_rss,
        "pre_trimming_count": stats.get("pre_trimming_count", len(old_items)),
        "post_trimming_count": stats.get("post_trimming_count", len(old_items)),
        "trimming_head_count": stats.get("trimming_head_count", 0),
        "trimming_tail_count": stats.get("trimming_tail_count", 0),
        "hirschberg_calls": stats.get("hirschberg_calls", 0),
        "hirschberg_matched_count": stats.get("hirschberg_matched_count", 0),
        "dp_cells_evaluated": stats.get("dp_cells_evaluated", 0),
        "confidence_pairs_evaluated": stats.get("confidence_pairs_evaluated", 0),
        "confidence_matched_count": stats.get("confidence_matched_count", 0),
    }
