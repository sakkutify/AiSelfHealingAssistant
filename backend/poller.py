import httpx
import hashlib
import logging
import os
from typing import List, Dict, Optional, Tuple

from db import is_log_processed, mark_logs_processed

logger = logging.getLogger("healer.poller")

ERROR_LEVELS = {"ERROR", "WARN"}
TRACKED_ERROR_TYPES = {
    "GATEWAY_TIMEOUT",
    "DUPLICATE_PAYMENT_DETECTED",
    "ORDER_UPDATE_FAILURE",
    "NEGATIVE_STOCK",
    "INCONSISTENT_STATE",
    "CONFIRM_NOTIFICATION_FAILED",
    "ASYNC_TRACE_LOSS",
}


async def fetch_logs(base_url: str, trace_id: Optional[str] = None) -> List[Dict]:
    url = f"{base_url}/logs"
    if trace_id:
        url += f"?trace_id={trace_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Failed to fetch logs from {base_url}: {e}")
        return []


async def fetch_health(base_url: str) -> Dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/health")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch health from {base_url}: {e}")
        return {"status": "UNREACHABLE", "services": {}}


async def fetch_inventory(base_url: str) -> Dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/inventory")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch inventory from {base_url}: {e}")
        return {}


def log_hash(log: Dict, service_name: str) -> str:
    """Include service_name in hash so same log from different services is distinct."""
    key = "|".join([
        service_name,
        str(log.get("timestamp", "")),
        str(log.get("trace_id", "")),
        str(log.get("error_type", "")),
        str(log.get("message", ""))[:80],
    ])
    return hashlib.sha1(key.encode()).hexdigest()


async def poll_new_logs(service: Dict[str, str]) -> Tuple[List[Dict], List[str]]:
    """Return (new_logs, new_hashes) for a single service."""
    base_url = service["url"]
    service_name = service["name"]

    all_logs = await fetch_logs(base_url)
    new_logs = []
    new_hashes = []

    for log in all_logs:
        h = log_hash(log, service_name)
        if not is_log_processed(h):
            new_logs.append(log)
            new_hashes.append(h)

    if new_hashes:
        mark_logs_processed(new_hashes)

    logger.info(f"[{service_name}] Polled {len(all_logs)} logs, {len(new_logs)} new")
    return new_logs, new_hashes


def group_by_trace(logs: List[Dict]) -> Dict[str, List[Dict]]:
    groups: Dict[str, List[Dict]] = {}
    for log in logs:
        tid = log.get("trace_id") or "unknown"
        groups.setdefault(tid, []).append(log)
    return groups


def is_incident_worthy(logs: List[Dict]) -> bool:
    for log in logs:
        if log.get("error_type") in TRACKED_ERROR_TYPES:
            return True
        if log.get("level") in ERROR_LEVELS and log.get("error_type"):
            return True
    return False


def extract_error_types(logs: List[Dict]) -> List[str]:
    seen = set()
    result = []
    for log in logs:
        et = log.get("error_type")
        if et and et not in seen:
            seen.add(et)
            result.append(et)
    return result
