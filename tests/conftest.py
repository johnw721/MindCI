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


# Stub `anthropic.Anthropic` BEFORE any pipeline module is imported.
# Several modules (notably pipeline/jd_analyzer.py) construct the client at
# import time. We never want a real network client in the test suite, and on
# some sandboxed runners constructing the real client even fails (e.g. missing
# socksio for httpx SOCKS proxy support). The stub raises on actual API calls
# so any test that accidentally goes to the network fails loudly instead of
# silently exercising real infrastructure.
try:
    import anthropic  # type: ignore

    class _StubAnthropic:
        def __init__(self, *_, **__):
            self.messages = self

        def create(self, *_, **__):
            raise RuntimeError(
                "Real Anthropic API call from a test — mock the call site, "
                "or move this test out of the smoke-test tier."
            )

    anthropic.Anthropic = _StubAnthropic  # type: ignore[attr-defined]
except ImportError:
    # If anthropic isn't installed in this env, modules that import it will
    # fail on their own — nothing for us to stub.
    pass
