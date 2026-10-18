"""Owner-only, read-only resource usage for BonusTracker.

No Railway token, GraphQL payload or storage object name is logged or returned.
Each Railway field is queried as its own step so a denial is attributed to the
exact field: only a failed token check (me { id }) means the token is rejected.
Complete snapshots are cached for one hour; anything else for two minutes.
"""
from __future__ import annotations

import calendar
import logging
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
log = logging.getLogger("uvicorn.error")
CACHE_SECONDS = 3600
FAILURE_CACHE_SECONDS = 120
REFRESH_INTERVAL_SECONDS = 60
R2_FREE_BYTES = 10_000_000_000
URL = "https://backboard.railway.com/graphql/v2"
# Railway documented billing rates, dollars per unit-minute and per GB of egress.
CPU_PER_MINUTE = 20 / (30 * 24 * 60)
MEMORY_PER_GB_MINUTE = 10 / (30 * 24 * 60)
NETWORK_PER_GB = 0.05
_cache: dict[str, tuple[float, dict]] = {}
_refreshed_at: dict[str, float] = {}
_cache_lock = threading.Lock()


def _month(now: datetime) -> tuple[str, datetime, float]:
    utc = now.astimezone(timezone.utc)
    start = utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_minutes = calendar.monthrange(utc.year, utc.month)[1] * 1440
    elapsed_minutes = max((utc - start).total_seconds() / 60, 1.0)
    return start.strftime("%Y-%m"), start, min(elapsed_minutes / period_minutes, 1.0)


class UsageFailure(Exception):
    """Safe classification only. Never retain or expose remote response bodies.

    "denied" is internal: the caller decides whether a denial means the token is
    invalid (token check step) or one field is not readable (permission_denied).
    """
    def __init__(self, code: str, field: str | None = None, source: str | None = None):
        super().__init__(code)
        self.code = code
        self.field = field
        # Fixed label for how Railway answered (http401, http403, graphql), never remote text.
        self.source = source


SAFE_REASONS = {
    "token_missing": "Railway account token is not configured.",
    "token_unauthorized": "Railway rejected the account token.",
    "permission_denied": "Railway doesn't allow this token to read a required field.",
    "workspace_not_found": "No accessible Railway workspace was found.",
    "project_not_visible": "The Railway account token cannot access this project.",
    "estimate_missing": "Railway did not report all required billing estimates.",
    "measurement_missing": "Railway did not report all required usage measurements.",
    "rate_limited": "Railway is limiting usage queries.",
    "timeout": "Railway did not respond before the request timed out.",
    "railway_unreachable": "Railway could not be reached.",
    "request_blocked": "Railway's network edge blocked the usage request before checking the token.",
    "response_changed": "Railway returned an unrecognized usage response.",
}
# Step/field names are fixed identifiers, never derived from remote text.
FIELDS = ("me", "project", "usage", "estimatedUsage", "metrics")
MEASUREMENTS = ("CPU_USAGE", "MEMORY_USAGE_GB", "NETWORK_TX_GB")
BASIS = "Railway resource-minutes at published rates; before plan credit, base subscription or tax"
PROJECTION_UNAVAILABLE = "Projection not available from Railway"


def _denied_reason(field: str) -> str:
    return f"Railway doesn't allow this token to read {field}." if field in FIELDS else SAFE_REASONS["permission_denied"]


def _reason_response(code: str, field: str | None = None) -> dict:
    code = code if code in SAFE_REASONS else "response_changed"
    field = field if field in FIELDS else None
    result = {"status": "unavailable", "reasonCode": code, "reason": SAFE_REASONS[code],
              "costSoFar": None, "projectedMonthEnd": None, "knownCostSubtotal": None,
              "services": [], "missing": []}
    if field:
        result["failedStep"] = field
    if code == "permission_denied" and field:
        result["deniedFields"] = [field]
        result["reason"] = _denied_reason(field)
    return result


