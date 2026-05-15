"""
config.py

Single source of truth for environment + paths.
- Fails fast if required env vars are missing
- All paths are env-var-overridable for container deployments
- Logging goes to stdout (no file handlers) so container runtimes capture it
"""

import json
import logging
import os
import sys

# ── Logging to stdout (container-friendly) ────────────────────────────────────
logging.basicConfig(
    level=os.environ.get("MINDCI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("mindci")


# ── Fail-fast env validation ──────────────────────────────────────────────────
REQUIRED_ENV = ["ANTHROPIC_API_KEY"]


def _validate_env():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        msg = (
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set them in .env (local) or via docker-compose / your container "
            "platform before launching MindCI."
        )
        # In Streamlit dev mode importing config repeatedly should still raise.
        log.error(msg)
        raise RuntimeError(msg)


# Skip validation when explicitly running tooling that doesn't need the key
# (e.g. py_compile, unit tests). Set MINDCI_SKIP_ENV_CHECK=1 to bypass.
if not os.environ.get("MINDCI_SKIP_ENV_CHECK"):
    _validate_env()


# ── Configurable paths (all env-overridable) ──────────────────────────────────
DATA_DIR        = os.environ.get("MINDCI_DATA_DIR",       "data")
OUTPUT_DIR      = os.environ.get("MINDCI_OUTPUT_DIR",     "output")
RAW_DIR         = os.environ.get("MINDCI_RAW_DIR",        "raw")
JD_REPORTS_DIR  = os.environ.get("MINDCI_JD_REPORTS_DIR", "jd_reports")

for d in (DATA_DIR, OUTPUT_DIR, RAW_DIR, JD_REPORTS_DIR):
    os.makedirs(d, exist_ok=True)


# Fallback hardcoded frequencies used until enough JD reports are accumulated
_FALLBACK_FREQUENCIES = {
    "Kubernetes": 0.85, "Terraform": 0.80, "AWS": 0.90, "CI/CD": 0.82,
    "Python": 0.75, "Docker": 0.78, "Helm": 0.65, "Prometheus": 0.60,
    "Grafana": 0.58, "ArgoCD": 0.55, "GitOps": 0.52, "Ansible": 0.50,
    "Linux": 0.72, "Networking/VPC": 0.68, "IAM": 0.70, "Security/WAF": 0.62,
    "Observability": 0.60, "EKS": 0.65, "Lambda": 0.70, "API Gateway": 0.58,
    "AIOps": 0.40, "MLOps": 0.35, "Cost Optimization": 0.48, "SRE practices": 0.55
}

MARKET_FREQUENCIES_PATH = os.path.join(DATA_DIR, "market_frequencies.json")
MIN_REPORTS_FOR_LIVE_DATA = 3  # use live data only after this many reports


def load_jd_frequencies():
    """
    Load JD skill frequencies.
    Uses live aggregated data if available and sufficient,
    otherwise falls back to hardcoded baseline.
    Returns (frequencies_dict, source_label, report_count)
    """
    if os.path.exists(MARKET_FREQUENCIES_PATH):
        try:
            with open(MARKET_FREQUENCIES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = data.get("total_reports", 0)
            freqs = data.get("frequencies", {})
            if count >= MIN_REPORTS_FOR_LIVE_DATA and freqs:
                return freqs, f"live ({count} JD reports)", count
            elif freqs:
                # Blend: live data exists but below threshold — merge with fallback
                merged = dict(_FALLBACK_FREQUENCIES)
                merged.update(freqs)
                return merged, f"blended (live {count} + baseline)", count
        except Exception:
            pass
    return _FALLBACK_FREQUENCIES, "baseline (no JD reports yet)", 0


# Module-level load — kept as a snapshot for backwards compatibility.
# Live callers (app_dashboard, suggestions) should call load_jd_frequencies()
# directly so new JD reports show up without an app restart.
JD_SKILL_FREQUENCIES, _FREQ_SOURCE, _FREQ_COUNT = load_jd_frequencies()

VALID_TYPES = {"project", "certification", "exploration"}

MIN_WORD_COUNT = 50

# ── Anthropic call configuration (env-overridable) ────────────────────────────
# One choke point for the model name and a small set of named token caps.
# Sized to use case so a model upgrade or budget adjustment is one env var.
MODEL = os.environ.get("MINDCI_MODEL", "claude-sonnet-4-6")

MAX_TOKENS_GRADE      = int(os.environ.get("MINDCI_MAX_TOKENS_GRADE",       512))   # interview grading
MAX_TOKENS_REVIEW     = int(os.environ.get("MINDCI_MAX_TOKENS_REVIEW",     1024))   # preview, enrichment, rewrite
MAX_TOKENS_ANALYSIS   = int(os.environ.get("MINDCI_MAX_TOKENS_ANALYSIS",   2048))   # gap analysis, suggestions
MAX_TOKENS_BATCH      = int(os.environ.get("MINDCI_MAX_TOKENS_BATCH",      3000))   # batch JD analysis
MAX_TOKENS_GENERATION = int(os.environ.get("MINDCI_MAX_TOKENS_GENERATION", 4096))   # flashcards, scenarios, weekly

# USD per million tokens. Defaults are sensible placeholders — verify against
# Anthropic's current pricing for the active model and override via env vars
# if your model or pricing has changed.
# Override per environment if Anthropic adjusts pricing or you switch models.
MODEL_INPUT_PRICE_PER_MTOK  = float(os.environ.get("MINDCI_INPUT_PRICE_PER_MTOK",  3.0))
MODEL_OUTPUT_PRICE_PER_MTOK = float(os.environ.get("MINDCI_OUTPUT_PRICE_PER_MTOK", 15.0))

QUALITY_SIGNALS = {
    "confidence": ["confidence:", "confidence level:", "confidence -"],
    "difficulty": ["difficulty:", "difficulty level:", "difficulty -"],
    "root_cause": ["root cause:", "root_cause:", "why:", "because", "caused by"],
    "symptoms": ["symptom", "misleading", "looked like", "appeared to", "seemed like", "initially thought"],
    "fix": ["fix:", "solution:", "fixed by", "resolved by", "the fix"],
    "lesson": ["lesson:", "remember:", "key takeaway", "going forward", "next time"],
}
