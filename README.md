# google-analytics-mcp

Step by step build log for connecting Google Analytics 4 to Claude through a small custom MCP server, deployed on Google Cloud Run so a whole team can use it. Each person signs in with their own Google account and only sees the GA4 properties they already have access to. Read only. Documented as I went, including the parts that did not work on the first try and how each one was fixed.

Third in a set, one connector per Google API:

- [googleads_mcp](https://github.com/aayoo1/googleads_mcp) for Google Ads
- [google-search-console-mcp](https://github.com/aayoo1/google-search-console-mcp) for Search Console
- this repo for Google Analytics 4

Separate services mean one can break or be redeployed without touching the others.

## Goal

Let Claude read GA4 data (sessions, users, pages, landing pages, traffic sources, key events, revenue, realtime) and answer questions like "which channels drove the most key events last month?" or "which landing pages lost traffic vs the month before?", without anyone sharing logins or service account keys. And keep the running cost close to zero.

## Who this is for

Anyone who works with GA4, usually across several properties, and wants to query it from Claude. You do not need to be a developer, but you will use Google Cloud Shell (a terminal in the browser) and the Google Cloud console.

## Before you start

You will need:

- **A Google Cloud project with billing enabled.** At team scale this runs inside the free tiers, but billing must be active.
- **A Google Workspace account** for the "Internal" OAuth consent screen, so only people in your organization can sign in. On a personal Gmail account, use "External" and add yourself as a test user instead.
- **GA4 access** to the properties you want to query, on the same Google account you will sign in with.
- **Claude** with custom connectors (Claude.ai or Claude Desktop).

Nothing needs installing on your own computer. Everything runs in Cloud Shell.

## What is in this repo

| Path | What it is |
|---|---|
| [`server/server.py`](server/server.py) | The MCP server (Python, [FastMCP](https://gofastmcp.com)) with all tools |
| [`server/deploy.sh`](server/deploy.sh) | One command setup and deploy, with cost limits: APIs, Firestore, service account, secrets, Cloud Run |
| [`server/set-secret.sh`](server/set-secret.sh) | The only way to set or replace the OAuth client secret. Validates it and restarts |
| [`server/budget.sh`](server/budget.sh) | Creates a monthly billing budget with email alerts at 50, 90 and 100 percent |
| [`server/cost-check.sh`](server/cost-check.sh) | Read only: lists anything in the project that costs money while idle |
| [`server/security-check.sh`](server/security-check.sh) | Read only: security scan of the project and every MCP service in it |
| [`server/tests/test_fake.py`](server/tests/test_fake.py) | Tests every tool against fake GA4 data, no Google account needed |
| `server/Dockerfile`, `server/requirements.txt` | Container (runs as non-root) and pinned dependencies |
| [`examples/env.example`](examples/env.example) | Environment variables, for running locally |
| [`docs/`](docs/) | The build log, one file per step |

## Tools Claude gets

| Tool | What it does |
|---|---|
| `list_properties` | GA4 accounts and properties you can access |
| `get_metadata` | Valid dimension and metric names for a property, including custom ones |
| `run_report` | Any report: dimensions, metrics, dates, optional compare range, filters, sorting, up to 100k rows |
| `realtime_report` | The last 1 to 30 minutes of activity |
| `top_pages` | Most viewed pages with users, engagement time and key events |
| `traffic_sources` | Sessions by channel, source/medium, source, medium or campaign |
| `conversions_summary` | Key events (GA4's name for conversions) with counts and value, optionally by channel |
| `compare_periods` | Biggest gains and losses for one metric, last N days vs the N days before |
| `landing_page_performance` | Landing pages with engagement, key events and revenue, optionally organic search only |
| `whoami` | Which Google account is connected |

All read only. The server only asks for the `analytics.readonly` scope and has no write tools.

**With Search Console:** `landing_page_performance` returns clean page paths. With both connectors added, Claude pulls organic landing pages from GA4 and pages from Search Console for the same dates and matches them, so you see clicks and position next to what visitors did after landing. The GA4 server never touches Search Console itself.

## Build log

1. [Prepare the Google Cloud project](docs/01-google-cloud-project.md)
2. [Create the OAuth client](docs/02-oauth-client.md)
3. [Deploy the server to Cloud Run](docs/03-deploy-cloud-run.md)
4. [Connect Claude](docs/04-connect-claude.md)
5. [Keep the cost near zero](docs/05-cost-guardrails.md)
6. [Security check](docs/06-security-check.md)

## Problems hit and fixed

7. [The check scripts hung with no output](docs/07-check-scripts-hung-fix.md)
8. [The budget could not be created: billing permissions](docs/08-budget-permission-fix.md)
9. [Smaller things: a lost tool description, a false alarm, a frozen Cloud Shell tab](docs/09-smaller-fixes.md)

Two problems from the Search Console build were designed out up front: the [client secret paste problem](https://github.com/aayoo1/google-search-console-mcp/blob/main/docs/05-client-secret-fix.md) (here only `set-secret.sh` ever sets the secret) and the [Internal Server Error on Connect](https://github.com/aayoo1/google-search-console-mcp/blob/main/docs/06-internal-server-error-fix.md) (the Firestore key fix is in from the start).

## How it works

```
Claude  --(MCP over HTTPS)-->  Cloud Run: the MCP server  --(your Google token)-->  GA4 Data API + Admin API
                                    |
                                    +-- Secret Manager: OAuth client secret, JWT key, storage key
                                    +-- Firestore: encrypted sign in sessions
```

The server is an OAuth proxy in front of Google (FastMCP's `GoogleProvider`). When you click Connect in Claude, you approve a consent page (once), sign in with Google, the server keeps your Google token on its side and gives Claude its own token. Every tool call uses your token, so you only see your own properties.

## Result

Working end to end as a shared Cloud Run deployment. Verified by connecting from Claude, signing in with Google and listing GA4 properties.

## Security notes

- No credentials are stored in this repo. The client secret lives in Secret Manager; the two other keys are generated by `deploy.sh` and also stored there.
- Sign in is limited to your Workspace domain twice: the Internal consent screen, and an `ALLOWED_DOMAIN` check in the server. Unverified emails are refused.
- Only Claude's callback URL can receive sign in codes. Any other redirect gets a 400.
- Google tokens are encrypted before they are stored. No service account keys are used anywhere.
- The live Cloud Run URL for this deployment is not in this repo.

## Status

Built and deployed for team wide access, November 2026.

## Licence

MIT, see [LICENSE](LICENSE).
