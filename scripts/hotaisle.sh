#!/usr/bin/env bash
# Hot aisle GPU box lifecycle: serve vLLM, check, shut down to save money.
# Re-runnable and idempotent - safe to use on a fresh/re-provisioned box.
#
#   ./scripts/hotaisle.sh serve    # pull image if needed + start vLLM (idempotent)
#   ./scripts/hotaisle.sh wait     # block until the gateway-facing API answers
#   ./scripts/hotaisle.sh status   # container, model, GPU state
#   ./scripts/hotaisle.sh off      # stop vLLM + power the box off (saves money)
#
# The box is power-off-safe by design: litellm health-checks it and the 5090
# covers the qwen38-27b-fast group while it is dark.
set -euo pipefail

SSH="${HOTAISLE_SSH:-}"
if [ -z "$SSH" ]; then
  echo "error: HOTAISLE_SSH is not set (e.g. HOTAISLE_SSH=hotaisle@<ip>)" >&2
  exit 1
fi
IMAGE="rocm/vllm:rocm7.13.0_gfx94X-dcgpu_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1"
MODEL="${HOTAISLE_MODEL:-Qwen/Qwen3.8-27B-FP8}"
PORT=8000
API_KEY="sk-local"
CTX="${HOTAISLE_CTX:-32768}"
MAX_SEQS=32

# -F config only when present (pod mounts /tmp/om-ssh-home; local runs use defaults)
CFG_OPTS=()
[ -n "${HOTAISLE_SSH_CONFIG:-}" ] && [ -f "$HOTAISLE_SSH_CONFIG" ] && CFG_OPTS=(-F "$HOTAISLE_SSH_CONFIG")
run() { ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  ${CFG_OPTS[@]+"${CFG_OPTS[@]}"} ${HOTAISLE_SSH_OPTS:-} "$SSH" "$@"; }

case "${1:-}" in
serve)
  if run "curl -sf -m 3 -H 'Authorization: Bearer $API_KEY' http://localhost:$PORT/v1/models" >/dev/null 2>&1; then
    echo "already serving - nothing to do"; exit 0
  fi
  if [ -n "${HOTAISLE_S3_BUCKET:-}" ] && command -v aws >/dev/null 2>&1 \
     && [ -n "${HOTAISLE_S3_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}" ]; then
    echo "building S3 warm manifest"
    B="$HOTAISLE_S3_BUCKET" P="${HOTAISLE_S3_PREFIX:?set HOTAISLE_S3_PREFIX when using S3 warm}"
    if AWS_ACCESS_KEY_ID="${HOTAISLE_S3_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}" \
       AWS_SECRET_ACCESS_KEY="${HOTAISLE_S3_KEY_SECRET:-${AWS_SECRET_ACCESS_KEY:-}}" \
       aws s3 cp --region us-east-1 "s3://$B/$P/filelist.txt" /tmp/om-warm-files.txt 2>/dev/null; then
      { while IFS= read -r rel; do
          printf '%s\t%s\n' "$rel" "$(AWS_ACCESS_KEY_ID="${HOTAISLE_S3_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}" \
            AWS_SECRET_ACCESS_KEY="${HOTAISLE_S3_KEY_SECRET:-${AWS_SECRET_ACCESS_KEY:-}}" \
            aws s3 presign "s3://$B/$P/$rel" --expires-in 86400 --region us-east-1)"
        done < /tmp/om-warm-files.txt
        printf 'manifest.txt\t%s\n' "$(AWS_ACCESS_KEY_ID="${HOTAISLE_S3_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}" \
          AWS_SECRET_ACCESS_KEY="${HOTAISLE_S3_KEY_SECRET:-${AWS_SECRET_ACCESS_KEY:-}}" \
          aws s3 presign "s3://$B/$P/manifest.txt" --expires-in 86400 --region us-east-1)"
      } > /tmp/om-warm.tsv
      scp -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        ${CFG_OPTS[@]+"${CFG_OPTS[@]}"} ${HOTAISLE_SSH_OPTS:-} /tmp/om-warm.tsv "$SSH":/tmp/warm.tsv
    fi
  fi
  scp -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    ${CFG_OPTS[@]+"${CFG_OPTS[@]}"} ${HOTAISLE_SSH_OPTS:-} \
    "$(dirname "$0")/hotaisle-serve-remote.sh" "$SSH":/tmp/hotaisle-serve-remote.sh
  run "HOTAISLE_IMAGE='$IMAGE' HOTAISLE_MODEL='$MODEL' HOTAISLE_CTX='$CTX' \
       HOTAISLE_AITER='${HOTAISLE_AITER:-}' \
       HOTAISLE_REASON_ARGS='${HOTAISLE_REASON_ARGS:-}' \
       HOTAISLE_TOOL_ARGS='${HOTAISLE_TOOL_ARGS:-}' \
       bash /tmp/hotaisle-serve-remote.sh"
  "$0" wait
  ;;
wait)
  echo -n "waiting for :$PORT "
  for _ in $(seq 1 120); do
    if run "curl -sf -m 5 -H 'Authorization: Bearer $API_KEY' http://localhost:$PORT/v1/models" >/dev/null 2>&1; then
      echo " - serving"; exit 0
    fi
    echo -n .; sleep 20
  done
  echo " TIMEOUT"; run "docker logs --tail 20 om-vllm"; exit 1
  ;;
status)
  run "
    docker ps --filter name=om-vllm --format 'container: {{.Status}}' || true
    curl -s -m 5 -H 'Authorization: Bearer $API_KEY' http://localhost:$PORT/v1/models | head -c 160; echo
    rocm-smi --showuse --showmemuse 2>/dev/null | grep -E 'GPU use|VRAM%' || true
  "
  ;;
off)
  echo "stopping vLLM + powering off $SSH"
  echo "WARNING: on rented hotaisle VMs billing runs while the VM EXISTS - power-off"
  echo "only saves electricity; to stop the meter use: ./scripts/hotaisle-api.sh off"
  run "docker stop om-vllm >/dev/null 2>&1 || true; sudo poweroff" || true
  echo "done - bring it back later with: ./scripts/hotaisle.sh serve"
  ;;
*)
  grep '^#   ' "$0" | sed 's/^#   //'; exit 1
  ;;
esac
