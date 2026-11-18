#!/usr/bin/env bash
# Create a monthly billing budget with email alerts at 50, 90 and 100 percent.
# Alerts go to the billing account's admins and users by email.
# Run in Cloud Shell:  PROJECT_ID=<project-id> ./budget.sh
# A budget only alerts. It does not stop spending.
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
AMOUNT="${AMOUNT:?Set AMOUNT, the monthly budget in your billing currency}"
CURRENCY="${CURRENCY:?Set CURRENCY to your billing account currency, e.g. USD or EUR}"
NAME="${NAME:-MCP servers monthly}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable billingbudgets.googleapis.com cloudbilling.googleapis.com
BA=$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' | sed 's#billingAccounts/##')
[[ -z $BA ]] && { echo "No billing account linked to $PROJECT_ID"; exit 1; }
ACC_CUR=$(gcloud billing accounts describe "$BA" --format='value(currencyCode)' 2>/dev/null || true)
if [[ -n $ACC_CUR && $ACC_CUR != "$CURRENCY" ]]; then
  echo "The billing account is in $ACC_CUR, not $CURRENCY. Budgets must use the account's currency."
  echo "Re-run with CURRENCY=$ACC_CUR AMOUNT=<amount in $ACC_CUR>."
  exit 1
fi

if gcloud billing budgets list --billing-account="$BA" --format='value(displayName)' | grep -qx "$NAME"; then
  echo "A budget called '$NAME' already exists. Nothing changed."
  exit 0
fi

# Scoped to the whole billing account so any surprise cost shows up.
gcloud billing budgets create \
  --billing-account="$BA" \
  --display-name="$NAME" \
  --budget-amount="${AMOUNT}${CURRENCY}" \
  --calendar-period=month \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0

echo "Budget created: ${AMOUNT} ${CURRENCY}/month, email alerts at 50, 90 and 100 percent."
