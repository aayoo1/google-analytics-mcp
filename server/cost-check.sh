#!/usr/bin/env bash
# Read-only check for anything that costs money while idle. Changes nothing.
# Run in Cloud Shell:  PROJECT_ID=<project-id> ./cost-check.sh
set -uo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1  # never stop at "enable this API?" prompts
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:?Set REGION, e.g. us-central1}"
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1
flag() { echo "  !! $*"; }

echo "== Cloud Run services (all regions)"
echo "   want: minScale empty or 0, cpu-throttling true (request-based billing), maxScale small"
gcloud run services list --format=json 2>/dev/null | python3 -c '
import json, sys
for s in json.load(sys.stdin):
    name = s["metadata"]["name"]
    region = s["metadata"]["labels"].get("cloud.googleapis.com/location", "?")
    ann = s["spec"]["template"]["metadata"].get("annotations", {})
    svc_ann = s["metadata"].get("annotations", {})
    lim = s["spec"]["template"]["spec"]["containers"][0].get("resources", {}).get("limits", {})
    mn = ann.get("autoscaling.knative.dev/minScale") or svc_ann.get("run.googleapis.com/minScale") or "0"
    mx = ann.get("autoscaling.knative.dev/maxScale", "default(100)")
    thr = ann.get("run.googleapis.com/cpu-throttling", "true")
    cpu, mem = lim.get("cpu", "?"), lim.get("memory", "?")
    print(f"   {name:18} {region:12} min={mn:3} max={mx:12} cpu={cpu} mem={mem} cpu_throttling={thr}")
    if mn not in ("0", ""):
        print(f"  !! {name}: min-instances {mn} keeps an instance warm (costs money 24/7)")
    if thr == "false":
        print(f"  !! {name}: CPU always allocated (instance-based billing)")
    if mx.startswith("default") or int(mx) > 5:
        print(f"  !! {name}: max-instances {mx} is higher than needed for a team of 4")
'

echo "== Cloud SQL instances"
out=$(gcloud sql instances list --format='value(name,state,tier)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "Cloud SQL bills 24/7 while RUNNABLE"; } || echo "   none"

echo "== Static IP addresses"
out=$(gcloud compute addresses list --format='value(name,region,status)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "static IPs bill hourly (more when RESERVED and unused)"; } || echo "   none"

echo "== Compute Engine VMs"
out=$(gcloud compute instances list --format='value(name,zone,status,machineType.basename())' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "VMs bill while RUNNING (disks bill even when stopped)"; } || echo "   none"

echo "== Persistent disks"
out=$(gcloud compute disks list --format='value(name,zone,sizeGb)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "disks bill per GB per month"; } || echo "   none"

echo "== Load balancers (forwarding rules)"
out=$(gcloud compute forwarding-rules list --format='value(name,region)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "each forwarding rule bills ~USD 18/month"; } || echo "   none"

echo "== GKE clusters"
out=$(gcloud container clusters list --format='value(name,location)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "GKE clusters bill a management fee plus nodes"; } || echo "   none"

echo "== Serverless VPC connectors / Memorystore"
out=$(gcloud compute networks vpc-access connectors list --region="${REGION}" --format='value(name)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "VPC connectors run VMs 24/7"; } || echo "   no VPC connectors in ${REGION}"
out=$(gcloud redis instances list --region="${REGION}" --format='value(name)' 2>/dev/null)
[[ -n $out ]] && { echo "$out"; flag "Memorystore bills 24/7"; } || echo "   no Memorystore in ${REGION}"

echo "== Artifact Registry (container images; 0.5 GB free per billing account)"
gcloud artifacts repositories list --format='table(name.basename(),location,format,sizeBytes.size(units_out=M))' 2>/dev/null \
  || echo "   (could not list)"
echo "   Over 0.5 GB costs ~USD 0.10/GB/month. A cleanup policy keeping the last 2 images per service fixes it."

echo "== Secret Manager (6 active versions free, then ~USD 0.06/version/month)"
total=0
for s in $(gcloud secrets list --format='value(name)' 2>/dev/null); do
  n=$(gcloud secrets versions list "$s" --filter='state=ENABLED' --format='value(name)' 2>/dev/null | wc -l | tr -d ' ')
  total=$((total + n)); echo "   $s: $n enabled"
done
echo "   total enabled versions: $total"
(( total > 6 )) && flag "$((total - 6)) versions over the free 6 (destroy old ones you don't need)"

echo "== Firestore databases (free tier only applies to one database)"
gcloud firestore databases list --format='value(name.basename(),locationId)' 2>/dev/null

echo "== Billing budgets on this project's billing account"
BA=$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' 2>/dev/null | sed 's#billingAccounts/##')
echo "   billing account: ${BA:-unknown}"
[[ -n $BA ]] && gcloud billing budgets list --billing-account="$BA" \
  --format='table(displayName,amount.specifiedAmount.units,amount.specifiedAmount.currencyCode)' 2>/dev/null \
  || echo "   (could not list budgets; needs Billing Account Viewer)"

echo "== Spend so far this month (gcloud can't read spend, so open this link)"
echo "   https://console.cloud.google.com/billing/${BA}/reports?project=${PROJECT_ID}"
echo "   Group by Service, then by SKU. Cloud Run costs per service show under the"
echo "   label run.googleapis.com/service (one line per MCP service)."

echo
echo "Done. Lines starting with !! are things that cost money while idle or could push past the free tier."
