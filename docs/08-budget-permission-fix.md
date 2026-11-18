# 08. The budget could not be created: billing permissions

## Symptom

`budget.sh` enabled the Budgets API, then failed:

```
ERROR: (gcloud.billing.budgets.create) [<your-email>] does not have permission to access
billingAccounts instance [<billing-account-id>] (or it may not exist)
```

## Cause

Budgets belong to the **billing account**, not the project. Being Owner of the project is not enough. In my case the billing account was managed by a different admin, so my account could see which billing account the project used but could not read or change it.

The same missing permission is why `cost-check.sh` could not list budgets or show spend for me.

## Fix

Either:

- the billing account admin runs `budget.sh` (or creates the budget in **Billing**, **Budgets & alerts**), or
- they give you **Billing Account Administrator** (or at least Billing Account Costs Manager) on that billing account, and you run it.

Then:

```bash
PROJECT_ID=<project-id> AMOUNT=<amount> CURRENCY=<currency> ./budget.sh
```

## Does the MCP need it?

No. The server deploys and works without a budget. The budget only sends email alerts, so it can be added later. It is still worth having: it is the only thing that tells you if something starts costing money.

## Next

[09. Smaller fixes](09-smaller-fixes.md)
