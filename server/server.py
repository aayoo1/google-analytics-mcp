"""GA4 MCP - Google Analytics 4 for Claude.

Each person signs in with their own Google account; every API call uses that
person's token, so they only ever see the GA4 properties they already have
access to. Read-only scope (analytics.readonly), no write tools.
"""

import base64
import os
from datetime import date, timedelta

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from mcp.types import Icon

GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "").lower().lstrip("@")
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"

# Cost and quota guardrails. One question must never turn into a huge pull.
DEFAULT_ROW_LIMIT = 1_000
MAX_ROW_LIMIT = 100_000      # hard cap per tool call, whatever is asked for
PAGE_SIZE = 25_000           # rows per Data API request (API allows 250k; smaller is gentler)
MAX_PROPERTIES = 500         # cap on list_properties
MAX_REALTIME_ROWS = 10_000   # realtime has no paging, one request only
SUMMARY_ROWS = 10_000        # rows fetched by the summary tools before trimming to `limit`

QUOTA_NOTE = (
    "GA4 Data API quota (standard property): about 200,000 tokens/day and "
    "40,000/hour per property, max 10 concurrent requests. Long date ranges, "
    "many dimensions and high row limits cost more tokens. Each response includes "
    "property_quota so you can see what is left."
)

ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#fff"/>'
    '<rect x="40" y="10" width="12" height="44" rx="6" fill="#F9AB00"/>'
    '<rect x="24" y="26" width="12" height="28" rx="6" fill="#E37400"/>'
    '<circle cx="14" cy="48" r="6" fill="#E37400"/></svg>'
)
ICON_URI = "data:image/svg+xml;base64," + base64.b64encode(ICON_SVG.encode()).decode()


def _client_storage():
    """Persist OAuth state in Firestore so restarts don't log people out.

    STORAGE_BACKEND=local uses FastMCP's local store instead (for a laptop).
    """
    if os.environ.get("STORAGE_BACKEND", "firestore") == "local":
        return None
    from key_value.aio.stores.firestore import FirestoreStore
    from key_value.aio.stores.firestore.store import (
        FirestoreV1CollectionSanitizationStrategy,
        FirestoreV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    store = FirestoreStore(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
        default_collection="ga4_mcp_oauth",
        # Claude's client ID is a URL ("https://claude.ai/..."); Firestore
        # document IDs can't contain "/", so encode keys before storing.
        # Without these, Connect gives "Internal Server Error".
        key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
        collection_sanitization_strategy=FirestoreV1CollectionSanitizationStrategy(),
    )
    return FernetEncryptionWrapper(
        key_value=store,
        fernet=Fernet(os.environ["STORAGE_ENCRYPTION_KEY"].encode()),
        raise_on_decryption_error=False,
    )


def _allowed_redirects() -> list[str]:
    extra = [u.strip() for u in os.environ.get("EXTRA_REDIRECT_URIS", "").split(",") if u.strip()]
    return [CLAUDE_CALLBACK, *extra]


auth = GoogleProvider(
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    base_url=os.environ["BASE_URL"],  # e.g. https://ga4-mcp-xxxx.run.app
    required_scopes=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        GA_SCOPE,
    ],
    jwt_signing_key=os.environ["JWT_SIGNING_KEY"],
    client_storage=_client_storage(),
    # Only Claude may receive auth codes from this server.
    allowed_client_redirect_uris=_allowed_redirects(),
    # Show the consent page the first time, then remember the approval.
    require_authorization_consent="remember",
)

mcp = FastMCP(
    "Google Analytics 4",
    auth=auth,
    icons=[Icon(src=ICON_URI, mimeType="image/svg+xml")],
    instructions=(
        "Google Analytics 4 data for the signed-in user. Call list_properties first "
        "to get the numeric property_id. Use get_metadata to find valid dimension and "
        "metric names (including custom ones) before run_report. Dates accept "
        "YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo'; defaults end yesterday. "
        "Data for today and yesterday can still change. To combine with Search "
        "Console, match GA4 page paths to the path part of GSC 'page' URLs. "
        "Every tool caps rows; ask for less rather than more. " + QUOTA_NOTE
    ),
)


# ---------------------------------------------------------------- helpers ---

