# Healer — AI Self-Healing Incident Assistant

Automatically detects, analyses, and explains incidents from a Spring Boot e-commerce backend using Claude AI.

## Run with Docker

```
docker compose up --build
```

Dashboard at **http://localhost:8000**

Set env vars inline if you don't have a `.env` file:
```
ANTHROPIC_API_KEY=sk-ant-... TARGET_URL=http://host.docker.internal:8080 docker compose up --build
```

> `host.docker.internal` lets the container reach the Spring Boot app running on your host machine.

---

## Prerequisites

- Python 3.11+
- An Anthropic API key
- Spring Boot target running on `http://localhost:8080`

## Setup

**1. Clone and enter the project**
```
cd d:\AIHackathon\Healer
```

**2. Create your `.env` file**
```
copy .env.example .env
```
Edit `.env` and set your key:
```
ANTHROPIC_API_KEY=sk-ant-...
```

**3. Install dependencies**
```
pip install -r requirements.txt
```

## Run the Server

```
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Open the dashboard at **http://localhost:8001**

## How It Works

- Polls `http://localhost:8080/logs` every 30 seconds
- Deduplicates logs by hash to avoid repeat alerts
- Groups logs by `trace_id` and sends incident-worthy groups to Claude
- Claude returns: root cause, blast radius (LOW / MEDIUM / HIGH / CRITICAL), fix suggestion, and PR description
- Results stream live to the dashboard via WebSocket

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard UI |
| GET | `/api/incidents` | List all incidents |
| GET | `/api/incidents/{id}` | Get single incident |
| POST | `/api/incidents/{id}/resolve` | Mark resolved |
| GET | `/api/stats` | Counts + severity breakdown |
| GET | `/api/target-health` | Spring Boot health status |
| POST | `/api/analyze-now` | Trigger immediate poll |
| WS | `/ws` | Real-time incident feed |
| GET | `/docs` | Swagger UI |
