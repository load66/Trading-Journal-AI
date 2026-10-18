"""Owner-only, read-only resource usage for BonusTracker.

No Railway token, GraphQL payload or storage object name is logged or returned.
Cache successful (and unavailable) snapshots for one hour in this process.
"""
from __future__ import annotations

import calendar
import socket
from urllib.error import HTTPError, URLError
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


class UsageFailure(Exception):
    """Safe classification only. Never retain or expose remote response bodies."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


SAFE_REASONS = {
    "token_missing": "Railway account token is not configured.",
    "token_unauthorized": "Railway rejected the account token.",
    "workspace_not_found": "No accessible Railway workspace was found.",
    "project_not_visible": "The Railway account token cannot access this project.",
    "estimate_missing": "Railway did not report all required billing estimates.",
    "measurement_missing": "Railway did not report all required usage measurements.",
    "rate_limited": "Railway is limiting usage queries.",
    "timeout": "Railway did not respond before the request timed out.",
    "railway_unreachable": "Railway could not be reached.",
    "response_changed": "Railway returned an unrecognized usage response.",
}
MEASUREMENTS = ("CPU_USAGE", "MEMORY_USAGE_GB", "NETWORK_TX_GB")
BASIS = "Railway resource-minutes at published rates; before plan credit, base subscription or tax"


def _reason_response(code: str) -> dict:
    code = code if code in SAFE_REASONS else "response_changed"
    return {"status": "unavailable", "reasonCode": code, "reason": SAFE_REASONS[code],
            "costSoFar": None, "projectedMonthEnd": None, "knownCostSubtotal": None,
            "services": [], "missing": []}


def _railway_query(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = UrlRequest(URL, data=body, method="POST",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=12) as response:
            if response.status in (401, 403):
                raise UsageFailure("token_unauthorized")
            if response.status == 429:
                raise UsageFailure("rate_limited")
            if response.status != 200:
                raise UsageFailure("railway_unreachable" if response.status >= 500 else "response_changed")
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise UsageFailure("token_unauthorized") from None
        if exc.code == 429:
            raise UsageFailure("rate_limited") from None
        raise UsageFailure("railway_unreachable" if exc.code >= 500 else "response_changed") from None
    except (TimeoutError, socket.timeout):
        raise UsageFailure("timeout") from None
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise UsageFailure("timeout") from None
        raise UsageFailure("railway_unreachable") from None
    except (OSError, ConnectionError):
        raise UsageFailure("railway_unreachable") from None
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise UsageFailure("response_changed") from None
    if not isinstance(payload, dict):
        raise UsageFailure("response_changed")
    errors = payload.get("errors") or []
    if errors:
        if not isinstance(errors, list):
            raise UsageFailure("response_changed")
        for error in errors:
            if not isinstance(error, dict):
                continue
            ext = error.get("extensions") or {}
            marker = str(ext.get("code", "")).lower() if isinstance(ext, dict) else ""
            msg = str(error.get("message", "")).lower()
            if any(x in marker or x in msg for x in ("unauthoriz", "forbidden", "permission", "unauthenticat", "invalid token", "not authenticated")):
                raise UsageFailure("token_unauthorized")
            if "rate_limit" in marker or "rate limit" in msg or "too many requests" in msg:
                raise UsageFailure("rate_limited")
        raise UsageFailure("response_changed")
    if not isinstance(payload.get("data"), dict):
        raise UsageFailure("response_changed")
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
        return _reason_response("token_missing")
    if not project or not environment:
        return _reason_response("project_not_visible")

    month, start, _ = _month(now)
    verified_at = now.astimezone(timezone.utc).isoformat()
    try:
        workspace_data = _railway_query(token, _WORKSPACES, {})
        if not isinstance(workspace_data, dict) or not isinstance(workspace_data.get("externalWorkspaces"), list):
            raise UsageFailure("response_changed")
        me = workspace_data.get("me") or {}
        if not isinstance(me, dict) or not isinstance(me.get("workspaces"), list):
            raise UsageFailure("response_changed")
        if not workspace_data["externalWorkspaces"] and not me["workspaces"]:
            raise UsageFailure("workspace_not_found")
        workspace = _workspace_for_project(workspace_data, project)
        if not workspace:
            raise UsageFailure("project_not_visible")

        data = _railway_query(token, _QUERY, {
            "workspaceId": workspace, "projectId": project,
            "startDate": start.isoformat(), "endDate": verified_at
        })
        if data.get("project") is None:
            raise UsageFailure("project_not_visible")
        services_node = data["project"].get("services") if isinstance(data["project"], dict) else None
        edges = services_node.get("edges") if isinstance(services_node, dict) else None
        if not isinstance(edges, list) or not edges:
            raise UsageFailure("response_changed")
        names = {}
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict) or not node.get("id") or not node.get("name"):
                raise UsageFailure("response_changed")
            names[str(node["id"])] = str(node["name"])

        usage = data.get("usage")
        estimates = data.get("estimatedUsage")
        if not isinstance(usage, list):
            raise UsageFailure("response_changed")
        if not isinstance(estimates, list):
            raise UsageFailure("estimate_missing")

        by_service = {sid: {} for sid in names}
        for row in usage:
            if not isinstance(row, dict) or not isinstance(row.get("tags"), dict):
                raise UsageFailure("response_changed")
            tags = row["tags"]
            sid = tags.get("serviceId")
            if tags.get("projectId") != project or sid not in names:
                continue
            metric = row.get("measurement")
            if metric not in MEASUREMENTS:
                continue
            value = _valid_number(row.get("value"))
            if value is not None:
                by_service[sid][metric] = by_service[sid].get(metric, 0.0) + value

        projected_metrics = {}
        for row in estimates:
            if not isinstance(row, dict):
                raise UsageFailure("response_changed")
            if row.get("projectId") != project:
                continue
            metric = row.get("measurement")
            if metric not in MEASUREMENTS:
                continue
            value = _valid_number(row.get("estimatedValue"))
            if value is not None:
                projected_metrics[metric] = projected_metrics.get(metric, 0.0) + value

        # Average RAM is supplementary. A failed time-series query cannot
        # invalidate the already verified cost measurements.
        averages = {}
        memory_issue = None
        try:
            memory_data = _railway_query(token, _METRICS, {
                "environmentId": environment, "projectId": project, "startDate": start.isoformat()})
            series = memory_data.get("metrics")
            if not isinstance(series, list):
                raise UsageFailure("response_changed")
            for item in series:
                if not isinstance(item, dict) or not isinstance(item.get("tags"), dict) or not isinstance(item.get("values"), list):
                    continue
                sid = item["tags"].get("serviceId")
                samples = [_valid_number(p.get("value")) for p in item["values"] if isinstance(p, dict)]
                samples = [v for v in samples if v is not None]
                if sid in names and samples:
                    averages[sid] = sum(samples) / len(samples)
        except UsageFailure as exc:
            # Authentication, throttling and outages remain identifiable,
            # without discarding the independently verified cost rows.
            memory_issue = exc.code

        missing = []
        known_subtotal = 0.0
        complete = True
        services = []
        for sid, name in names.items():
            metrics = by_service[sid]
            parts = {}
            for metric in MEASUREMENTS:
                value = metrics.get(metric)
                parts[metric] = value if value is not None else None
                if value is None:
                    missing.append({"service": name, "metric": metric, "kind": "usage"})
            cost = _metric_cost(metrics)
            if cost is None:
                complete = False
            else:
                known_subtotal += cost
            services.append({
                "name": name, "costSoFar": round(cost, 4) if cost is not None else None,
                "measurements": parts, "projectedMonthEnd": None,
                "avgMemoryGb": round(averages[sid], 3) if sid in averages else None,
            })
        for metric in MEASUREMENTS:
            if metric not in projected_metrics:
                missing.append({"service": "project", "metric": metric, "kind": "estimate"})
        if memory_issue:
            missing.append({"service": "project", "metric": "average_memory", "kind": memory_issue})
        projection = _metric_cost(projected_metrics)
        if projection is not None and complete and known_subtotal > 0:
            for row in services:
                row["projectedMonthEnd"] = round(projection * row["costSoFar"] / known_subtotal, 4)
        if projection == 0 and complete and known_subtotal == 0:
            for row in services:
                row["projectedMonthEnd"] = 0.0
        status = "ok" if not missing else "partial"
        result = {
            "status": status, "month": month, "verifiedAt": verified_at, "basis": BASIS,
            "costSoFar": round(known_subtotal, 4) if complete else None,
            "knownCostSubtotal": round(known_subtotal, 4) if any(x["costSoFar"] is not None for x in services) else None,
            "projectedMonthEnd": round(projection, 4) if projection is not None else None,
            "services": services, "missing": missing,
        }
        if missing:
            result["reasonCode"] = "measurement_missing" if any(x["kind"] == "usage" for x in missing) else (
                "estimate_missing" if any(x["kind"] == "estimate" for x in missing) else memory_issue
            )
            result["reason"] = SAFE_REASONS.get(result["reasonCode"], SAFE_REASONS["response_changed"])
        return result
    except UsageFailure as exc:
        return _reason_response(exc.code)
    except Exception:
        # All unexpected failures are classified without exposing messages
        # or remote bodies in logs, API output or the browser.
        return _reason_response("response_changed")

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