def _creds() -> Credentials:
    token = get_access_token()
    if token is None:
        raise PermissionError("Not signed in.")
    claims = token.claims or {}
    if claims.get("email_verified") is False:
        raise PermissionError("Google says this email address is not verified.")
    if ALLOWED_DOMAIN:
        email = claims.get("email") or ""
        if not email.lower().endswith("@" + ALLOWED_DOMAIN):
            raise PermissionError(f"Only @{ALLOWED_DOMAIN} accounts can use this server.")
    return Credentials(token.token)


def _data():
    return build("analyticsdata", "v1beta", credentials=_creds(), cache_discovery=False)


def _admin():
    return build("analyticsadmin", "v1beta", credentials=_creds(), cache_discovery=False)


def _call(request):
    try:
        return request.execute()
    except HttpError as e:
        if e.status_code == 429:
            raise RuntimeError(
                "GA4 quota exhausted for this property (429). Wait an hour or use a "
                "shorter date range, fewer dimensions or a lower row_limit."
            ) from e
        if e.status_code == 403:
            raise RuntimeError(
                f"No access (403): {e.reason}. Check the property_id with list_properties."
            ) from e
        raise RuntimeError(f"GA4 API error ({e.status_code}): {e.reason}") from e


def _prop(property_id: str | int) -> str:
    p = str(property_id).strip().removeprefix("properties/")
    if not p.isdigit():
        raise ValueError("property_id must be the numeric GA4 property ID from list_properties.")
    return f"properties/{p}"


def _cap(n: int | None, default: int = DEFAULT_ROW_LIMIT) -> int:
    n = default if n is None else int(n)
    return max(1, min(n, MAX_ROW_LIMIT))


def _range(start_date: str | None, end_date: str | None, days: int) -> tuple[str, str]:
    """Default: the last `days` full days, ending yesterday."""
    if days < 1 or days > 3650:
        raise ValueError("days must be between 1 and 3650")
    end = end_date or "yesterday"
    if start_date:
        return start_date, end
    try:
        end_d = date.fromisoformat(end)
    except ValueError:
        end_d = {"today": date.today()}.get(end, date.today() - timedelta(days=1))
        if end.endswith("daysAgo"):
            end_d = date.today() - timedelta(days=int(end[:-7]))
    return (end_d - timedelta(days=days - 1)).isoformat(), end_d.isoformat()


def _prev_range(start: str, end: str) -> tuple[str, str]:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    length = (e - s).days + 1
    pe = s - timedelta(days=1)
    return (pe - timedelta(days=length - 1)).isoformat(), pe.isoformat()


_STRING_OPS = {"exact": "EXACT", "contains": "CONTAINS", "begins_with": "BEGINS_WITH",
               "ends_with": "ENDS_WITH", "regex": "FULL_REGEXP", "partial_regex": "PARTIAL_REGEXP"}
_NUM_OPS = {"eq": "EQUAL", "lt": "LESS_THAN", "lte": "LESS_THAN_OR_EQUAL",
            "gt": "GREATER_THAN", "gte": "GREATER_THAN_OR_EQUAL"}


def _num(v):
    return {"doubleValue": float(v)} if isinstance(v, float) or "." in str(v) else {"int64Value": str(int(v))}


def _one_filter(f: dict) -> dict:
    field = f.get("field")
    op = (f.get("op") or "exact").lower()
    value = f.get("value")
    if not field:
        raise ValueError(f"filter needs 'field': {f}")
    if op in _STRING_OPS:
        inner = {"stringFilter": {"matchType": _STRING_OPS[op], "value": str(value),
                                  "caseSensitive": bool(f.get("case_sensitive", False))}}
    elif op == "in_list":
        vals = value if isinstance(value, list) else [value]
        inner = {"inListFilter": {"values": [str(v) for v in vals]}}
    elif op in _NUM_OPS:
        inner = {"numericFilter": {"operation": _NUM_OPS[op], "value": _num(value)}}
    elif op == "between":
        lo, hi = value
        inner = {"betweenFilter": {"fromValue": _num(lo), "toValue": _num(hi)}}
    elif op == "empty":
        inner = {"emptyFilter": {}}
    else:
        raise ValueError(f"unknown filter op '{op}'")
    expr = {"filter": {"fieldName": field, **inner}}
    return {"notExpression": expr} if f.get("not") else expr


def _filters(filters: list[dict] | None) -> dict | None:
    """List of simple filters, all must match (AND)."""
    if not filters:
        return None
    exprs = [_one_filter(f) for f in filters]
    return exprs[0] if len(exprs) == 1 else {"andGroup": {"expressions": exprs}}


