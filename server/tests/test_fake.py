"""Local test with fake GA4 data. No Google account or network needed.

    STORAGE_BACKEND=local python tests/test_fake.py
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

os.environ.update(
    STORAGE_BACKEND="local",
    GOOGLE_CLIENT_ID="fake.apps.googleusercontent.com",
    GOOGLE_CLIENT_SECRET="fake",
    BASE_URL="http://localhost:8080",
    JWT_SIGNING_KEY="test-key-" + "x" * 40,
    ALLOWED_DOMAIN="example.com",
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402
from fastmcp import Client  # noqa: E402

CALLS: list[tuple[str, dict]] = []
EMAIL = {"value": "alice@example.com"}


def fake_token():
    return SimpleNamespace(token="fake-token", claims={"email": EMAIL["value"]})


class Req:
    def __init__(self, fn, kw):
        self.fn, self.kw = fn, kw

    def execute(self):
        CALLS.append((self.fn, self.kw))
        return RESPONSES[self.fn](self.kw)


class Node:
    def __init__(self, path=""):
        self.path = path

    def __getattr__(self, name):
        def f(**kw):
            fn = f"{self.path}.{name}".lstrip(".")
            return Req(fn, kw) if fn in RESPONSES else Node(fn)
        return f


def report(kw):
    body = kw["body"]
    dims = [d["name"] for d in body.get("dimensions", [])]
    mets = [m["name"] for m in body["metrics"]]
    ranges = body.get("dateRanges", [{}])
    if len(ranges) > 1:
        dims = dims + ["dateRange"]
    total = 2500  # pretend the property has 2500 rows for any query
    offset, limit = int(body["offset"]), int(body["limit"])
    rows = []
    for i in range(offset, min(offset + limit, total)):
        for ri, rng in enumerate(ranges):
            dv = [f"/page-{i}?utm=x" if d in ("landingPage", "pagePath") else f"{d}-{i}" for d in dims[: len(dims) - (len(ranges) > 1)]]
            if len(ranges) > 1:
                dv.append(rng.get("name", f"date_range_{ri}"))
            rows.append({
                "dimensionValues": [{"value": v} for v in dv],
                "metricValues": [{"value": str((i % 7) * 10 + (5 if ri == 0 else 0) + (0.5 if m.endswith("Rate") else 0))} for m in mets],
            })
    return {
        "dimensionHeaders": [{"name": d} for d in dims],
        "metricHeaders": [{"name": m, "type": "TYPE_FLOAT" if m.endswith("Rate") else "TYPE_INTEGER"} for m in mets],
        "rows": rows, "rowCount": total,
        "propertyQuota": {"tokensPerDay": {"consumed": 10, "remaining": 199990}},
    }


RESPONSES = {
    "accountSummaries.list": lambda kw: {
        "accountSummaries": [{"account": "accounts/1", "displayName": "Account A",
                              "propertySummaries": [{"property": "properties/111", "displayName": "site-a.example", "propertyType": "PROPERTY_TYPE_ORDINARY"}]},
                             {"account": "accounts/2", "displayName": "Account B",
                              "propertySummaries": [{"property": "properties/222", "displayName": "site-b.example"}]}]},
    "properties.runReport": report,
    "properties.runRealtimeReport": lambda kw: {
        "dimensionHeaders": [{"name": "unifiedScreenName"}], "metricHeaders": [{"name": "activeUsers", "type": "TYPE_INTEGER"}],
        "rows": [{"dimensionValues": [{"value": "Home"}], "metricValues": [{"value": "3"}]}], "rowCount": 1},
    "properties.getMetadata": lambda kw: {
        "dimensions": [{"apiName": "pagePath", "uiName": "Page path", "category": "Page / screen"},
                       {"apiName": "customEvent:location", "uiName": "Location", "category": "Custom", "customDefinition": True}],
        "metrics": [{"apiName": "sessions", "uiName": "Sessions", "category": "Session", "type": "TYPE_INTEGER"}]},
    "properties.keyEvents.list": lambda kw: {"keyEvents": [{"eventName": "sign_up"}]},
}


def fake_build(api, version, **_):
    assert api in ("analyticsdata", "analyticsadmin"), api
    return Node()


server.get_access_token = fake_token
server.build = fake_build


async def main():
    ok = 0

    def check(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1
        print("  ok:", msg)

    async with Client(server.mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
        expected = {"whoami", "list_properties", "get_metadata", "run_report", "realtime_report", "top_pages",
                    "traffic_sources", "conversions_summary", "compare_periods", "landing_page_performance"}
        check(set(tools) == expected, f"exactly the 10 tools: {sorted(tools)}")
        check(all(t.description for t in tools.values()), "every tool has a description")
        check("200,000" in tools["run_report"].description, "run_report describes the quota")

        async def call(name, **args):
            r = await c.call_tool(name, args)
            return r.data if r.data is not None else json.loads(r.content[0].text)

        r = await call("list_properties")
        check(r["count"] == 2 and r["properties"][0]["property_id"] == "111", "list_properties flattens accounts")
        r = await call("list_properties", name_contains="account b")
        check(r["count"] == 1, "list_properties name filter")

        r = await call("get_metadata", property_id="111", custom_only=True)
        check(r["dimension_count"] == 1 and r["dimensions"][0]["custom"], "get_metadata custom_only")

        CALLS.clear()
        r = await call("run_report", property_id="properties/111", metrics=["sessions"], dimensions=["pagePath"])
        check(r["row_count"] == 1000 and r["truncated"] and len(CALLS) == 1, "run_report default row_limit 1000, one request")
        check(r["property_quota"]["tokensPerDay"] == 199990, "run_report returns property_quota")

        CALLS.clear()
        r = await call("run_report", property_id="111", metrics=["sessions"], dimensions=["pagePath"], row_limit=10_000_000)
        check(r["row_count"] == 2500 and not r["truncated"], "run_report pages and stops at end of data")
        check(all(int(kw["body"]["limit"]) <= server.PAGE_SIZE for _, kw in CALLS), "each request <= PAGE_SIZE rows")
        check(server._cap(10_000_000) == 100_000, "hard cap is 100000")

        CALLS.clear()
        await call("run_report", property_id="111", metrics=["sessions", "keyEvents"], dimensions=["sessionSource"],
                   dimension_filters=[{"field": "sessionSource", "op": "contains", "value": "google"},
                                      {"field": "country", "op": "in_list", "value": ["Denmark", "Sweden"], "not": True}],
                   metric_filters=[{"field": "sessions", "op": "gte", "value": 10}],
                   start_date="2026-10-01", end_date="2026-10-31", compare_start_date="2026-09-01", compare_end_date="2026-09-30")
        body = CALLS[0][1]["body"]
        check(len(body["dimensionFilter"]["andGroup"]["expressions"]) == 2, "dimension filters ANDed")
        check("notExpression" in body["dimensionFilter"]["andGroup"]["expressions"][1], "not filter")
        check(body["metricFilter"]["filter"]["numericFilter"]["value"] == {"int64Value": "10"}, "metric filter")
        check(len(body["dateRanges"]) == 2, "compare date range added")

        for bad in [dict(metrics=[]), dict(metrics=["m"] * 11), dict(metrics=["sessions"], dimensions=["d"] * 10)]:
            try:
                await c.call_tool("run_report", {"property_id": "111", **bad})
                raise AssertionError("should fail")
            except Exception as e:
                assert "should fail" not in str(e)
        check(True, "run_report rejects bad metric/dimension counts")
        try:
            await c.call_tool("run_report", {"property_id": "abc", "metrics": ["sessions"]})
            raise AssertionError("should fail")
        except Exception as e:
            check("should fail" not in str(e), "rejects non-numeric property_id")

        r = await call("realtime_report", property_id="111", minutes_ago=999, row_limit=10**9)
        body = CALLS[-1][1]["body"]
        check(r["minutes"] == 30 and int(body["limit"]) == 10_000, "realtime caps minutes and rows")

        r = await call("top_pages", property_id="111", limit=5000)
        check(r["row_count"] == 1000 and "avg_engagement_seconds_per_user" in r["rows"][0], "top_pages capped at 1000, adds engagement time")

        r = await call("traffic_sources", property_id="111", group_by="source_medium", limit=20)
        check(r["group_by"] == "sessionSourceMedium" and r["row_count"] == 20, "traffic_sources group_by")

        r = await call("conversions_summary", property_id="111", by_channel=True, limit=10)
        check(r["configured_key_events"] == ["sign_up"] and r["total_key_events"] > 0, "conversions_summary")

        CALLS.clear()
        r = await call("compare_periods", property_id="111", days=28)
        body = CALLS[0][1]["body"]
        check(len(CALLS) == 1 and len(body["dateRanges"]) == 2, "compare_periods uses one request with two ranges")
        check(r["previous"][1] < r["current"][0], "previous period ends before current starts")
        check(all(g["change"] > 0 for g in r["biggest_gains"]), "gains are positive")

        CALLS.clear()
        r = await call("landing_page_performance", property_id="111", organic_search_only=True, min_sessions=5)
        body = CALLS[0][1]["body"]
        check(body["dimensionFilter"]["filter"]["stringFilter"]["value"] == "Organic Search", "organic filter")
        check(r["rows"][0]["page_path"] == "/page-0", "page_path strips query string for GSC join")

        r = await call("whoami")
        check(r["email"] == "alice@example.com", "whoami")

        EMAIL["value"] = "mallory@example.org"
        try:
            await c.call_tool("list_properties", {})
            raise AssertionError("should fail")
        except Exception as e:
            check("should fail" not in str(e), "other domains are refused")

    print(f"\nAll {ok} checks passed.")


asyncio.run(main())
