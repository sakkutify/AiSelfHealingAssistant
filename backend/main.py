import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional, Set

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import db
import poller
import ai_engine
import github_client
import webhook
from config import SERVICES, GITHUB_TOKEN, WEBHOOK_URL, WEBHOOK_SEVERITIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("healer.main")

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "30"))

# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, message: dict):
        if not self.active:
            return
        text = json.dumps(message)
        dead: Set[WebSocket] = set()
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()

# ── Polling loop ──────────────────────────────────────────────────────────────

async def polling_loop():
    logger.info(f"Polling loop started — interval={POLL_INTERVAL_SECONDS}s, services={[s['name'] for s in SERVICES]}")
    while True:
        try:
            await run_poll_cycle()
        except Exception as e:
            logger.error(f"Polling cycle error: {e}", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def run_poll_cycle():
    """Poll every configured service and analyse new logs."""
    for service in SERVICES:
        await _poll_service(service)


async def _poll_service(service: dict):
    service_name = service["name"]
    new_logs, _ = await poller.poll_new_logs(service)

    if not new_logs:
        return

    await manager.broadcast({
        "type": "log_update",
        "service": service_name,
        "count": len(new_logs),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    groups = poller.group_by_trace(new_logs)

    for trace_id, logs in groups.items():
        if not poller.is_incident_worthy(logs):
            continue

        logger.info(f"[{service_name}] Incident-worthy trace={trace_id} logs={len(logs)}")

        try:
            rca = ai_engine.analyze_logs(logs)
        except Exception as e:
            logger.error(f"[{service_name}] RCA failed trace={trace_id}: {e}")
            continue

        incident_data = {
            "service_name": service_name,
            "trace_id": trace_id,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "error_types": rca.get("error_types", []),
            "root_cause": rca.get("root_cause", ""),
            "blast_radius": rca.get("blast_radius", "LOW"),
            "fix_suggestion": rca.get("fix_suggestion", ""),
            "pr_description": rca.get("pr_description"),
            "raw_logs": logs,
            "pr_status": "none",
            "pr_url": None,
            "pr_number": None,
            "pr_branch": None,
        }

        incident_id = db.save_incident(incident_data)
        incident_data["id"] = incident_id
        incident_data["resolved"] = False

        await manager.broadcast({"type": "new_incident", "incident": incident_data})
        logger.info(f"[{service_name}] Incident #{incident_id} blast={incident_data['blast_radius']}")

        # Fire webhook for CRITICAL / HIGH (or whatever WEBHOOK_SEVERITIES specifies)
        if WEBHOOK_URL and incident_data["blast_radius"] in WEBHOOK_SEVERITIES:
            asyncio.create_task(webhook.send(WEBHOOK_URL, incident_data))

        # Auto-create PR if AI decided it's needed and service has a repo configured
        if rca.get("needs_pr") and service.get("repo") and GITHUB_TOKEN:
            asyncio.create_task(
                _create_pr_for_incident(incident_id, incident_data, service["repo"])
            )


async def _create_pr_for_incident(incident_id: int, incident_data: dict, repo_url: str):
    """Create a GitHub PR and broadcast the result."""
    logger.info(f"Creating PR for incident #{incident_id} → {repo_url}")
    result = await github_client.create_pr(GITHUB_TOKEN, repo_url, incident_data)

    db.update_incident_pr(
        incident_id,
        pr_status=result["pr_status"],
        pr_url=result.get("pr_url"),
        pr_number=result.get("pr_number"),
        pr_branch=result.get("pr_branch"),
    )

    if result.get("error"):
        logger.warning(f"PR for incident #{incident_id}: {result['error']}")

    await manager.broadcast({
        "type": "pr_update",
        "incident_id": incident_id,
        "pr_status": result["pr_status"],
        "pr_url": result.get("pr_url"),
        "pr_number": result.get("pr_number"),
        "pr_branch": result.get("pr_branch"),
    })


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info(f"Database initialised — tracking {len(SERVICES)} service(s)")
    task = asyncio.create_task(polling_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Healer — AI Self-Healing Incident Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/services")
async def list_services():
    """Return all configured services with their live health status."""
    results = []
    for svc in SERVICES:
        health = await poller.fetch_health(svc["url"])
        results.append({
            "name": svc["name"],
            "url": svc["url"],
            "health": health,
        })
    return results


@app.get("/api/incidents")
async def list_incidents(
    limit: int = 100,
    resolved: Optional[bool] = None,
    service: Optional[str] = None,
):
    return db.get_incidents(limit=limit, resolved=resolved, service_name=service)


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: int):
    inc = db.get_incident_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: int):
    ok = db.resolve_incident(incident_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Incident not found")
    await manager.broadcast({"type": "resolved", "incident_id": incident_id})
    return {"ok": True}


@app.post("/api/incidents/{incident_id}/create-pr")
async def manual_create_pr(incident_id: int):
    """Manually trigger PR creation for an incident."""
    inc = db.get_incident_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    svc = next((s for s in SERVICES if s["name"] == inc["service_name"]), None)
    if not svc or not svc.get("repo"):
        raise HTTPException(status_code=400, detail="No GitHub repo configured for this service")
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN not configured")

    asyncio.create_task(_create_pr_for_incident(incident_id, inc, svc["repo"]))
    return {"ok": True, "message": "PR creation started"}


@app.get("/api/stats")
async def stats(service: Optional[str] = None):
    s = db.get_stats(service_name=service)
    # Include health for the requested service (or first service if all)
    if service and service != "all":
        svc = next((x for x in SERVICES if x["name"] == service), SERVICES[0])
    else:
        svc = SERVICES[0]
    s["target_health"] = await poller.fetch_health(svc["url"])
    return s


@app.get("/api/health")
async def healer_health():
    return {"status": "ok", "service": "healer", "tracking": len(SERVICES)}


@app.get("/api/inventory")
async def inventory(service: Optional[str] = None):
    if service:
        svc = next((x for x in SERVICES if x["name"] == service), SERVICES[0])
    else:
        svc = SERVICES[0]
    return await poller.fetch_inventory(svc["url"])


@app.post("/api/analyze-now")
async def analyze_now():
    asyncio.create_task(run_poll_cycle())
    return {"ok": True, "message": f"Polling {len(SERVICES)} service(s)"}


@app.post("/api/webhook/test")
async def test_webhook():
    """Send a test payload to the configured WEBHOOK_URL."""
    if not WEBHOOK_URL:
        raise HTTPException(status_code=400, detail="WEBHOOK_URL is not configured")
    dummy = {
        "id": 0, "service_name": "Test Service", "blast_radius": "CRITICAL",
        "error_types": ["TEST_WEBHOOK"], "trace_id": "test-trace-id",
        "root_cause": "This is a test notification from Healer.",
        "fix_suggestion": "No action required — this is a test.",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "pr_url": None,
    }
    ok = await webhook.send(WEBHOOK_URL, dummy)
    return {"ok": ok, "url": WEBHOOK_URL, "severities": list(WEBHOOK_SEVERITIES)}


@app.get("/api/analytics")
async def analytics(service: Optional[str] = None, hours: Optional[int] = None):
    """Aggregated data for the analytics dashboard."""
    import json as _json
    from collections import defaultdict

    incidents = db.get_incidents(limit=2000, service_name=service)

    # Optional time filter
    if hours:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        incidents = [i for i in incidents if (i.get("detected_at") or "") >= cutoff]

    by_blast:   dict = defaultdict(int)
    by_service: dict = defaultdict(int)
    by_error:   dict = defaultdict(int)
    by_level:   dict = defaultdict(int)
    by_pr:      dict = defaultdict(int)
    by_hour:    dict = defaultdict(lambda: {"total": 0, "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})
    resolved_times: list = []

    for inc in incidents:
        blast = inc.get("blast_radius", "LOW")
        by_blast[blast] += 1
        by_service[inc.get("service_name", "Unknown")] += 1
        by_pr[inc.get("pr_status") or "none"] += 1

        for et in (inc.get("error_types") or []):
            by_error[et] += 1

        for log in (inc.get("raw_logs") or []):
            lvl = log.get("level", "INFO")
            by_level[lvl] += 1

        # Bucket by hour
        ts = (inc.get("detected_at") or "")[:13]  # "2024-05-09T14"
        if ts:
            by_hour[ts]["total"] += 1
            by_hour[ts][blast] += 1

    # Sort top error types
    top_errors = sorted(by_error.items(), key=lambda x: -x[1])[:15]

    # Timeline sorted
    timeline = [
        {"hour": k, **v} for k, v in sorted(by_hour.items())
    ]

    return {
        "total": len(incidents),
        "open": sum(1 for i in incidents if not i.get("resolved")),
        "by_blast_radius": dict(by_blast),
        "by_service": dict(by_service),
        "by_error_type": dict(top_errors),
        "by_log_level": dict(by_level),
        "by_pr_status": dict(by_pr),
        "timeline": timeline,
        "configured_services": [s["name"] for s in SERVICES],
    }


@app.get("/analytics")
async def serve_analytics():
    path = os.path.join(FRONTEND_DIR, "analytics.html")
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "analytics.html not found"})


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        incidents = db.get_incidents(limit=50)
        s = db.get_stats()
        s["target_health"] = await poller.fetch_health(SERVICES[0]["url"])
        services_info = [{"name": sv["name"], "url": sv["url"]} for sv in SERVICES]
        await websocket.send_text(json.dumps({
            "type": "init",
            "incidents": incidents,
            "stats": s,
            "services": services_info,
        }))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Static frontend ───────────────────────────────────────────────────────────

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

@app.get("/")
async def serve_dashboard():
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"message": "Healer API running. Frontend not found."})
