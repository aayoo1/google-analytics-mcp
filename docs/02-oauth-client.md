# 02. Create the OAuth client

## Why

When someone clicks Connect in Claude, they sign in with Google. Google needs to know which app is asking. That app is an OAuth client: a client ID plus a client secret.

Use a **new client** for this MCP, separate from any other MCP's client, so one can be rotated or deleted without breaking the others.

## Steps

### 1. Create a Web application client

**Google Auth Platform**, **Clients**, **Create client**.

- **Application type:** Web application.
- **Name:** anything, for example `<web-client-name>`. Only you see it.
- **Authorized redirect URIs:** you can add it now, because the Cloud Run URL is predictable:
  ```
  https://<service>-<project-number>.<region>.run.app/auth/callback
  ```
  The project number is on the project dashboard. With the default service name it starts `https://ga4-mcp-`.
- **"Use this client for an AI-powered agent":** leave it unchecked. Claude never talks to Google with this client; the server does. My Search Console client works without it, so I kept it the same.

Click **Create**.

### 2. Download the JSON straight away

The dialog shows the client ID and secret. Click **Download JSON**, then OK. Google does not show the secret again.

You will hand this file to `set-secret.sh` in step 03. Afterwards, keep the secret in a password manager and delete the file.

## Gotcha

I filled in the form, then reloaded the page before clicking Create. Nothing was saved and the client list did not show it. If the new client is not in the **Clients** list, it was not created.

## Next

[03. Deploy the server to Cloud Run](03-deploy-cloud-run.md)
