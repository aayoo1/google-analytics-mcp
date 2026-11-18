#!/usr/bin/env bash
# Read-only security check for the project that runs the MCP servers.
# Changes nothing. Run in Cloud Shell:  PROJECT_ID=<project-id> ./security-check.sh
# Lines starting with !! need a look. "ok" lines passed.
set -uo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1  # never stop at "enable this API?" prompts
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:?Set REGION, e.g. us-central1}"
ALLOWED_DOMAIN="${ALLOWED_DOMAIN:?Set ALLOWED_DOMAIN to your Google Workspace domain}"
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
DEFAULT_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "== Cloud Run services"
gcloud run services list --format=json 2>/dev/null | DEFAULT_SA="$DEFAULT_SA" python3 -c '
import json, os, re, sys
SECRETY = re.compile(r"(SECRET|TOKEN|KEY|PASSWORD|PRIVATE|CREDENTIAL|REFRESH)", re.I)
for s in json.load(sys.stdin):
    name = s["metadata"]["name"]
    spec = s["spec"]["template"]["spec"]
    sa = spec.get("serviceAccountName", "")
    ingress = s["metadata"].get("annotations", {}).get("run.googleapis.com/ingress", "all")
    shown = sa or "(default)"
    print(f"   {name}: service account={shown} ingress={ingress}")
    if not sa or sa == os.environ["DEFAULT_SA"]:
        print(f"  !! {name}: uses the default compute service account (usually has Editor on the whole project). Give it its own account.")
    for c in spec.get("containers", []):
        for e in c.get("env", []):
            ename = e["name"]
            if "value" in e and SECRETY.search(e["name"]) and e["name"] not in ("GOOGLE_CLIENT_ID", "SECRET_UPDATED"):
                print(f"  !! {name}: env var {ename} is a plain value. Secrets belong in Secret Manager (--set-secrets).")
            if "valueFrom" in e:
                print(f"      ok {ename} comes from Secret Manager")
'

echo "== Who can call each service (allUsers is expected: the server does its own OAuth)"
for svc in $(gcloud run services list --format='value(metadata.name)' 2>/dev/null); do
  region=$(gcloud run services list --filter="metadata.name=$svc" --format='value(metadata.labels."cloud.googleapis.com/location")' 2>/dev/null)
  members=$(gcloud run services get-iam-policy "$svc" --region "$region" --format='value(bindings.members)' 2>/dev/null)
  echo "   $svc: ${members:-none}"
done

echo "== Unauthenticated access is refused (expect 401 on each /mcp)"
for url in $(gcloud run services list --format='value(status.url)' 2>/dev/null); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$url/mcp")
  [[ $code == 401 ]] && echo "   ok $url/mcp -> 401" || echo "  !! $url/mcp -> $code (expected 401)"
done

echo "== Project IAM: who has access"
gcloud projects get-iam-policy "$PROJECT_ID" --format=json | ALLOWED_DOMAIN="$ALLOWED_DOMAIN" DEFAULT_SA="$DEFAULT_SA" python3 -c '
import json, os, sys
dom = os.environ["ALLOWED_DOMAIN"]; dsa = os.environ["DEFAULT_SA"]
BROAD = {"roles/owner", "roles/editor"}
for b in json.load(sys.stdin).get("bindings", []):
    for m in b["members"]:
        role = b["role"]
        if m in ("allUsers", "allAuthenticatedUsers"):
            print(f"  !! {role}: {m} (anyone on the internet)")
        elif m.startswith("user:") and not m.endswith("@" + dom):
            print(f"  !! {role}: {m} is outside {dom}")
        elif m.startswith("serviceAccount:") and role in BROAD:
            note = " (default compute account)" if m.endswith(dsa) else ""
            print(f"  !! {role}: {m}{note} has broad access")
        elif role in BROAD:
            print(f"      {role}: {m}")
'

echo "== Service account keys (downloadable keys are the most common leak)"
found=0
for sa in $(gcloud iam service-accounts list --format='value(email)' 2>/dev/null); do
  keys=$(gcloud iam service-accounts keys list --iam-account "$sa" --managed-by=user --format='value(name.basename(),validAfterTime)' 2>/dev/null)
  if [[ -n $keys ]]; then found=1; echo "  !! $sa has user-managed keys:"; echo "$keys" | sed 's/^/       /'; fi
done
(( found == 0 )) && echo "   ok no user-managed keys"

echo "== API keys (should be restricted or not exist)"
gcloud services api-keys list --format=json 2>/dev/null | python3 -c '
import json, sys
keys = json.load(sys.stdin)
if not keys: print("   ok none")
for k in keys:
    r = k.get("restrictions") or {}
    tag = "restricted" if r else "UNRESTRICTED"
    print(("  !! " if not r else "      ") + (k.get("displayName") or k["name"].split("/")[-1]) + ": " + tag)
'

echo "== Secrets and who can read them"
for s in $(gcloud secrets list --format='value(name)' 2>/dev/null); do
  m=$(gcloud secrets get-iam-policy "$s" --format='value(bindings.members)' 2>/dev/null)
  echo "   $s: ${m:-only project-level roles}"
  [[ $m == *allUsers* || $m == *allAuthenticatedUsers* ]] && echo "  !! $s is readable by everyone"
done

echo "== Public Cloud Storage buckets"
found=0
for b in $(gcloud storage buckets list --format='value(name)' 2>/dev/null); do
  if gcloud storage buckets get-iam-policy "gs://$b" --format=json 2>/dev/null | grep -qE 'allUsers|allAuthenticatedUsers'; then
    found=1; echo "  !! gs://$b is public"
  fi
done
(( found == 0 )) && echo "   ok no public buckets"

echo "== Firewall rules open to the internet (only matters if there are VMs)"
gcloud compute firewall-rules list --filter='sourceRanges:0.0.0.0/0 AND direction=INGRESS' \
  --format='value(name,allowed[].map().firewall_rule().list())' 2>/dev/null | sed 's/^/   /'

echo "== Audit: recent IAM changes (last 30 days)"
gcloud logging read 'protoPayload.methodName="SetIamPolicy"' --freshness=30d --limit=10 \
  --format='value(timestamp,protoPayload.authenticationInfo.principalEmail,protoPayload.resourceName)' 2>/dev/null | sed 's/^/   /'

cat <<'EOF'

Check by hand in the console (gcloud can't read these):
  - Google Auth Platform > Audience: user type is Internal.
  - Google Auth Platform > Clients: one Web client per MCP, no unused clients,
    redirect URIs are only the three https://<service>-...run.app/auth/callback URLs.
  - Admin console (admin.google.com) > Security > 2-Step Verification enforced for everyone.
  - Firestore > Rules: "allow read, write: if false;" (the servers use IAM, not rules).

Done. Lines starting with !! need a look.
EOF
