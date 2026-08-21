"""Diff scope utilities.

v1 only supports CSS selector scoping.  Scope is applied to the HTML
fragment produced by an extractor, *before* transforms run.
"""

from typing import List, Optional, Tuple

from bs4 import BeautifulSoup
from soupsieve import SelectorSyntaxError


class ScopeMiss(Exception):
    """Raised when a scope selector matches 0 elements."""


class ScopeInvalid(Exception):
    """Raised when a scope selector is syntactically invalid."""


def apply_diff_scope(
    html_content: str,
    scope_selector: str,
) -> Tuple[str, dict]:
    """Apply a CSS diff scope to *html_content*.

    Args:
        html_content: Raw HTML fragment produced by the extractor selector.
        scope_selector: CSS selector to further narrow the fragment.

    Returns:
        (scoped_html, info)
        scoped_html: HTML string of matched elements (merged if multiple).
        info: dict with keys:
            - matched_count (int)
            - original_length (int)
            - scoped_length (int)
            - selector (str)

    Raises:
        ScopeMiss: If *scope_selector* matches 0 elements.
        ScopeInvalid: If *scope_selector* is empty/invalid.
    """
    if not scope_selector or not scope_selector.strip():
        raise ScopeInvalid("scope_selector must be a non-empty CSS selector")

    selector = scope_selector.strip()

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        elements = soup.select(selector)
    except (NotImplementedError, ValueError, TypeError, SelectorSyntaxError) as exc:
        raise ScopeInvalid(f"Invalid CSS selector '{selector}': {exc}") from exc

    if not elements:
        raise ScopeMiss(f"scope_selector '{selector}' matched 0 elements")

    # Merge multiple elements into a single HTML string.
    merged_html = "".join(str(el) for el in elements)
    scoped_soup = BeautifulSoup(merged_html, "html.parser")

    return str(scoped_soup), {
        "selector": selector,
        "matched_count": len(elements),
        "original_length": len(html_content),
        "scoped_length": len(merged_html),
    }
