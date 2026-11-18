# 07. The check scripts hung with no output

## Symptom

`cost-check.sh` printed the Cloud Run section, then `== Cloud SQL instances`, and then nothing. No error, no prompt, it just sat there.

## Cause

The Cloud SQL Admin API was not enabled in the project (there is no Cloud SQL, so it never was). When you run `gcloud sql instances list` without it, gcloud asks:

```
API [sqladmin.googleapis.com] not enabled on project [...]. Would you like to enable and retry? (y/N)?
```

The script sends gcloud's error output to `/dev/null` so normal "not found" noise stays out of the report. That also hid the question, so the script was waiting for an answer nobody could see. The same would have happened at the Compute Engine checks.

## Fix

At the top of both `cost-check.sh` and `security-check.sh`:

```bash
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
```

gcloud then never asks anything. A disabled API returns an error, the script reads it as "nothing there", and moves on. That is correct: if the Cloud SQL API is off, there is no Cloud SQL to pay for.

## Lesson

In any script that hides gcloud's stderr, disable prompts too, or it can hang on a question you cannot see.

## Next

[08. The budget could not be created](08-budget-permission-fix.md)
