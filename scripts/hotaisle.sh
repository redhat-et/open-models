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

run() { ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH" "$@"; }

case "${1:-}" in
serve)
  run "
    set -euo pipefail
    echo 0 | sudo tee /proc/sys/kernel/numa_balancing >/dev/null
    docker rm -f om-vllm >/dev/null 2>&1 || true
    docker images '$IMAGE' --format '{{.ID}}' | grep -q . || docker pull '$IMAGE'
    docker run -d --name om-vllm --restart unless-stopped \
      --network host --device /dev/kfd --device /dev/dri --group-add video \
      --security-opt seccomp=unconfined --shm-size 16g \
      -e HIP_VISIBLE_DEVICES=0 \
      '$IMAGE' \
      vllm serve '$MODEL' \
        --served-model-name '$MODEL' \
        --api-key '$API_KEY' \
        --max-model-len $CTX --max-num-seqs $MAX_SEQS \
        --gpu-memory-utilization 0.9 \
        --host 0.0.0.0 --port $PORT
    echo 'container started; weights re-download (~27GB) only if the box was re-imaged'
  "
  "$0" wait
  ;;
wait)
  echo -n "waiting for :$PORT "
  for _ in $(seq 1 90); do
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
  echo "stopping vLLM + powering off $SSH (gateway will fall back to the 5090)"
  run "docker stop om-vllm >/dev/null 2>&1 || true; sudo poweroff" || true
  echo "done - bring it back later with: ./scripts/hotaisle.sh serve"
  ;;
*)
  grep '^#   ' "$0" | sed 's/^#   //'; exit 1
  ;;
esac
