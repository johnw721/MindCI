"""
Tests for pipeline.convert.parse_markdown_with_frontmatter.
"""

from pipeline.convert import parse_markdown_with_frontmatter


def test_no_frontmatter_returns_empty_meta_and_full_body():
    src = "Just a plain note about etcd quorum.\nLine 2."
    meta, body = parse_markdown_with_frontmatter(src)
    assert meta == {}
    assert body == src


def test_frontmatter_extracted_and_body_preserved():
    src = (
        "---\n"
        "type: project\n"
        "confidence: Medium\n"
        "difficulty: Hard\n"
        "---\n"
        "\n"
        "Body line one.\n"
        "Body line two.\n"
    )
    meta, body = parse_markdown_with_frontmatter(src)
    assert meta == {"type": "project", "confidence": "Medium", "difficulty": "Hard"}
    assert body.startswith("Body line one.")
    assert "Body line two." in body


def test_quoted_values_are_stripped():
    src = "---\ntopic: \"etcd Raft\"\nauthor: 'Grey'\n---\n\nBody."
    meta, body = parse_markdown_with_frontmatter(src)
    assert meta == {"topic": "etcd Raft", "author": "Grey"}
    assert body == "Body."


def test_unclosed_frontmatter_returns_full_content_unchanged():
    src = "---\ntype: project\n\nbody continues here without a closing fence"
    meta, body = parse_markdown_with_frontmatter(src)
    assert meta == {}
    assert body == src
