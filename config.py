import json
import os

# Fallback hardcoded frequencies used until enough JD reports are accumulated
_FALLBACK_FREQUENCIES = {
    "Kubernetes": 0.85, "Terraform": 0.80, "AWS": 0.90, "CI/CD": 0.82,
    "Python": 0.75, "Docker": 0.78, "Helm": 0.65, "Prometheus": 0.60,
    "Grafana": 0.58, "ArgoCD": 0.55, "GitOps": 0.52, "Ansible": 0.50,
    "Linux": 0.72, "Networking/VPC": 0.68, "IAM": 0.70, "Security/WAF": 0.62,
    "Observability": 0.60, "EKS": 0.65, "Lambda": 0.70, "API Gateway": 0.58,
    "AIOps": 0.40, "MLOps": 0.35, "Cost Optimization": 0.48, "SRE practices": 0.55
}

MARKET_FREQUENCIES_PATH = "data/market_frequencies.json"
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


# Module-level load — cached for the session
JD_SKILL_FREQUENCIES, _FREQ_SOURCE, _FREQ_COUNT = load_jd_frequencies()

VALID_TYPES = {"project", "certification", "exploration"}

MIN_WORD_COUNT = 50

QUALITY_SIGNALS = {
    "confidence": ["confidence:", "confidence level:", "confidence -"],
    "difficulty": ["difficulty:", "difficulty level:", "difficulty -"],
    "root_cause": ["root cause:", "root_cause:", "why:", "because", "caused by"],
    "symptoms": ["symptom", "misleading", "looked like", "appeared to", "seemed like", "initially thought"],
    "fix": ["fix:", "solution:", "fixed by", "resolved by", "the fix"],
    "lesson": ["lesson:", "remember:", "key takeaway", "going forward", "next time"],
}