def _parse(resp: dict) -> list[dict]:
    dims = [h["name"] for h in resp.get("dimensionHeaders", [])]
    mets = [(h["name"], h.get("type", "")) for h in resp.get("metricHeaders", [])]
    out = []
    for r in resp.get("rows", []):
        item = {d: v.get("value") for d, v in zip(dims, r.get("dimensionValues", []))}
        for (name, typ), v in zip(mets, r.get("metricValues", [])):
            raw = v.get("value")
            try:
                num = float(raw)
                item[name] = int(num) if typ == "TYPE_INTEGER" or num.is_integer() else round(num, 4)
            except (TypeError, ValueError):
                item[name] = raw
        out.append(item)
    return out


def _quota(resp: dict) -> dict | None:
    q = resp.get("propertyQuota")
    if not q:
        return None
    return {k: v.get("remaining") for k, v in q.items() if isinstance(v, dict)}


def _run(property_id: str, body: dict, row_limit: int) -> dict:
    """runReport with paging, never more than row_limit rows in total."""
    service = _data()
    name = _prop(property_id)
    rows: list[dict] = []
    offset, total, quota = 0, 0, None
    while len(rows) < row_limit:
        page = min(PAGE_SIZE, row_limit - len(rows))
        req = {**body, "limit": str(page), "offset": str(offset), "returnPropertyQuota": True}
        resp = _call(service.properties().runReport(property=name, body=req))
        batch = _parse(resp)
        rows.extend(batch)
        total = int(resp.get("rowCount", len(rows)))
        quota = _quota(resp) or quota
        offset += len(batch)
        if len(batch) < page or offset >= total:
            break
    return {"rows": rows, "row_count": len(rows), "total_rows_available": total,
            "truncated": total > len(rows), "property_quota": quota}


def _dates_body(start: str, end: str) -> list[dict]:
    return [{"startDate": start, "endDate": end}]


def _order(field: str, metric: bool = True, desc: bool = True) -> list[dict]:
    key = "metric" if metric else "dimension"
    sub = {"metricName": field} if metric else {"dimensionName": field}
    return [{key: sub, "desc": desc}]


# ------------------------------------------------------------------ tools ---

@mcp.tool
def whoami() -> dict:
    """Show which Google account this connector is signed in as."""
    token = get_access_token()
    return {"email": (token.claims or {}).get("email") if token else None}


@mcp.tool
def list_properties(name_contains: str | None = None) -> dict:
    """List the GA4 accounts and properties the signed-in user can access.

    Returns property_id (numeric, use it in every other tool), property name,
    account and type. Optional name_contains filters by property or account name.
    Capped at 500 properties.
    """
    service = _admin()
    out: list[dict] = []
    token = None
    while len(out) < MAX_PROPERTIES:
        resp = _call(service.accountSummaries().list(pageSize=200, pageToken=token))
        for acc in resp.get("accountSummaries", []):
            for p in acc.get("propertySummaries", []):
                out.append({
                    "property_id": p["property"].removeprefix("properties/"),
                    "property_name": p.get("displayName"),
                    "property_type": p.get("propertyType"),
                    "account_id": acc.get("account", "").removeprefix("accounts/"),
                    "account_name": acc.get("displayName"),
                })
        token = resp.get("nextPageToken")
        if not token:
            break
    if name_contains:
        n = name_contains.lower()
        out = [p for p in out if n in (p["property_name"] or "").lower()
               or n in (p["account_name"] or "").lower()]
    return {"count": len(out[:MAX_PROPERTIES]), "properties": out[:MAX_PROPERTIES]}


@mcp.tool
def get_metadata(property_id: str, search: str | None = None, custom_only: bool = False,
                 limit: int = 200) -> dict:
    """List valid dimension and metric names for a property, including custom ones.

    Use before run_report when unsure of a field name. search filters by API name,
    UI name or category (case-insensitive). custom_only returns only custom
    dimensions and metrics. limit caps each list (max 1000).
    """
    name = _prop(property_id) + "/metadata"
    resp = _call(_data().properties().getMetadata(name=name))
    limit = max(1, min(int(limit), 1000))

    def pick(items):
        res = []
        for i in items:
            if custom_only and not i.get("customDefinition"):
                continue
            hay = " ".join([i.get("apiName", ""), i.get("uiName", ""), i.get("category", "")]).lower()
            if search and search.lower() not in hay:
                continue
            res.append({"api_name": i.get("apiName"), "ui_name": i.get("uiName"),
                        "category": i.get("category"), "type": i.get("type"),
                        "custom": bool(i.get("customDefinition"))})
        return res

    dims, mets = pick(resp.get("dimensions", [])), pick(resp.get("metrics", []))
    return {"dimension_count": len(dims), "metric_count": len(mets),
            "dimensions": dims[:limit], "metrics": mets[:limit]}


