"""
run_pipeline.py — thin alias kept for backwards compatibility.

The real CLI lives in mindci.py. Running this is equivalent to:

    python mindci.py run

Delete this file at any time; nothing else imports it.
"""

import sys

from mindci import main

if __name__ == "__main__":
    sys.exit(main(["run"] + sys.argv[1:]))
