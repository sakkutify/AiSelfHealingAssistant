from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


class LogEntry(BaseModel):
    timestamp: Optional[str] = None
    trace_id: Optional[str] = "unknown"
    service: Optional[str] = None
    error_type: Optional[str] = None
    message: Optional[str] = ""
    level: Optional[str] = "INFO"
    class_name: Optional[str] = None
    thread: Optional[str] = None


class IncidentCreate(BaseModel):
    trace_id: str
    detected_at: str
    error_types: List[str]
    root_cause: str
    blast_radius: str
    fix_suggestion: str
    pr_description: Optional[str] = None
    raw_logs: List[Any]


class Incident(IncidentCreate):
    id: int
    resolved: bool = False
    created_at: Optional[str] = None


class HealthStatus(BaseModel):
    status: str
    services: dict


class Stats(BaseModel):
    total_incidents: int
    open_incidents: int
    by_severity: dict
    by_error_type: dict
    target_health: Optional[dict] = None
