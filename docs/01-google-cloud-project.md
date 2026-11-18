# 01. Prepare the Google Cloud project

## Why

The server runs on Google Cloud Run and keeps its secrets in Secret Manager and its sign in sessions in Firestore. All of that lives in one Google Cloud project.

## Steps

### 1. Pick a project

I reused the project that already ran my Google Ads and Search Console MCPs. Each MCP is its own Cloud Run service with its own OAuth client, service account and secrets, so sharing the project is fine and keeps them in one place. Note the **project ID**. It appears as `<project-id>` in the rest of this guide.

You need Owner or Editor on the project.

### 2. Make sure billing is active

Cloud Run, Cloud Build, Firestore and Secret Manager will not enable without a billing account. At team scale this costs close to nothing (see [05](05-cost-guardrails.md)).

If the project is on a free trial, upgrade it before relying on the server. When a trial ends, Google stops the project's services.

### 3. Check the OAuth consent screen type

In the console go to **Google Auth Platform**, then **Audience**.

- **Internal** means only accounts in your Google Workspace organization can sign in. This is what I used.
- **External** works with personal Gmail accounts, but you must add each person as a test user.

If the project already has a consent screen (mine did), there is nothing to change. Internal apps do not need scopes listed on the Data Access page.

### 4. APIs

Nothing to enable by hand. `deploy.sh` enables Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Firestore, the **Google Analytics Data API** (reports) and the **Google Analytics Admin API** (accounts and properties).

## Next

[02. Create the OAuth client](02-oauth-client.md)
