# 06. Security check

## Built into the server

- Scope `analytics.readonly` only, no write tools. Each person's own token.
- Internal consent screen, plus an `ALLOWED_DOMAIN` check and a verified email check in the server.
- `allowed_client_redirect_uris` is locked to `https://claude.ai/api/mcp/auth_callback`. Any other redirect gets 400.
- `require_authorization_consent="remember"`: a consent page with the server's name and icon the first time, remembered after.
- Secrets only in Secret Manager. Google tokens are encrypted with a Fernet key before they reach Firestore.
- Its own service account with Firestore and secret access only. No service account keys.
- The container runs as a non-root user.
- Dependencies checked with `pip-audit -r requirements.txt`: no known vulnerabilities at the time of writing. Re-run it before each redeploy.

## Check the whole project

```bash
PROJECT_ID=<project-id> REGION=<region> ALLOWED_DOMAIN=<your-domain> ./security-check.sh
```

Read only. It covers every service in the project, not just this one:

- services running as the default compute service account (usually Editor on the whole project)
- secrets passed as plain environment variables instead of Secret Manager
- any `/mcp` endpoint that does not return 401 without sign in
- project members outside your domain, broad Owner or Editor grants, and `allUsers` anywhere
- downloadable service account keys, unrestricted API keys
- who can read each secret
- public storage buckets, firewall rules open to the internet
- IAM changes in the last 30 days

It ends with four things to check by hand in the console: the consent screen is Internal, no unused OAuth clients, 2-Step Verification enforced in the Workspace admin console, and Firestore rules deny all (the servers use IAM, not rules).

## What it found on my project

- All three `/mcp` endpoints returned 401. All secrets came from Secret Manager. No service account keys, no API keys, no public buckets.
- **One real finding:** an older MCP service was running as the default compute service account, which has Editor on the whole project. If that service were ever compromised, it could change almost anything.

  Fixed without a rebuild: a new service account with only Firestore access and read access to that service's own secrets, then `gcloud run services update <service> --service-account <new-account>`. After checking it worked (401, 302 and a real query from Claude), I removed the default account's access to those secrets. Keep the previous revision name handy: `gcloud run services update-traffic <service> --to-revisions=<old-revision>=100` rolls back.

  The default compute account still has Editor on the project. Cloud Build may use it for `--source` deploys, so test before removing it.
- An unused Desktop OAuth client left over from an earlier experiment. Worth deleting.

## Next

[07. The check scripts hung with no output](07-check-scripts-hung-fix.md)
