#!/usr/bin/env bash
# Deploy the public demo to an Oracle Cloud Always Free VM (or any Ubuntu VM with ports 80/443 open).
#
#   deploy/oracle/deploy.sh <public-ip> [ssh-key]
#
# Copies the committed tree (git archive HEAD: .env files and local data never leave the Mac), builds
# the image on the VM (ARM, same as the Mac), and runs it behind Caddy with automatic HTTPS at
# https://<ip-with-dashes>.sslip.io. The LLM key is asked for once and kept in ~/rag-demo/.env on the VM.
set -euo pipefail

IP=${1:?usage: deploy.sh <public-ip> [ssh-key]}
KEY=${2:-$HOME/.ssh/oracle_rag}
HOST="ubuntu@$IP"
SITE="${IP//./-}.sslip.io"
ROOT=$(git rev-parse --show-toplevel)
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST")

if [ -n "$(git -C "$ROOT" status --porcelain -- backend web eval/corpus deploy)" ]; then
  echo "Note: uncommitted changes are NOT deployed (only HEAD is copied)."
fi

echo "== VM setup"
"${SSH[@]}" 'bash -s' < "$ROOT/deploy/oracle/setup-vm.sh"

echo "== Copy $(git -C "$ROOT" rev-parse --short HEAD)"
"${SSH[@]}" 'rm -rf ~/rag-demo/src && mkdir -p ~/rag-demo/src'
git -C "$ROOT" archive HEAD backend web eval/corpus deploy .dockerignore | "${SSH[@]}" 'tar -x -C ~/rag-demo/src && rm -rf ~/rag-demo/src/backend/tests'
scp -q -i "$KEY" "$ROOT/deploy/oracle/compose.yml" "$ROOT/deploy/oracle/Caddyfile" "$HOST:rag-demo/"

echo "== Secrets"
if ! "${SSH[@]}" 'test -f ~/rag-demo/.env'; then
  read -rsp "LLM API key for the demo (OpenRouter key with a daily limit; input hidden): " LLM_KEY; echo
  [ -n "$LLM_KEY" ] || { echo "No key given; stopping."; exit 1; }
  JWT=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  printf 'SITE_ADDRESS=%s\nOPENAI_API_KEY=%s\nJWT_SECRET=%s\n' "$SITE" "$LLM_KEY" "$JWT" |
    "${SSH[@]}" 'umask 077 && cat > ~/rag-demo/.env'
  unset LLM_KEY
fi

echo "== Build and start (the first build takes several minutes)"
"${SSH[@]}" 'cd ~/rag-demo && sudo docker compose up -d --build && sudo docker image prune -f >/dev/null'

echo "== Waiting for https://$SITE"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "https://$SITE/health" 2>/dev/null; then echo "Live: https://$SITE"; exit 0; fi
  sleep 5
done
echo "Not answering yet. Check: ssh -i $KEY $HOST 'cd rag-demo && sudo docker compose logs --tail 50'"
exit 1
