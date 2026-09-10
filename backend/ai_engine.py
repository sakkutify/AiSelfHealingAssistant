import json
import logging
from typing import List, Dict, Any, Optional

import anthropic

logger = logging.getLogger("healer.ai")

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


SYSTEM_PROMPT = """\
You are an expert SRE AI assistant. You analyze structured JSON logs from a distributed \
e-commerce system and return a root-cause analysis.

Known failure patterns:
- GATEWAY_TIMEOUT: Payment gateway timeout → client retries → duplicate charges
- DUPLICATE_PAYMENT_DETECTED: Multiple payment records for one order (from retries)
- ORDER_UPDATE_FAILURE: Payment saved but order status not updated to PAID (missing @Transactional)
- NEGATIVE_STOCK: Race condition — non-atomic check-then-act in inventory service
- INCONSISTENT_STATE: Order stuck in CREATED — saved before inventory reservation succeeds
- CONFIRM_NOTIFICATION_FAILED: Async job lost MDC trace context
- ASYNC_TRACE_LOSS: Background job running without a trace_id

Blast radius rules:
- CRITICAL → DUPLICATE_PAYMENT_DETECTED or NEGATIVE_STOCK (financial / stock integrity)
- HIGH     → ORDER_UPDATE_FAILURE or INCONSISTENT_STATE (data consistency)
- MEDIUM   → GATEWAY_TIMEOUT (reliability, likely self-recovers)
- LOW      → ASYNC_TRACE_LOSS or CONFIRM_NOTIFICATION_FAILED (observability only)

PR decision rules (needs_pr field):
- CRITICAL or HIGH blast radius → needs_pr: true
- MEDIUM → needs_pr: true only if DUPLICATE_PAYMENT_DETECTED or ORDER_UPDATE_FAILURE present
- LOW → needs_pr: false

Respond with ONLY a valid JSON object — no markdown, no explanation:
{
  "error_types": ["<error_type1>", ...],
  "root_cause": "<one sentence>",
  "blast_radius": "LOW|MEDIUM|HIGH|CRITICAL",
  "fix_suggestion": "<specific code fix referencing the Java class/service>",
  "pr_description": "<concise GitHub PR title + one-line body>",
  "needs_pr": true|false,
  "pr_title": "<short imperative PR title, e.g. fix: add idempotency key to PaymentGatewayClient>"
}
"""


def analyze_logs(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Send log batch to Claude and return parsed RCA dict."""
    log_text = json.dumps(logs, indent=2)

    try:
        message = get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyze the following logs and return ONLY the JSON object:\n\n"
                        + log_text
                    ),
                }
            ],
        )
        raw = message.content[0].text.strip()

        if "```" in raw:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            raw = raw[start:end]

        result = json.loads(raw)
        logger.info(
            f"RCA: blast={result.get('blast_radius')} "
            f"errors={result.get('error_types')} "
            f"needs_pr={result.get('needs_pr')}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Claude returned non-JSON: {e}")
        return _fallback_analysis(logs)
    except Exception as e:
        logger.error(f"AI analysis failed: {e}")
        return _fallback_analysis(logs)


def _fallback_analysis(logs: List[Dict]) -> Dict:
    error_types = list({log["error_type"] for log in logs if log.get("error_type")})

    blast = "LOW"
    if any(e in ("DUPLICATE_PAYMENT_DETECTED", "NEGATIVE_STOCK") for e in error_types):
        blast = "CRITICAL"
    elif any(e in ("ORDER_UPDATE_FAILURE", "INCONSISTENT_STATE") for e in error_types):
        blast = "HIGH"
    elif "GATEWAY_TIMEOUT" in error_types:
        blast = "MEDIUM"

    needs_pr = blast in ("CRITICAL", "HIGH")

    return {
        "error_types": error_types or ["UNKNOWN"],
        "root_cause": "Automated rule-based analysis (AI unavailable).",
        "blast_radius": blast,
        "fix_suggestion": "Review logs manually; AI analysis was unavailable.",
        "pr_description": "Incident detected — manual review required.",
        "needs_pr": needs_pr,
        "pr_title": f"fix: incident detected — {', '.join(error_types[:2])}",
    }
