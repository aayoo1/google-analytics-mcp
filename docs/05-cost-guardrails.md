# 05. Keep the cost near zero

## Goal

A team of a few people, three MCP servers, and a bill that stays at or near zero.

## Cloud Run settings (in `deploy.sh`)

| Flag | Why |
|---|---|
| `--min-instances 0` | Nothing runs while nobody is using it. The first request after idle waits a few seconds (cold start). |
| `--max-instances 2` | Caps the worst case, including a flood of junk requests. |
| `--cpu 1 --memory 512Mi` | Plenty for this server. |
| `--concurrency 80 --timeout 300` | Many requests share one instance; long reports can finish. |
| `--cpu-throttling` | Request based billing: CPU is only billed while a request is being handled. |

`--min-instances 1` keeps one copy running around the clock, which costs roughly USD 10 a month even with nobody using it. My Search Console server had that from its first deploy. `cost-check.sh` caught it, and I set it to 0.

## Row and quota caps (in `server.py`)

- Default 1,000 rows per call, hard max 100,000, fetched in pages of 25,000.
- Summary tools stop at 1,000 rows, realtime at 10,000.
- `compare_periods` asks for both periods in one request instead of two.
- Tool descriptions state the GA4 Data API quota (standard property: about 200,000 tokens a day and 40,000 an hour per property, 10 concurrent requests), and every report returns `property_quota` with what is left.

## Budget alert

```bash
PROJECT_ID=<project-id> AMOUNT=<amount> CURRENCY=<currency> ./budget.sh
```

Creates a monthly budget on the billing account with email alerts at 50, 90 and 100 percent. The currency must match the billing account's. A budget only sends alerts; it does not stop spending. You need admin rights on the billing account, not just the project (see [08](08-budget-permission-fix.md)).

## Check for anything always on

```bash
PROJECT_ID=<project-id> REGION=<region> ./cost-check.sh
```

Read only. For every Cloud Run service it shows min and max instances and whether CPU is always allocated, then looks for Cloud SQL, static IPs, VMs, disks, load balancers, GKE, VPC connectors and Memorystore. It also shows Artifact Registry size (0.5 GB free), Secret Manager versions (6 free) and a link to this month's spend.

What I found: no always on resources apart from the one warm instance above. Two things to watch:

- **Container images.** Every deploy adds an image. Three services sat at about 270 MB of the free 0.5 GB before this one. A cleanup policy that keeps the last two images per service stops it growing.
- **Secret versions.** Three servers with three secrets each, plus old versions from bad pastes, go past the free 6. Each extra version is about USD 0.06 a month. Disabled versions still count; only destroyed ones stop costing. Disable old versions first, check nothing breaks, then destroy the ones you are sure are wrong.

## Expected cost

About USD 0 a month for a small team: Cloud Run's free tier is 2 million requests and 180,000 vCPU seconds a month, Firestore's is 50,000 reads a day. The realistic worst case across three services is under USD 1 a month.

## Next

[06. Security check](06-security-check.md)
