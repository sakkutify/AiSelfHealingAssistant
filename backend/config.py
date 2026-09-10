import os
from typing import List, Dict, Optional


def _parse_service_part(part: str) -> Optional[Dict[str, str]]:
    """
    Parse one service entry. Supported formats:
      Name:http://service-url
      Name:http://service-url:https://github-repo-url
    """
    part = part.strip()
    # Find where the first URL begins
    idx = part.find(":http")
    if idx == -1:
        return None
    name = part[:idx].strip()
    rest = part[idx + 1:]          # "http://localhost:8080" or "http://....:https://..."

    # Detect a second URL (repo) — look for :https:// or a second :http://
    second = rest.find(":https://")
    if second == -1:
        second = rest.find(":http://", 7)  # skip past the scheme of the first URL

    if second != -1:
        service_url = rest[:second].rstrip("/")
        repo_url = rest[second + 1:].rstrip("/")
    else:
        service_url = rest.rstrip("/")
        repo_url = None

    if not name or not service_url:
        return None

    return {"name": name, "url": service_url, "repo": repo_url}


def parse_services() -> List[Dict[str, str]]:
    """
    Parse SERVICES env var.

    Format:
      SERVICES=Orders:http://localhost:8080:https://github.com/rajeevWissen/Order,\\
               Payments:http://localhost:8081:https://github.com/rajeevWissen/Payment

    Falls back to TARGET_URL (no repo) for backward compatibility.
    """
    raw = os.getenv("SERVICES", "").strip()
    if raw:
        services = [s for part in raw.split(",") if (s := _parse_service_part(part))]
        if services:
            return services

    url = os.getenv("TARGET_URL", "http://localhost:8080").rstrip("/")
    return [{"name": "Default", "url": url, "repo": None}]


SERVICES = parse_services()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
# Comma-separated list of blast_radius values that trigger a webhook
WEBHOOK_SEVERITIES = {
    s.strip().upper()
    for s in os.getenv("WEBHOOK_SEVERITIES", "CRITICAL,HIGH").split(",")
    if s.strip()
}
