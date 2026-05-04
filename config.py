JD_SKILL_FREQUENCIES = {
    "Kubernetes": 0.85, "Terraform": 0.80, "AWS": 0.90, "CI/CD": 0.82,
    "Python": 0.75, "Docker": 0.78, "Helm": 0.65, "Prometheus": 0.60,
    "Grafana": 0.58, "ArgoCD": 0.55, "GitOps": 0.52, "Ansible": 0.50,
    "Linux": 0.72, "Networking/VPC": 0.68, "IAM": 0.70, "Security/WAF": 0.62,
    "Observability": 0.60, "EKS": 0.65, "Lambda": 0.70, "API Gateway": 0.58,
    "AIOps": 0.40, "MLOps": 0.35, "Cost Optimization": 0.48, "SRE practices": 0.55
}

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