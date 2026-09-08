#!/usr/bin/env bash
# Runs ON a hotaisle box: warm the HF weight cache from S3 if missing, then
# (re)start the om-vllm container. Invoked by hotaisle.sh serve via ssh.
set -euo pipefail

IMAGE="${HOTAISLE_IMAGE:?}"
MODEL="${HOTAISLE_MODEL:?}"
PORT="${HOTAISLE_PORT:-8000}"
API_KEY="${HOTAISLE_API_KEY:-sk-local}"
CTX="${HOTAISLE_CTX:-32768}"
MAX_SEQS="${HOTAISLE_MAX_SEQS:-32}"
WARM_TSV="${HOTAISLE_WARM_TSV:-/tmp/warm.tsv}"

REASON_ARGS=""
case "$MODEL" in *GLM*|*glm*|*DeepSeek*|*deepseek*) REASON_ARGS="--reasoning-parser glm45" ;; esac

M="models--${MODEL//\//--}"
HUB="$HOME/.cache/huggingface/hub"

# warm.tsv: relpath<TAB>presigned-GET-url rows (built by hotaisle.sh on the pod;
# presigned PUTs are broken on this account but GETs are fine) + a manifest.txt row.
if [ ! -d "$HUB/$M/snapshots" ] && [ -s "$WARM_TSV" ]; then
  echo "warming $M from S3 (8-way parallel)"
  mkdir -p "$HUB/$M"
  export HUB M
  grep -v '^manifest.txt' "$WARM_TSV" | xargs -P 8 -n 2 bash -c '
    rel=$0; url=$1
    mkdir -p "$HUB/$M/$(dirname "$rel")"
    curl -sSf -C - -o "$HUB/$M/$rel" "$url" || { echo "WARM FAIL $rel"; exit 1; }'
  grep '^manifest.txt' "$WARM_TSV" | head -1 | cut -f2 |
  xargs curl -fsS | while IFS=$'\t' read -r p t; do
    mkdir -p "$HUB/$M/$(dirname "$p")"; ln -sf "$t" "$HUB/$M/$p"
  done
  rm -f "$WARM_TSV"
  echo "warm complete: $(du -sh "$HUB/$M" | cut -f1)"
else
  echo "weight cache present or no warm.tsv - vLLM will download from HF if needed"
fi

HF_OFFLINE=""
[ -d "$HUB/$M/snapshots" ] && HF_OFFLINE="-e HF_HUB_OFFLINE=1"

echo 0 | sudo tee /proc/sys/kernel/numa_balancing >/dev/null
docker rm -f om-vllm >/dev/null 2>&1 || true
docker images "$IMAGE" --format '{{.ID}}' | grep -q . || docker pull "$IMAGE"
docker run -d --name om-vllm --restart unless-stopped \
  --network host --device /dev/kfd --device /dev/dri --group-add video \
  --security-opt seccomp=unconfined --shm-size 16g \
  -e HIP_VISIBLE_DEVICES=0 \
  -e AITER_JIT_DIR=/opt/aiter-cache \
  ${HOTAISLE_AITER:+-e VLLM_ROCM_USE_AITER=1 -e VLLM_ROCM_USE_AITER_MOE=1} \
  -v "$HOME/.aiter-cache:/opt/aiter-cache" \
  -v "$HUB:/root/.cache/huggingface/hub" \
  $HF_OFFLINE \
  "$IMAGE" \
  vllm serve "$MODEL" \
    --served-model-name "$MODEL" \
    --api-key "$API_KEY" \
    --max-model-len "$CTX" --max-num-seqs "$MAX_SEQS" $REASON_ARGS \
    --gpu-memory-utilization 0.9 --kv-cache-dtype fp8 --max-num-batched-tokens 8192 \
    --host 0.0.0.0 --port "$PORT"
echo "container started"