_DENIAL_MARKERS = ("not authorized", "unauthoriz", "forbidden", "permission", "unauthenticat",
                   "invalid token", "not authenticated")


# Python's default urllib User-Agent is commonly refused at Railway's CDN edge
# with an HTML 403 page, which is not an answer about the token.
USER_AGENT = "BonusTracker-usage/1.0 (+https://load66.github.io/bonus-tracker/)"


def _clean_token(raw: str) -> str:
    """Tolerate common paste artifacts: surrounding quotes or a "Bearer " prefix."""
    token = raw.strip().strip('"').strip("'").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _denial_from_status(status: int, content_type: str) -> UsageFailure:
    # A token denial comes back as JSON; an HTML 403 is the edge refusing the request.
    if status == 403 and "json" not in content_type.lower():
        return UsageFailure("request_blocked", source="http403")
    return UsageFailure("denied", source=f"http{status}")


def _railway_query(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = UrlRequest(URL, data=body, method="POST",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                  "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=12) as response:
            if response.status in (401, 403):
                raise _denial_from_status(response.status, str(response.headers.get("Content-Type", "")))
            if response.status == 429:
                raise UsageFailure("rate_limited")
            if response.status != 200:
                raise UsageFailure("railway_unreachable" if response.status >= 500 else "response_changed")
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403):
            headers = exc.headers if exc.headers is not None else {}
            raise _denial_from_status(exc.code, str(headers.get("Content-Type", ""))) from None
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
            # Railway reports authorization denials as HTTP 200 with "Not Authorized".
            if any(x in marker or x in msg for x in _DENIAL_MARKERS):
                raise UsageFailure("denied", source="graphql")
            if "rate_limit" in marker or "rate limit" in msg or "too many requests" in msg:
                raise UsageFailure("rate_limited")
        raise UsageFailure("response_changed")
    if not isinstance(payload.get("data"), dict):
        raise UsageFailure("response_changed")
    return payload["data"]


# One field per query: a non-null field that is denied nulls the whole
# response, so combining fields hides which one Railway refused.
_VERIFY = """
query BonusHostingVerify {
  me { id }
}
"""

_PROJECT = """
query BonusHostingProject($projectId: String!) {
  project(id: $projectId) {
    workspaceId
    services { edges { node { id name } } }
  }
}
"""

_USAGE = """
query BonusHostingUsage($workspaceId: String, $projectId: String, $startDate: DateTime!, $endDate: DateTime!) {
  usage(workspaceId: $workspaceId, projectId: $projectId, startDate: $startDate, endDate: $endDate,
        includeDeleted: true, groupBy: [PROJECT_ID, SERVICE_ID],
        measurements: [CPU_USAGE, MEMORY_USAGE_GB, NETWORK_TX_GB]) {
    measurement
    value
    tags { projectId serviceId }
  }
}
"""

_ESTIMATE = """
query BonusHostingEstimate($workspaceId: String, $projectId: String) {
  estimatedUsage(workspaceId: $workspaceId, projectId: $projectId, includeDeleted: true,
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


def _step(token: str, field: str, query: str, variables: dict) -> dict:
    try:
        data = _railway_query(token, query, variables)
    except UsageFailure as exc:
        raise UsageFailure(exc.code, field, exc.source) from None
    if not isinstance(data, dict):
        raise UsageFailure("response_changed", field)
    return data


def _scoped_step(token: str, field: str, query: str, scopes: list[dict], extra: dict) -> list:
    """Query a billing field, trying the workspace scope then the project scope.

    Only a denial moves on to the next scope; any other failure is final.
    """
    last: UsageFailure | None = None
    for scope in scopes:
        try:
            rows = _step(token, field, query, {**scope, **extra}).get(field)
        except UsageFailure as exc:
            if exc.code != "denied":
                raise
            last = exc
            continue
        if not isinstance(rows, list):
            raise UsageFailure("response_changed", field)
        return rows
    raise last or UsageFailure("response_changed", field)


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


def _log_failure(exc: UsageFailure) -> None:
    # Fixed identifiers only: no token, variables or remote text.
    field = exc.field if exc.field in FIELDS else "unknown"
    code = exc.code if exc.code in SAFE_REASONS or exc.code == "denied" else "response_changed"
    source = exc.source if exc.source in ("http401", "http403", "graphql") else "none"
    log.info("bonus_usage_step_failed step=%s code=%s source=%s", field, code, source)


def _railway_usage(now: datetime) -> dict:
    token = _clean_token(os.getenv("RAILWAY_API_TOKEN", ""))
    project = os.getenv("RAILWAY_PROJECT_ID", "").strip()
    environment = os.getenv("RAILWAY_ENVIRONMENT_ID", "").strip()
    if not token:
        return _reason_response("token_missing")
    if not project or not environment:
        return _reason_response("project_not_visible")

    month, start, _ = _month(now)
    verified_at = now.astimezone(timezone.utc).isoformat()
    try:
        # 1. Token check. Only this step may conclude the token is rejected.
        try:
            me = _step(token, "me", _VERIFY, {}).get("me")
        except UsageFailure as exc:
            if exc.code == "denied":
                raise UsageFailure("token_unauthorized", "me", exc.source) from None
            raise
        if not isinstance(me, dict) or not me.get("id"):
            raise UsageFailure("response_changed", "me")

        # 2. Project services and the workspace that bills it.
        node = _step(token, "project", _PROJECT, {"projectId": project}).get("project")
        if not isinstance(node, dict):
            raise UsageFailure("project_not_visible", "project")
        services_node = node.get("services")
        edges = services_node.get("edges") if isinstance(services_node, dict) else None
        if not isinstance(edges, list) or not edges:
            raise UsageFailure("response_changed", "project")
        names = {}
        for edge in edges:
            item = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                raise UsageFailure("response_changed", "project")
            names[str(item["id"])] = str(item["name"])
        workspace = node.get("workspaceId")
        scopes = ([{"workspaceId": str(workspace), "projectId": None}] if isinstance(workspace, str) and workspace else [])
        scopes.append({"workspaceId": None, "projectId": project})

        # 3. Month-to-date usage. Without it there is no verified cost at all.
        usage = _scoped_step(token, "usage", _USAGE, scopes,
                             {"startDate": start.isoformat(), "endDate": verified_at})
        by_service = {sid: {} for sid in names}
        for row in usage:
            if not isinstance(row, dict) or not isinstance(row.get("tags"), dict):
                raise UsageFailure("response_changed", "usage")
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

        # 4. Railway's month-end estimate. Supplementary: a denial or outage
        # leaves verified month-to-date cost intact and the projection null.
        projected_metrics: dict[str, float] = {}
        estimate_issue: UsageFailure | None = None
        try:
            estimates = _scoped_step(token, "estimatedUsage", _ESTIMATE, scopes, {})
            for row in estimates:
                if not isinstance(row, dict):
                    raise UsageFailure("response_changed", "estimatedUsage")
                if row.get("projectId") != project:
                    continue
                metric = row.get("measurement")
                if metric not in MEASUREMENTS:
                    continue
                value = _valid_number(row.get("estimatedValue"))
                if value is not None:
                    projected_metrics[metric] = projected_metrics.get(metric, 0.0) + value
        except UsageFailure as exc:
            estimate_issue = exc
            projected_metrics = {}
            _log_failure(exc)

        # 5. Average RAM is supplementary as well.
        averages = {}
        memory_issue: UsageFailure | None = None
        try:
            series = _step(token, "metrics", _METRICS, {
                "environmentId": environment, "projectId": project, "startDate": start.isoformat()}).get("metrics")
            if not isinstance(series, list):
                raise UsageFailure("response_changed", "metrics")
            for item in series:
                if not isinstance(item, dict) or not isinstance(item.get("tags"), dict) or not isinstance(item.get("values"), list):
                    continue
                sid = item["tags"].get("serviceId")
                samples = [_valid_number(p.get("value")) for p in item["values"] if isinstance(p, dict)]
                samples = [v for v in samples if v is not None]
                if sid in names and samples:
                    averages[sid] = sum(samples) / len(samples)
        except UsageFailure as exc:
            memory_issue = exc
            _log_failure(exc)

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
        denied = [exc.field for exc in (estimate_issue, memory_issue) if exc and exc.code == "denied"]
        if estimate_issue:
            kind = "permission_denied" if estimate_issue.code == "denied" else estimate_issue.code
            missing.append({"service": "project", "metric": "estimatedUsage", "kind": kind})
        else:
            for metric in MEASUREMENTS:
                if metric not in projected_metrics:
                    missing.append({"service": "project", "metric": metric, "kind": "estimate"})
        if memory_issue:
            kind = "permission_denied" if memory_issue.code == "denied" else memory_issue.code
            missing.append({"service": "project", "metric": "average_memory", "kind": kind})
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
        if projection is None:
            result["projectionNote"] = PROJECTION_UNAVAILABLE
        if missing:
            if any(x["kind"] == "usage" for x in missing):
                code = "measurement_missing"
            elif denied:
                code = "permission_denied"
            elif estimate_issue:
                code = estimate_issue.code if estimate_issue.code in SAFE_REASONS else "response_changed"
            elif any(x["kind"] == "estimate" for x in missing):
                code = "estimate_missing"
            else:
                code = memory_issue.code if memory_issue and memory_issue.code in SAFE_REASONS else "response_changed"
            result["reasonCode"] = code
            result["reason"] = SAFE_REASONS[code]
            if denied:
                result["deniedFields"] = denied
                if code == "permission_denied":
                    result["reason"] = _denied_reason(denied[0])
        return result
    except UsageFailure as exc:
        _log_failure(exc)
        if exc.code == "denied":
            return _reason_response("permission_denied", exc.field)
        return _reason_response(exc.code, exc.field)
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


def _ttl(snapshot: dict) -> int:
    railway = snapshot.get("railway") if isinstance(snapshot, dict) else None
    ok = isinstance(railway, dict) and railway.get("status") == "ok"
    return CACHE_SECONDS if ok else FAILURE_CACHE_SECONDS


def usage_snapshot(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    current_month, _, _ = _month(now)
    result = {
        "month": current_month, "checkedAt": now.isoformat(),
        "railway": _railway_usage(now), "r2": _r2_usage()
    }
    result["cacheSeconds"] = _ttl(result)
    return result


def cached_usage(owner_id: str, refresh: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    key = owner_id + ":" + _month(now)[0]
    current = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        # A manual refresh bypasses the cache at most once a minute per owner.
        bypass = refresh and current - _refreshed_at.get(owner_id, float("-inf")) >= REFRESH_INTERVAL_SECONDS
        if cached and not bypass and current - cached[0] < _ttl(cached[1]):
            return dict(cached[1])
        if refresh and bypass:
            _refreshed_at[owner_id] = current
        # Serialize refreshes to prevent duplicate paid/R2 list operations.
        snapshot = usage_snapshot(now)
        _cache.clear()
        _cache[key] = (current, snapshot)
        return dict(snapshot)


def startup_probe() -> None:
    """Log which Railway step works after a deploy; fixed codes only, nothing cached."""
    railway = _railway_usage(datetime.now(timezone.utc))
    log.info("bonus_usage_probe status=%s reason=%s step=%s denied=%s",
             railway.get("status"), railway.get("reasonCode", "none"), railway.get("failedStep", "none"),
             ",".join(railway.get("deniedFields") or []) or "none")


@router.get("/api/bonus-tracker/usage")
async def hosting_usage(request: Request):
    authenticated_owner = owner(request)
    refresh = request.query_params.get("refresh") == "1"
    value = await run_in_threadpool(cached_usage, authenticated_owner, refresh)
    return JSONResponse(value, headers={"Cache-Control": "private, no-store"})
