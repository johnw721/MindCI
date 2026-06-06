"""Grounding / hallucination check for gap-analysis output.

A gap-analysis result should only assert skills the job description actually
mentions. If the model lists "Kafka" as a priority gap but the JD never says
"Kafka", that requirement is hallucinated -- the candidate would waste study
time on something the role never asked for, and in an interview the claim is
indefensible.

This check is deterministic (no API call). A domain is considered *grounded*
when at least one of its significant tokens appears in the JD text, using the
same word-boundary tokenization the scorer uses (so "EKS" must literally appear,
but "Amazon EKS" is grounded by the shared 'eks' token). Vendor words alone
("AWS", "cloud") never ground a domain -- otherwise every AWS skill would look
present in any AWS job post.

It is intentionally lenient (one shared significant token grounds the domain):
the goal is to catch fabricated requirements, not to penalize the model for
paraphrasing a skill the JD clearly states.
"""

from __future__ import annotations

from evals import metrics


def is_grounded(domain: str, jd_text: str) -> bool:
    """True if any significant token of ``domain`` appears in the JD text."""
    jd_tokens = metrics._tokens(jd_text)
    domain_sig = metrics._significant(metrics._tokens(domain))
    if not domain_sig:
        # Domain reduced to vendor/stopwords only -- can't ground it on those.
        return False
    return bool(domain_sig & jd_tokens)


def check_grounding(parsed: object, jd_text: str) -> dict:
    """Flag asserted gaps / matched skills that are absent from the JD text.

    Returns counts plus the offending domain strings, so a reviewer can see
    exactly what the model invented.
    """
    gaps = metrics.extract_domains(parsed, "priority_gaps")
    matches = metrics.extract_domains(parsed, "matched_skills")

    hallucinated_gaps = [d for d in gaps if not is_grounded(d, jd_text)]
    hallucinated_matches = [d for d in matches if not is_grounded(d, jd_text)]

    n_asserted = len(gaps) + len(matches)
    n_hallucinated = len(hallucinated_gaps) + len(hallucinated_matches)

    return {
        "asserted": n_asserted,
        "hallucinated": n_hallucinated,
        "rate": round(n_hallucinated / n_asserted, 4) if n_asserted else 0.0,
        "hallucinated_gaps": hallucinated_gaps,
        "hallucinated_matches": hallucinated_matches,
    }