@mcp.tool
def run_report(
    property_id: str,
    metrics: list[str],
    dimensions: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    compare_start_date: str | None = None,
    compare_end_date: str | None = None,
    dimension_filters: list[dict] | None = None,
    metric_filters: list[dict] | None = None,
    order_by: str | None = None,
    descending: bool = True,
    row_limit: int = DEFAULT_ROW_LIMIT,
) -> dict:
    """Run any GA4 report (Data API runReport).

    metrics: up to 10, e.g. ["sessions", "totalUsers", "keyEvents", "totalRevenue"].
    dimensions: up to 9, e.g. ["date", "sessionDefaultChannelGroup", "pagePath"].
    Dates: YYYY-MM-DD, 'today', 'yesterday' or 'NdaysAgo' (default last 28 days
    ending yesterday). Optional compare_start_date/compare_end_date adds a second
    range; rows then carry a dateRange column (date_range_0 = main, date_range_1 = compare).
    Filters are lists of {"field", "op", "value", "not"?, "case_sensitive"?}, all ANDed.
    Dimension ops: exact, contains, begins_with, ends_with, regex, partial_regex,
    in_list (value is a list), empty. Metric ops: eq, gt, gte, lt, lte, between
    (value is [low, high]).
    order_by: a metric or dimension name in the report (default: first metric).
    row_limit: default 1000, hard max 100000 (pages automatically).
    Use get_metadata to check field names.
    Quota (standard property): about 200,000 tokens/day and 40,000/hour per
    property, max 10 concurrent requests. Long date ranges, many dimensions and
    high row limits cost more. The response includes property_quota.
    """
    if not metrics or len(metrics) > 10:
        raise ValueError("Give 1 to 10 metrics.")
    dims = dimensions or []
    if len(dims) > 9:
        raise ValueError("At most 9 dimensions.")
    start, end = _range(start_date, end_date, 28)
    ranges = [{"startDate": start, "endDate": end, "name": "date_range_0"}]
    if compare_start_date and compare_end_date:
        ranges.append({"startDate": compare_start_date, "endDate": compare_end_date,
                       "name": "date_range_1"})
    body = {
        "dateRanges": ranges,
        "dimensions": [{"name": d} for d in dims],
        "metrics": [{"name": m} for m in metrics],
    }
    if (df := _filters(dimension_filters)):
        body["dimensionFilter"] = df
    if (mf := _filters(metric_filters)):
        body["metricFilter"] = mf
    key = order_by or metrics[0]
    body["orderBys"] = _order(key, metric=key not in dims, desc=descending)
    result = _run(property_id, body, _cap(row_limit))
    return {"property_id": str(property_id), "date_ranges": ranges, **result}


@mcp.tool
def realtime_report(
    property_id: str,
    metrics: list[str] | None = None,
    dimensions: list[str] | None = None,
    minutes_ago: int = 30,
    dimension_filters: list[dict] | None = None,
    row_limit: int = 100,
) -> dict:
    """Realtime activity for the last N minutes (max 30 on standard properties).

    metrics default ["activeUsers"]; others: eventCount, screenPageViews, keyEvents.
    dimensions default ["unifiedScreenName"]; others: country, city, deviceCategory,
    platform, eventName, minutesAgo, unifiedScreenClass. Filters as in run_report.
    row_limit default 100, max 10000. Realtime uses its own quota pool
    (similar size to the core one).
    """
    minutes_ago = max(1, min(int(minutes_ago), 30))
    body = {
        "metrics": [{"name": m} for m in (metrics or ["activeUsers"])],
        "dimensions": [{"name": d} for d in (dimensions or ["unifiedScreenName"])],
        "minuteRanges": [{"startMinutesAgo": minutes_ago - 1, "endMinutesAgo": 0}],
        "limit": str(max(1, min(int(row_limit), MAX_REALTIME_ROWS))),
        "returnPropertyQuota": True,
    }
    if (df := _filters(dimension_filters)):
        body["dimensionFilter"] = df
    resp = _call(_data().properties().runRealtimeReport(property=_prop(property_id), body=body))
    rows = _parse(resp)
    return {"property_id": str(property_id), "minutes": minutes_ago, "row_count": len(rows),
            "total_rows_available": int(resp.get("rowCount", len(rows))), "rows": rows,
            "property_quota": _quota(resp)}


