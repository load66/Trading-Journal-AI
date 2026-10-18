"""Owner-only, read-only resource usage for BonusTracker.

No Railway token, GraphQL payload or storage object name is logged or returned.
Cache successful (and unavailable) snapshots for one hour in this process.
"""
from __future__ import annotations

import calendar
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bonus_backups import owner, store

router = APIRouter()
CACHE_SECONDS = 3600
R2_FREE_BYTES = 10_000_000_000
URL = "https://backboard.railway.com/graphql/v2"
# Railway documented billing rates, dollars per unit-minute and per GB of egress.
CPU_PER_MINUTE = 20 / (30 * 24 * 60)
MEMORY_PER_GB_MINUTE = 10 / (30 * 24 * 60)
NETWORK_PER_GB = 0.05
_cache: dict[str, object] = {}
_cache_lock = threading.Lock()


def _month(now: datetime) -> tuple[str, datetime, float]:
    utc = now.astimezone(timezone.utc)
    start = utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_minutes = calendar.monthrange(utc.year, utc.month)[1] * 1440
    elapsed_minutes = max((utc - start).total_seconds() / 60, 1.0)
    return start.strftime("%Y-%m"), start, min(elapsed_minutes / period_minutes, 1.0)


def _railway_query(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = UrlRequest(URL, data=body, method="POST",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                         )
    with urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise ValueError("Railway API unavailable")
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
        raise ValueError("Railway API response invalid")
    return payload["data"]


_WORKSPACES = """
query BonusHostingWorkspaces {
  externalWorkspaces { id projects { id } }
  me { workspaces { id projects(first: 500) { edges { node { id } } } } }
}
"""

_QUERY = """
query BonusHosting($workspaceId: String!, $projectId: String!, $startDate: DateTime!, $endDate: DateTime!) {
  project(id: $projectId) {
    services { edges { node { id name } } }
  }
  usage(workspaceId: $workspaceId, startDate: $startDate, endDate: $endDate,
        includeDeleted: true, groupBy: [PROJECT_ID, SERVICE_ID],
        measurements: [CPU_USAGE, MEMORY_USAGE_GB, NETWORK_TX_GB]) {
    measurement
    value
    tags { projectId serviceId }
  }
  estimatedUsage(workspaceId: $workspaceId, includeDeleted: true,
                 measurements: [CPU_USAGE, MEMORY_USAGE_GB, NETWORK_TX_GB]) {
    measurement
    estimatedValue
    projectId
  }
}
"""

_METRICS = """
query BonusHostingMemory($environmentId: String!, $projectId: String!, $startDate: DateTime!) {
  metrics(environmentId: $environmentId, projectId: $projectId, startDate: $startDate,
          groupBy: [SERVICE_ID], measurements: [MEMORY_USAGE_GB]) {
    measurement
    tags { serviceId }
    values { ts value }
  }
}
"""


def _valid_number(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if not 0 <= x < 1e14:
        return None
    return float(x)


def _metric_cost(measurements: dict[str, float]) -> float | None:
    # Usage metrics are aggregated resource-minutes; the public price card lists
    # vCPU/GB per minute. This excludes discounts, taxes, and base subscription.
    if not measurements:
        return None
    cpu = measurements.get("CPU_USAGE")
    memory = measurements.get("MEMORY_USAGE_GB")
    network = measurements.get("NETWORK_TX_GB")
    if cpu is None or memory is None or network is None:
        return None
    return cpu * CPU_PER_MINUTE + memory * MEMORY_PER_GB_MINUTE + network * NETWORK_PER_GB


def _workspace_for_project(payload: dict, project_id: str) -> str | None:
    candidates = []
    external = payload.get("externalWorkspaces")
    if isinstance(external, list):
        candidates.extend(external)
    member = (payload.get("me") or {}).get("workspaces")
    if isinstance(member, list):
        candidates.extend(member)
    for workspace in candidates:
        if not isinstance(workspace, dict) or not workspace.get("id"):
            continue
        projects = workspace.get("projects") or []
        if isinstance(projects, dict):
            projects = [edge.get("node") for edge in projects.get("edges") or [] if isinstance(edge, dict)]
        if any(isinstance(item, dict) and item.get("id") == project_id for item in projects):
            return str(workspace["id"])
    return None


def _railway_usage(now: datetime) -> dict:
    token = os.getenv("RAILWAY_API_TOKEN", "").strip()
    project = os.getenv("RAILWAY_PROJECT_ID", "").strip()
    environment = os.getenv("RAILWAY_ENVIRONMENT_ID", "").strip()
    if not token:
        return {"status": "not_configured", "services": [], "costSoFar": None,
                "projectedMonthEnd": None, "reason": "Railway API token not configured"}
    if not project or not environment:
        return {"status": "unavailable", "services": [], "costSoFar": None,
                "projectedMonthEnd": None, "reason": "Railway project context unavailable"}
    month, start, _fraction = _month(now)
    try:
        workspace_data = _railway_query(token, _WORKSPACES, {})
        workspace = _workspace_for_project(workspace_data, project)
        if not workspace:
            raise ValueError("Railway workspace could not be resolved")
        data = _railway_query(token, _QUERY, {
            "workspaceId": workspace, "projectId": project,
            "startDate": start.isoformat(), "endDate": now.astimezone(timezone.utc).isoformat()
        })
        edges = ((data.get("project") or {}).get("services") or {}).get("edges") or []
        names = {str(edge["node"]["id"]): str(edge["node"]["name"]) for edge in edges
                 if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
                 and edge["node"].get("id") and edge["node"].get("name")}
        usage = data.get("usage")
        if not isinstance(usage, list) or not names:
            raise ValueError("No matching Railway project usage")
        by_service = {name: {} for name in names.values()}
        for row in usage:
            if not isinstance(row, dict):
                raise ValueError("Invalid usage line")
            sid = (row.get("tags") or {}).get("serviceId")
            row_project = (row.get("tags") or {}).get("projectId")
            metric = row.get("measurement")
            value = _valid_number(row.get("value"))
            if row_project != project or sid not in names or metric not in ("CPU_USAGE", "MEMORY_USAGE_GB", "NETWORK_TX_GB") or value is None:
                continue
            by_service[names[sid]][metric] = by_service[names[sid]].get(metric, 0) + value

        # A separate time series is required for average RAM; the per-month
        # usage aggregate is not a point-in-time RAM reading.
        memory_data = _railway_query(token, _METRICS, {
            "environmentId": environment, "projectId": project, "startDate": start.isoformat()})
        averages: dict[str, float] = {}
        for series in memory_data.get("metrics") or []:
            sid = (series.get("tags") or {}).get("serviceId")
            samples = [_valid_number(p.get("value")) for p in series.get("values", []) if isinstance(p, dict)]
            samples = [v for v in samples if v is not None]
            if sid in names and samples:
                averages[names[sid]] = sum(samples) / len(samples)
        # Use Railway's own current-billing-cycle estimate, not a homegrown
        # linear projection from a partial month. Missing/invalid estimates
        # are unavailable rather than inferred as zero.
        estimate_rows = data.get("estimatedUsage")
        if not isinstance(estimate_rows, list):
            raise ValueError("Railway billing estimate unavailable")
        projected_metrics = {}
        for row in estimate_rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid Railway estimated usage line")
            # Railway can return workspace-level measurements; only the
            # requested project is attributable to this card.
            if row.get("projectId") != project:
                continue
            metric = row.get("measurement")
            amount = _valid_number(row.get("estimatedValue"))
            if metric in ("CPU_USAGE", "MEMORY_USAGE_GB", "NETWORK_TX_GB") and amount is not None:
                projected_metrics[metric] = projected_metrics.get(metric, 0.0) + amount
        projected = _metric_cost(projected_metrics)
        if projected is None:
            raise ValueError("Incomplete Railway billing estimate")
        services = []
        for name, metrics in by_service.items():
            cost = _metric_cost(metrics)
            services.append({"name": name, "costSoFar": round(cost, 4) if cost is not None else None,
                             "projectedMonthEnd": None,
                             "avgMemoryGb": round(averages[name], 3) if name in averages else None})
        # If one service lacks billable measurements, never describe the
        # project total as complete. Distribute Railway's aggregate projection
        # across services in proportion to their verified accrued cost.
        if not services or any(s["costSoFar"] is None for s in services):
            raise ValueError("Railway usage measurements incomplete")
        total = sum(s["costSoFar"] for s in services)
        if total > 0:
            for item in services:
                item["projectedMonthEnd"] = round(projected * item["costSoFar"] / total, 4)
        return {"status": "ok", "month": month, "costSoFar": round(total, 4),
                "projectedMonthEnd": round(projected, 4), "services": services,
                "basis": "Railway's projected resource usage at published rates; before plan credits, base subscription or tax"}
    except Exception as error:
        reason = "workspace_resolution_failed" if "workspace" in str(error).lower() else "railway_query_failed"
        return {"status": "unavailable", "services": [], "costSoFar": None,
                "projectedMonthEnd": None, "reason": "Railway usage unavailable", "reasonCode": reason}


def _r2_usage() -> dict:
    try:
        summary = store().usage_summary()
        count = summary.get("stored_bytes")
        if not isinstance(count, int) or count < 0:
            raise ValueError("Invalid object listing")
        return {"status": "ok", "usedBytes": count, "freeBytes": R2_FREE_BYTES,
                "objectCount": summary.get("object_count")}
    except Exception:
        return {"status": "unavailable", "usedBytes": None, "freeBytes": R2_FREE_BYTES,
                "objectCount": None}


def usage_snapshot(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    current_month, _, _ = _month(now)
    result = {
        "month": current_month, "checkedAt": now.isoformat(), "cacheSeconds": CACHE_SECONDS,
        "railway": _railway_usage(now), "r2": _r2_usage()
    }
    return result


def cached_usage(owner_id: str) -> dict:
    now = datetime.now(timezone.utc)
    key = owner_id + ":" + _month(now)[0]
    current = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and current - cached[0] < CACHE_SECONDS:
            return dict(cached[1])
        # Serialize refreshes to prevent duplicate paid/R2 list operations.
        snapshot = usage_snapshot(now)
        _cache.clear()
        _cache[key] = (current, snapshot)
        return dict(snapshot)


@router.get("/api/bonus-tracker/usage")
async def hosting_usage(request: Request):
    authenticated_owner = owner(request)
    value = await run_in_threadpool(cached_usage, authenticated_owner)
    return JSONResponse(value, headers={"Cache-Control": "private, no-store"})
