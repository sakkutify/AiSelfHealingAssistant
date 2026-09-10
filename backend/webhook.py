"""
Webhook notifier — fires on CRITICAL / HIGH incidents.
Supports generic JSON and auto-detects Slack / Discord / Teams webhook URLs.
"""
import logging
from typing import Dict, Any

import httpx

logger = logging.getLogger("healer.webhook")

BLAST_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}


def _slack_payload(incident: Dict[str, Any]) -> Dict:
    blast = incident.get("blast_radius", "")
    emoji = BLAST_EMOJI.get(blast, "⚪")
    error_types = ", ".join(incident.get("error_types") or [])
    pr_line = f"\n>*PR:* <{incident['pr_url']}|View Pull Request>" if incident.get("pr_url") else ""

    return {
        "text": f"{emoji} *Healer Alert — {blast}*",
        "attachments": [{
            "color": {"CRITICAL": "#f85149", "HIGH": "#f0883e",
                      "MEDIUM": "#d29922", "LOW": "#3fb950"}.get(blast, "#8b949e"),
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    f"{emoji} *{blast} Incident — {incident.get('service_name','')}*\n"
                    f">*Error Types:* `{error_types}`\n"
                    f">*Root Cause:* {incident.get('root_cause','')}\n"
                    f">*Fix:* {incident.get('fix_suggestion','')}"
                    f"{pr_line}"}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"trace: `{incident.get('trace_id','')}` · {incident.get('detected_at','')[:19]} UTC"}]},
            ],
        }],
    }


def _discord_payload(incident: Dict[str, Any]) -> Dict:
    blast = incident.get("blast_radius", "")
    emoji = BLAST_EMOJI.get(blast, "⚪")
    color = {"CRITICAL": 0xf85149, "HIGH": 0xf0883e,
             "MEDIUM": 0xd29922, "LOW": 0x3fb950}.get(blast, 0x8b949e)
    error_types = ", ".join(incident.get("error_types") or [])

    fields = [
        {"name": "Error Types", "value": f"`{error_types}`", "inline": False},
        {"name": "Root Cause", "value": incident.get("root_cause",""), "inline": False},
        {"name": "Fix Suggestion", "value": incident.get("fix_suggestion",""), "inline": False},
        {"name": "Trace ID", "value": f"`{incident.get('trace_id','')}`", "inline": True},
        {"name": "Service", "value": incident.get("service_name",""), "inline": True},
    ]
    if incident.get("pr_url"):
        fields.append({"name": "Pull Request", "value": incident["pr_url"], "inline": False})

    return {
        "embeds": [{
            "title": f"{emoji} {blast} Incident",
            "color": color,
            "fields": fields,
            "footer": {"text": f"Healer · {incident.get('detected_at','')[:19]} UTC"},
        }]
    }


def _teams_payload(incident: Dict[str, Any]) -> Dict:
    blast = incident.get("blast_radius", "")
    emoji = BLAST_EMOJI.get(blast, "⚪")
    error_types = ", ".join(incident.get("error_types") or [])
    facts = [
        {"name": "Service", "value": incident.get("service_name","")},
        {"name": "Error Types", "value": error_types},
        {"name": "Root Cause", "value": incident.get("root_cause","")},
        {"name": "Fix Suggestion", "value": incident.get("fix_suggestion","")},
        {"name": "Trace ID", "value": incident.get("trace_id","")},
    ]
    if incident.get("pr_url"):
        facts.append({"name": "Pull Request", "value": incident["pr_url"]})

    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": {"CRITICAL": "f85149", "HIGH": "f0883e"}.get(blast, "58a6ff"),
        "summary": f"{emoji} {blast} Incident — {incident.get('service_name','')}",
        "sections": [{"activityTitle": f"{emoji} **{blast} Incident**", "facts": facts}],
    }


def _generic_payload(incident: Dict[str, Any]) -> Dict:
    return {
        "alert": "healer_incident",
        "blast_radius": incident.get("blast_radius"),
        "service_name": incident.get("service_name"),
        "trace_id": incident.get("trace_id"),
        "error_types": incident.get("error_types"),
        "root_cause": incident.get("root_cause"),
        "fix_suggestion": incident.get("fix_suggestion"),
        "pr_url": incident.get("pr_url"),
        "detected_at": incident.get("detected_at"),
        "incident_id": incident.get("id"),
    }


def _build_payload(url: str, incident: Dict[str, Any]) -> Dict:
    if "hooks.slack.com" in url or "slack.com/services" in url:
        return _slack_payload(incident)
    if "discord.com/api/webhooks" in url:
        return _discord_payload(incident)
    if "office.com" in url or "webhook.office.com" in url:
        return _teams_payload(incident)
    return _generic_payload(incident)


async def send(url: str, incident: Dict[str, Any]) -> bool:
    """POST incident to webhook URL. Returns True on success."""
    if not url:
        return False
    payload = _build_payload(url, incident)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload,
                                  headers={"Content-Type": "application/json"})
            r.raise_for_status()
            logger.info(f"Webhook sent for incident #{incident.get('id')} → {r.status_code}")
            return True
    except Exception as e:
        logger.warning(f"Webhook failed for incident #{incident.get('id')}: {e}")
        return False
