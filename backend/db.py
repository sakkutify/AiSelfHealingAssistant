import sqlite3
import json
import os
from pathlib import Path
from typing import List, Optional, Dict, Any

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "healer.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS processed_logs (
            log_hash TEXT PRIMARY KEY,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL DEFAULT 'Default',
            trace_id TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            error_types TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            blast_radius TEXT NOT NULL,
            fix_suggestion TEXT NOT NULL,
            pr_description TEXT,
            raw_logs TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            pr_status TEXT DEFAULT 'none',
            pr_url TEXT,
            pr_number INTEGER,
            pr_branch TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Migrate existing DB: add columns if missing
    for col, definition in [
        ("service_name", "TEXT NOT NULL DEFAULT 'Default'"),
        ("pr_status",    "TEXT DEFAULT 'none'"),
        ("pr_url",       "TEXT"),
        ("pr_number",    "INTEGER"),
        ("pr_branch",    "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE incidents ADD COLUMN {col} {definition}")
        except Exception:
            pass
    c.execute("CREATE INDEX IF NOT EXISTS idx_incidents_trace ON incidents(trace_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_incidents_blast ON incidents(blast_radius)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service_name)")
    conn.commit()
    conn.close()


def is_log_processed(log_hash: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed_logs WHERE log_hash = ?", (log_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None


def mark_logs_processed(hashes: List[str]):
    conn = get_conn()
    c = conn.cursor()
    c.executemany(
        "INSERT OR IGNORE INTO processed_logs (log_hash) VALUES (?)",
        [(h,) for h in hashes]
    )
    conn.commit()
    conn.close()


def save_incident(data: Dict[str, Any]) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO incidents
            (service_name, trace_id, detected_at, error_types, root_cause,
             blast_radius, fix_suggestion, pr_description, raw_logs,
             pr_status, pr_url, pr_number, pr_branch)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("service_name", "Default"),
        data["trace_id"],
        data["detected_at"],
        json.dumps(data["error_types"]),
        data["root_cause"],
        data["blast_radius"],
        data["fix_suggestion"],
        data.get("pr_description"),
        json.dumps(data["raw_logs"]),
        data.get("pr_status", "none"),
        data.get("pr_url"),
        data.get("pr_number"),
        data.get("pr_branch"),
    ))
    incident_id = c.lastrowid
    conn.commit()
    conn.close()
    return incident_id


def update_incident_pr(incident_id: int, pr_status: str, pr_url: Optional[str],
                       pr_number: Optional[int], pr_branch: Optional[str]):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE incidents SET pr_status=?, pr_url=?, pr_number=?, pr_branch=? WHERE id=?",
        (pr_status, pr_url, pr_number, pr_branch, incident_id),
    )
    conn.commit()
    conn.close()


def get_incidents(
    limit: int = 100,
    resolved: Optional[bool] = None,
    service_name: Optional[str] = None,
) -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    conditions = []
    params: List[Any] = []

    if resolved is not None:
        conditions.append("resolved = ?")
        params.append(1 if resolved else 0)
    if service_name and service_name != "all":
        conditions.append("service_name = ?")
        params.append(service_name)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    c.execute(f"SELECT * FROM incidents {where} ORDER BY detected_at DESC LIMIT ?", params)
    rows = c.fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_incident_by_id(incident_id: int) -> Optional[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    row = c.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def resolve_incident(incident_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE incidents SET resolved = 1 WHERE id = ?", (incident_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_stats(service_name: Optional[str] = None) -> Dict:
    conn = get_conn()
    c = conn.cursor()
    where = ""
    params: List[Any] = []
    if service_name and service_name != "all":
        where = "WHERE service_name = ?"
        params = [service_name]

    c.execute(f"SELECT COUNT(*) FROM incidents {where}", params)
    total = c.fetchone()[0]
    c.execute(f"SELECT COUNT(*) FROM incidents {where} {'AND' if where else 'WHERE'} resolved = 0",
              params + [])
    # Rebuild properly
    if where:
        c.execute("SELECT COUNT(*) FROM incidents WHERE service_name = ? AND resolved = 0", params)
    else:
        c.execute("SELECT COUNT(*) FROM incidents WHERE resolved = 0")
    open_count = c.fetchone()[0]

    if where:
        c.execute("SELECT blast_radius, COUNT(*) FROM incidents WHERE service_name = ? GROUP BY blast_radius", params)
    else:
        c.execute("SELECT blast_radius, COUNT(*) FROM incidents GROUP BY blast_radius")
    by_severity = {row[0]: row[1] for row in c.fetchall()}

    if where:
        c.execute("SELECT error_types FROM incidents WHERE service_name = ?", params)
    else:
        c.execute("SELECT error_types FROM incidents")
    all_types: Dict[str, int] = {}
    for row in c.fetchall():
        for et in json.loads(row[0]):
            all_types[et] = all_types.get(et, 0) + 1

    conn.close()
    return {
        "total_incidents": total,
        "open_incidents": open_count,
        "by_severity": by_severity,
        "by_error_type": all_types,
    }


def get_service_names() -> List[str]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT service_name FROM incidents ORDER BY service_name")
    names = [row[0] for row in c.fetchall()]
    conn.close()
    return names


def _row_to_dict(row) -> Dict:
    d = dict(row)
    d["error_types"] = json.loads(d["error_types"])
    d["raw_logs"] = json.loads(d["raw_logs"])
    d["resolved"] = bool(d["resolved"])
    return d