@mcp.tool
def top_pages(
    property_id: str,
    days: int = 28,
    start_date: str | None = None,
    end_date: str | None = None,
    page_contains: str | None = None,
    include_title: bool = False,
    limit: int = 50,
) -> dict:
    """Most viewed pages: views, active users, engagement time and key events.

    Sorted by views. page_contains filters pagePath. include_title adds pageTitle
    (can split a path into several rows). limit max 1000.
    """
    start, end = _range(start_date, end_date, days)
    dims = ["pagePath"] + (["pageTitle"] if include_title else [])
    body = {
        "dateRanges": _dates_body(start, end),
        "dimensions": [{"name": d} for d in dims],
        "metrics": [{"name": m} for m in
                    ("screenPageViews", "activeUsers", "userEngagementDuration", "keyEvents")],
        "orderBys": _order("screenPageViews"),
    }
    if page_contains:
        body["dimensionFilter"] = _filters([{"field": "pagePath", "op": "contains", "value": page_contains}])
    res = _run(property_id, body, max(1, min(int(limit), 1000)))
    for r in res["rows"]:
        users = r.get("activeUsers") or 0
        r["avg_engagement_seconds_per_user"] = round(r.pop("userEngagementDuration", 0) / users, 1) if users else 0
    return {"property_id": str(property_id), "start_date": start, "end_date": end, **res}


_SOURCE_DIMS = {"channel": "sessionDefaultChannelGroup", "source_medium": "sessionSourceMedium",
                "source": "sessionSource", "medium": "sessionMedium", "campaign": "sessionCampaignName"}


@mcp.tool
def traffic_sources(
    property_id: str,
    group_by: str = "channel",
    days: int = 28,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
) -> dict:
    """Where sessions come from: sessions, users, engagement, key events, revenue.

    group_by: channel (default channel group), source_medium, source, medium or
    campaign. Sorted by sessions. limit max 1000.
    """
    if group_by not in _SOURCE_DIMS:
        raise ValueError(f"group_by must be one of {list(_SOURCE_DIMS)}")
    start, end = _range(start_date, end_date, days)
    body = {
        "dateRanges": _dates_body(start, end),
        "dimensions": [{"name": _SOURCE_DIMS[group_by]}],
        "metrics": [{"name": m} for m in ("sessions", "totalUsers", "engagedSessions",
                                           "engagementRate", "keyEvents", "totalRevenue")],
        "orderBys": _order("sessions"),
    }
    res = _run(property_id, body, max(1, min(int(limit), 1000)))
    return {"property_id": str(property_id), "group_by": _SOURCE_DIMS[group_by],
            "start_date": start, "end_date": end, **res}


@mcp.tool
def conversions_summary(
    property_id: str,
    days: int = 28,
    start_date: str | None = None,
    end_date: str | None = None,
    by_channel: bool = False,
    limit: int = 100,
) -> dict:
    """Key events (GA4's name for conversions): counts, value and the configured list.

    Returns each key event with its count and event value, optionally split by
    default channel group (by_channel=True). Also lists which events are marked
    as key events in the property. limit max 1000.
    """
    start, end = _range(start_date, end_date, days)
    dims = ["eventName"] + (["sessionDefaultChannelGroup"] if by_channel else [])
    body = {
        "dateRanges": _dates_body(start, end),
        "dimensions": [{"name": d} for d in dims],
        "metrics": [{"name": "keyEvents"}, {"name": "eventValue"}, {"name": "totalUsers"}],
        "metricFilter": _filters([{"field": "keyEvents", "op": "gt", "value": 0}]),
        "orderBys": _order("keyEvents"),
    }
    res = _run(property_id, body, max(1, min(int(limit), 1000)))
    configured = None
    try:
        ke = _call(_admin().properties().keyEvents().list(parent=_prop(property_id), pageSize=200))
        configured = [k.get("eventName") for k in ke.get("keyEvents", [])]
    except Exception:  # Admin read can fail on some property types; report data anyway
        pass
    return {"property_id": str(property_id), "start_date": start, "end_date": end,
            "configured_key_events": configured,
            "total_key_events": sum(r.get("keyEvents", 0) for r in res["rows"]), **res}


