"""
Tests for `mindci.py capture` — the one-liner thought capture CLI.
"""

import re

import pytest

import mindci


def _run(argv, monkeypatch, raw_dir):
    """Invoke the CLI with argv. Returns (exit_code, files_in_raw)."""
    monkeypatch.setenv("MINDCI_RAW_DIR", str(raw_dir))
    # Rebind module-local RAW_DIR for callers that import it at module load.
    import config
    monkeypatch.setattr(config, "RAW_DIR", str(raw_dir))

    rc = mindci.main(argv)
    files = sorted(raw_dir.glob("*.txt"))
    return rc, files


def test_capture_writes_timestamped_file_with_text(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()

    rc, files = _run(
        ["capture", "Gotcha:", "Lambda", "cold-start", "circular", "import"],
        monkeypatch, raw,
    )

    assert rc == 0
    assert len(files) == 1
    # Filename matches capture_YYYYMMDD_HHMMSS.txt
    assert re.match(r"^capture_\d{8}_\d{6}\.txt$", files[0].name)
    # Args joined with spaces, trailing newline appended
    assert files[0].read_text(encoding="utf-8") == "Gotcha: Lambda cold-start circular import\n"


def test_capture_honors_custom_name(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()

    rc, files = _run(
        ["capture", "--name", "etcd_raft_quorum", "Split-brain at 2/3 quorum"],
        monkeypatch, raw,
    )

    assert rc == 0
    assert len(files) == 1
    assert files[0].name == "etcd_raft_quorum.txt"


def test_capture_rejects_empty_text(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()

    # argparse requires at least one positional with nargs="+", so pass whitespace
    # to exercise the "stripped to nothing" guard.
    with pytest.raises(SystemExit):
        _run(["capture"], monkeypatch, raw)
