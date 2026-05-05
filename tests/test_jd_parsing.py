"""
Tests for pipeline/jd_analyzer.parse_jds — the only pure function in that module.
The rest of the file calls Anthropic and is covered by the smoke-test boundary.
"""

from pipeline.jd_analyzer import parse_jds


def test_single_jd_returns_one_element():
    text = "We are hiring a Cloud Engineer with 3+ years of AWS, Terraform, and Kubernetes experience. " * 3
    out = parse_jds(text)
    assert len(out) == 1
    assert out[0].startswith("We are hiring")


def test_dashes_split_multiple_jds():
    jd_a = "Senior SRE role focused on Kubernetes, observability, and on-call rotation. " * 3
    jd_b = "Cloud Platform Engineer position emphasizing Terraform, AWS, and CI/CD pipelines. " * 3
    text = f"{jd_a}\n---\n{jd_b}"
    out = parse_jds(text)
    assert len(out) == 2
    assert "SRE" in out[0]
    assert "Platform Engineer" in out[1]


def test_short_chunks_are_dropped_below_threshold():
    """Chunks under 100 chars between separators are filtered out, so a single
    long JD wrapped in noise still parses as one."""
    text = "tiny\n---\n" + ("Real JD content with enough detail to clear the 100-char floor. " * 3)
    out = parse_jds(text)
    assert len(out) == 1
    assert "Real JD content" in out[0]
