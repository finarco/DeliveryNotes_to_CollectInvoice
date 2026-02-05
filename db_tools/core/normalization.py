"""Text normalization utilities for FK resolution matching.

This module provides normalization functions that handle common data entry
variations (whitespace, punctuation, case) while preserving actual word content.

Example:
    "FINARCO s. r.  o." → "finarco s.r.o." (matches "finarco, s.r.o.")
    "finarco B, s.r.o." → "finarco b s.r.o." (does NOT match "finarco s.r.o.")
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


def normalize_for_matching(text: str) -> str:
    """Normalize text for FK matching - handles typos, not different entities.

    Rules:
    - Convert to lowercase (case-insensitive matching)
    - Replace commas and semicolons with spaces
    - Remove trailing dots (after letters)
    - Collapse multiple spaces/dots into single space/dot
    - Trim leading/trailing whitespace

    Important: Letters and numbers are NEVER removed or changed (except case).
    Only whitespace and punctuation are normalized.

    Args:
        text: The input text to normalize

    Returns:
        Normalized text for comparison

    Examples:
        >>> normalize_for_matching("FINARCO s.r.o.")
        'finarco s.r.o.'
        >>> normalize_for_matching("finarco, s.r.o.")
        'finarco s.r.o.'
        >>> normalize_for_matching("finarco s. r.  o.")
        'finarco s.r.o.'
        >>> normalize_for_matching("finarco B, s.r.o.")
        'finarco b s.r.o.'
    """
    if not text:
        return ""

    result = text.lower()

    # Replace commas and semicolons with spaces
    result = re.sub(r"[,;]", " ", result)

    # Normalize "s. r. o." patterns - remove spaces around dots between single letters
    # This handles: "s. r. o." → "s.r.o."
    result = re.sub(r"(\b\w)\.\s+(\w\b)", r"\1.\2", result)

    # Remove trailing dot at end of string
    result = re.sub(r"\.$", "", result)

    # Collapse multiple spaces into single space
    result = re.sub(r"\s+", " ", result)

    # Trim edges
    result = result.strip()

    return result


def find_best_match(
    search_value: str,
    candidates: List[Tuple[int, str]],
    *,
    exact_only: bool = False,
) -> Optional[Tuple[int, str, bool]]:
    """Find the best matching candidate for a search value.

    Args:
        search_value: The value to search for
        candidates: List of (id, name) tuples to search in
        exact_only: If True, only return exact matches (no normalization)

    Returns:
        Tuple of (id, matched_name, was_exact_match) or None if no match found

    Raises:
        ValueError: If multiple candidates match after normalization (ambiguous)
    """
    if not search_value or not candidates:
        return None

    # First, try exact match
    for cid, cname in candidates:
        if cname == search_value:
            return (cid, cname, True)

    if exact_only:
        return None

    # Normalize search value
    normalized_search = normalize_for_matching(search_value)

    # Find all matches after normalization
    matches = []
    for cid, cname in candidates:
        if normalize_for_matching(cname) == normalized_search:
            matches.append((cid, cname))

    if len(matches) == 0:
        return None
    elif len(matches) == 1:
        return (matches[0][0], matches[0][1], False)
    else:
        # Ambiguous - multiple matches
        match_names = [m[1] for m in matches]
        raise ValueError(
            f"Ambiguous match for '{search_value}' - found: {', '.join(match_names)}"
        )


def suggest_similar(
    search_value: str,
    candidates: List[Tuple[int, str]],
    max_suggestions: int = 5,
) -> List[str]:
    """Suggest similar candidates when no match is found.

    Uses a simple approach: candidates that share the first word or
    contain significant parts of the search value.

    Args:
        search_value: The value that wasn't found
        candidates: List of (id, name) tuples
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of suggested names
    """
    if not search_value or not candidates:
        return []

    normalized_search = normalize_for_matching(search_value)
    search_words = set(normalized_search.split())
    first_word = normalized_search.split()[0] if normalized_search else ""

    scored = []
    for _, cname in candidates:
        normalized_cname = normalize_for_matching(cname)
        cname_words = set(normalized_cname.split())

        # Calculate simple similarity score
        score = 0

        # Bonus for matching first word
        if first_word and normalized_cname.startswith(first_word):
            score += 10

        # Bonus for shared words
        shared = search_words & cname_words
        score += len(shared) * 2

        # Bonus if search is substring of candidate or vice versa
        if normalized_search in normalized_cname:
            score += 5
        elif normalized_cname in normalized_search:
            score += 3

        if score > 0:
            scored.append((score, cname))

    # Sort by score descending, return top suggestions
    scored.sort(key=lambda x: -x[0])
    return [name for _, name in scored[:max_suggestions]]
