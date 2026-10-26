#!/usr/bin/env bash
# Deploy the public demo to Google Cloud Run.
#
#   deploy/cloudrun/deploy.sh [project] [region]
#
# Builds the committed tree (git archive HEAD, so .env files and local data never leave the machine)
# on Cloud Build, keeps the LLM key and JWT secret in Secret Manager, and runs one instance at most:
# that caps cost and keeps the in-process demo rate limits exact. Scales to zero when idle.
set -euo pipefail

PROJECT=${1:-rag-asst}
REGION=${2:-europe-west2}
SERVICE=rag-demo
REPO=demo
ROOT=$(git rev-parse --show-toplevel)
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/rag-demo:$(git -C "$ROOT" rev-parse --short HEAD)"
gc() { gcloud --project "$PROJECT" --quiet "$@"; }

if [ -n "$(git -C "$ROOT" status --porcelain -- backend web eval/corpus deploy)" ]; then
  echo "Note: uncommitted changes are NOT deployed (only HEAD is built)."
fi

echo "== APIs"
gc services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

echo "== Artifact Registry"
gc artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  gc artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --description "Demo images"

# Newer orgs don't auto-grant the default service account broad roles, so grant exactly what it needs:
# Cloud Build runs as it (push image, write logs, read the uploaded source) and Cloud Run runs as it (read secrets).
PN=$(gc projects describe "$PROJECT" --format 'value(projectNumber)')
SA="$PN-compute@developer.gserviceaccount.com"
echo "== Roles for $SA"
for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer roles/secretmanager.secretAccessor; do
  gc projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" --condition None >/dev/null
done

echo "== Secrets"
if ! gc secrets describe demo-openai-key >/dev/null 2>&1; then
  read -rsp "LLM API key for the demo (OpenRouter key with a daily limit; input hidden): " KEY; echo
  [ -n "$KEY" ] || { echo "No key given; stopping."; exit 1; }
  printf %s "$KEY" | gc secrets create demo-openai-key --data-file=- --replication-policy automatic >/dev/null
  unset KEY
fi
gc secrets describe demo-jwt-secret >/dev/null 2>&1 ||
  python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")' | gc secrets create demo-jwt-secret --data-file=- --replication-policy automatic >/dev/null

echo "== Build $IMAGE"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
git -C "$ROOT" archive HEAD backend web eval/corpus deploy .dockerignore | tar -x -C "$STAGE"
rm -rf "$STAGE/backend/tests"
gc builds submit "$STAGE" --config "$STAGE/deploy/cloudrun/cloudbuild.yaml" --substitutions "_IMAGE=$IMAGE"

echo "== Deploy"
gc run deploy "$SERVICE" \
  --image "$IMAGE" --region "$REGION" --port 7860 \
  --memory 2Gi --cpu 1 --cpu-boost --execution-environment gen2 \
  --min-instances 0 --max-instances 1 --concurrency 20 --timeout 300 \
  --set-secrets OPENAI_API_KEY=demo-openai-key:latest,JWT_SECRET=demo-jwt-secret:latest \
  --no-invoker-iam-check

URL=$(gc run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
echo "Live: $URL"
