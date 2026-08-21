from web_watcher.diff_scope import apply_diff_scope, ScopeMiss, ScopeInvalid


HTML_DOC = """
<!DOCTYPE html>
<html>
<body>
    <article>
        <h1>Title</h1>
        <div class="post-body">
            <p>First paragraph.</p>
            <p>Second paragraph.</p>
        </div>
        <aside class="ads">Ad content</aside>
    </article>
</body>
</html>
"""


def test_apply_diff_scope_returns_scoped_html():
    scoped, info = apply_diff_scope(HTML_DOC, "article .post-body")
    assert "<p>First paragraph.</p>" in scoped
    assert "<aside" not in scoped
    assert info["matched_count"] == 1
    assert info["selector"] == "article .post-body"


def test_apply_diff_scope_merges_multiple_matches():
    scoped, info = apply_diff_scope(HTML_DOC, "p")
    assert "First paragraph." in scoped
    assert "Second paragraph." in scoped
    assert info["matched_count"] == 2
    assert info["original_length"] == len(HTML_DOC)
    assert info["scoped_length"] > 0


def test_apply_diff_scope_miss_raises():
    import pytest
    with pytest.raises(ScopeMiss):
        apply_diff_scope(HTML_DOC, ".non-existent")


def test_apply_diff_scope_invalid_selector_raises():
    import pytest
    with pytest.raises(ScopeInvalid):
        apply_diff_scope(HTML_DOC, "##invalid")


def test_apply_diff_scope_empty_selector_raises():
    import pytest
    with pytest.raises(ScopeInvalid):
        apply_diff_scope(HTML_DOC, "   ")
