# 03. Deploy the server to Cloud Run

## Before you start

- Steps 01 and 02 done, with the client JSON downloaded.
- Pick a region close to your team, for example `us-central1`. It appears as `<region>` below.

## Steps

### 1. Open Cloud Shell and get the code in

Go to https://shell.cloud.google.com with your project selected. If it asks to **Authorize Cloud Shell**, click Authorize.

```bash
git clone https://github.com/aayoo1/google-analytics-mcp.git
cd google-analytics-mcp/server
chmod +x *.sh
```

### 2. Run the deploy script

```bash
PROJECT_ID=<project-id> REGION=<region> ALLOWED_DOMAIN=<your-domain> GOOGLE_CLIENT_ID=<client-id> ./deploy.sh
```

It:

1. Enables the APIs from step 01.
2. Creates a Firestore database if there is none. An existing one is reused with its own collection.
3. Creates a service account for this server with Firestore access only.
4. Creates the client secret in Secret Manager with a **placeholder**, plus two generated keys (JWT signing and token encryption).
5. Builds and deploys to Cloud Run with the cost limits from [05](05-cost-guardrails.md). The first time in a project it may ask to create an Artifact Registry repo: type `y`.

It does **not** ask for the client secret. On my Search Console build, pasting it into the deploy prompt went wrong twice. Here it only ever goes through the next step.

### 3. Set the real client secret

Upload the downloaded JSON to Cloud Shell (the ⋮ menu, **Upload**), then:

```bash
./set-secret.sh ~/client_secret_<client-id>.json && rm ~/client_secret_<client-id>.json
```

It reads only the secret from the file, checks it is a single valid `GOCSPX-` value, saves it as a new version in Secret Manager and restarts the service. Expect `Looks valid (35 characters)`, then `Done`.

### 4. Check it is up

```bash
U=https://<service>-<project-number>.<region>.run.app
curl -s -o /dev/null -w '%{http_code}\n' -X POST $U/mcp
curl -s -o /dev/null -w '%{http_code}\n' "$U/authorize?response_type=code&client_id=https%3A%2F%2Fclaude.ai%2Foauth%2Fmcp-oauth-client-metadata&redirect_uri=https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256&state=x&scope=openid"
```

- `401` for the first: the server is up and refuses requests that are not signed in.
- `302` for the second: Claude's sign in starts. A `500` here means the Firestore key fix is missing (see the [Search Console build](https://github.com/aayoo1/google-search-console-mcp/blob/main/docs/06-internal-server-error-fix.md)).

Change the `redirect_uri` in the second command to anything else and you get `400`. Only Claude's callback is allowed.

## Why `--allow-unauthenticated`

Cloud Run's own login check would block Claude from connecting at all. The server's OAuth layer protects `/mcp` instead, so requests without sign in get a 401 and never see data.

## Next

[04. Connect Claude](04-connect-claude.md)