@mcp.tool
def compare_periods(
    property_id: str,
    dimension: str = "pagePath",
    metric: str = "sessions",
    days: int = 28,
    end_date: str | None = None,
    limit: int = 50,
) -> dict:
    """Biggest gains and losses for one metric: last N days vs the N days before.

    dimension: any GA4 dimension, e.g. pagePath, landingPage,
    sessionDefaultChannelGroup, sessionSourceMedium, country, deviceCategory.
    metric: e.g. sessions, totalUsers, screenPageViews, keyEvents, totalRevenue.
    One API request with two date ranges (quota friendly), top 10,000 rows per range.
    """
    cur_start, cur_end = _range(None, end_date, days)
    prev_start, prev_end = _prev_range(cur_start, cur_end)
    body = {
        "dateRanges": [{"startDate": cur_start, "endDate": cur_end, "name": "current"},
                       {"startDate": prev_start, "endDate": prev_end, "name": "previous"}],
        "dimensions": [{"name": dimension}],
        "metrics": [{"name": metric}],
        "orderBys": _order(metric),
    }
    res = _run(property_id, body, SUMMARY_ROWS * 2)
    cur: dict[str, float] = {}
    prev: dict[str, float] = {}
    for r in res["rows"]:
        (cur if r.get("dateRange") == "current" else prev)[r.get(dimension)] = r.get(metric) or 0
    changes = []
    for key in set(cur) | set(prev):
        now, before = cur.get(key, 0), prev.get(key, 0)
        changes.append({
            dimension: key, f"{metric}_now": now, f"{metric}_before": before,
            "change": round(now - before, 4),
            "change_pct": round((now - before) / before * 100, 1) if before else None,
        })
    changes.sort(key=lambda r: r["change"])
    limit = max(1, min(int(limit), 500))
    total_now, total_before = sum(cur.values()), sum(prev.values())
    return {
        "property_id": str(property_id), "dimension": dimension, "metric": metric,
        "current": [cur_start, cur_end], "previous": [prev_start, prev_end],
        "total_now": round(total_now, 4), "total_before": round(total_before, 4),
        "total_change_pct": round((total_now - total_before) / total_before * 100, 1) if total_before else None,
        "biggest_losses": [c for c in changes[:limit] if c["change"] < 0],
        "biggest_gains": [c for c in reversed(changes[-limit:]) if c["change"] > 0],
        "truncated": res["truncated"], "property_quota": res["property_quota"],
    }


@mcp.tool
def landing_page_performance(
    property_id: str,
    days: int = 28,
    start_date: str | None = None,
    end_date: str | None = None,
    organic_search_only: bool = False,
    page_contains: str | None = None,
    min_sessions: int = 0,
    limit: int = 50,
) -> dict:
    """Landing pages: sessions, engagement, bounce rate, key events, revenue.

    organic_search_only=True keeps only the 'Organic Search' channel, which is
    the set to compare with Search Console. To combine with GSC: call the Search
    Console connector's search_analytics with dimensions ["page"] for the same
    dates, then match each GSC page URL's path to page_path here. That shows
    clicks and position next to what visitors did after landing. Sorted by
    sessions. limit max 1000.
    """
    start, end = _range(start_date, end_date, days)
    filters = []
    if organic_search_only:
        filters.append({"field": "sessionDefaultChannelGroup", "op": "exact", "value": "Organic Search"})
    if page_contains:
        filters.append({"field": "landingPage", "op": "contains", "value": page_contains})
    body = {
        "dateRanges": _dates_body(start, end),
        "dimensions": [{"name": "landingPage"}],
        "metrics": [{"name": m} for m in ("sessions", "totalUsers", "engagementRate", "bounceRate",
                                           "averageSessionDuration", "keyEvents",
                                           "sessionKeyEventRate", "totalRevenue")],
        "orderBys": _order("sessions"),
    }
    if (df := _filters(filters)):
        body["dimensionFilter"] = df
    if min_sessions > 0:
        body["metricFilter"] = _filters([{"field": "sessions", "op": "gte", "value": min_sessions}])
    res = _run(property_id, body, max(1, min(int(limit), 1000)))
    for r in res["rows"]:
        r["page_path"] = (r.pop("landingPage", "") or "").split("?")[0] or "/"
    return {"property_id": str(property_id), "start_date": start, "end_date": end,
            "organic_search_only": organic_search_only, **res}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
