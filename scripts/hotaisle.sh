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

SSH="${HOTAISLE_SSH:-hotaisle@23.183.40.70}"
IMAGE="rocm/vllm:rocm7.13.0_gfx94X-dcgpu_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1"
MODEL="${HOTAISLE_MODEL:-Qwen/Qwen3.8-27B-FP8}"
PORT=8000
API_KEY="sk-local"
CTX="${HOTAISLE_CTX:-32768}"
MAX_SEQS=32

REASON_ARGS=""
case "$MODEL" in *GLM*|*glm*|*DeepSeek*|*deepseek*) REASON_ARGS="--reasoning-parser glm45" ;; esac

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
  scp -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    ${CFG_OPTS[@]+"${CFG_OPTS[@]}"} ${HOTAISLE_SSH_OPTS:-} \
    "$(dirname "$0")/hotaisle-serve-remote.sh" "$SSH":/tmp/hotaisle-serve-remote.sh
  run "HOTAISLE_IMAGE='$IMAGE' HOTAISLE_MODEL='$MODEL' HOTAISLE_CTX='$CTX' \
       HOTAISLE_S3_BUCKET='${HOTAISLE_S3_BUCKET:-}' HOTAISLE_S3_PREFIX='${HOTAISLE_S3_PREFIX:-}' \
       HOTAISLE_AWS_KEY_ID='${HOTAISLE_AWS_KEY_ID:-${HOTAISLE_S3_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}}' \
       HOTAISLE_AWS_KEY_SECRET='${HOTAISLE_AWS_KEY_SECRET:-${HOTAISLE_S3_KEY_SECRET:-${AWS_SECRET_ACCESS_KEY:-}}}' \
       HOTAISLE_AITER='${HOTAISLE_AITER:-}' \
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
