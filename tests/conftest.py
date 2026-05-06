"""
Test bootstrap.

Several MindCI modules instantiate `Anthropic()` at import time and `config.py`
hard-fails if `ANTHROPIC_API_KEY` is missing. We set both before any project
module is imported so the test suite never reaches the network and never asks
for real credentials.

We also redirect MINDCI_* paths to a temp directory so tests can't touch the
real `data/`, `output/`, `raw/`, or `jd_reports/` folders.
"""

import os
import sys
import tempfile
from pathlib import Path

# Must be set BEFORE any `from config import …` happens anywhere in the suite.
os.environ.setdefault("MINDCI_SKIP_ENV_CHECK", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key-not-used")

# Sandbox MindCI's runtime directories so tests are filesystem-pure.
_TMP = Path(tempfile.mkdtemp(prefix="mindci-tests-"))
os.environ.setdefault("MINDCI_DATA_DIR",       str(_TMP / "data"))
os.environ.setdefault("MINDCI_OUTPUT_DIR",     str(_TMP / "output"))
os.environ.setdefault("MINDCI_RAW_DIR",        str(_TMP / "raw"))
os.environ.setdefault("MINDCI_JD_REPORTS_DIR", str(_TMP / "jd_reports"))

# Make the project root importable from anywhere in tests/.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Stub the lazy Anthropic client so tests never hit the network.
# Pipeline modules now go through `pipeline._client.get_client()` instead of
# constructing `Anthropic()` at import time, so we can stub at that single
# choke point. Any test that accidentally calls `messages.create(...)` fails
# loudly rather than silently exercising real infrastructure.
class _StubClient:
    def __init__(self):
        self.messages = self

    def create(self, *_, **__):
        raise RuntimeError(
            "Real Anthropic API call from a test — mock the call site, "
            "or move this test out of the smoke-test tier."
        )


try:
    from pipeline import _client as _pipeline_client  # type: ignore
    _pipeline_client.get_client = lambda: _StubClient()  # type: ignore[attr-defined]
except ImportError:
    # pipeline package missing — modules that import it will fail on their own.
    pass
