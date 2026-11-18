# 04. Connect Claude

## Steps

### 1. Add it for yourself first

In Claude: your name in the bottom left corner, **Settings**, **Connectors**, **Add custom connector**.

- **Name:** Google Analytics
- **URL:** `https://<service>-<project-number>.<region>.run.app/mcp`
- Leave the advanced settings empty.

Click **Add**, then **Connect**. A consent page shows the server name and icon the first time (after that it is remembered). Approve it, sign in with your Workspace account and click **Allow**.

In a new chat, with the connector switched on, ask:

> list my GA4 properties

### 2. Roll it out to the team

If you don't see **Add custom connector** in your own settings, your organization only lets owners add them. An owner adds the same URL under **Admin settings**, **Connectors**. Each person then clicks **Connect** and signs in with their own Google account.

Check with a second account that each person only sees their own properties.

## Things to try

- "which channels drove the most key events in the last 28 days?"
- "top 20 landing pages from organic search, with engagement rate and key events"
- "what lost the most sessions this month vs last month, by landing page?"
- "how many people are on the site right now, and on which pages?"
- With the Search Console connector too: "match my organic landing pages in GA4 with their Search Console clicks and position"

GA4 data for today and yesterday can still change, so date ranges end yesterday by default.

## If Connect fails

- `invalid_client`: the client secret is wrong. Run `set-secret.sh` again with the JSON.
- `Internal Server Error`: check Cloud Run logs for "lacks a collection id" (the Firestore key fix).
- `redirect_uri_mismatch`: the `/auth/callback` URL on the OAuth client is missing or has a typo.
- "Only @... accounts can use this server": you signed in with an account outside `ALLOWED_DOMAIN`.

## Next

[05. Keep the cost near zero](05-cost-guardrails.md)
