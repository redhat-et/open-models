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
BUCKET="${HOTAISLE_S3_BUCKET:-}"
PREFIX="${HOTAISLE_S3_PREFIX:-}"

export AWS_ACCESS_KEY_ID="${HOTAISLE_AWS_KEY_ID:-}"
export AWS_SECRET_ACCESS_KEY="${HOTAISLE_AWS_KEY_SECRET:-}"
export AWS_DEFAULT_REGION=us-east-1

REASON_ARGS=""
case "$MODEL" in *GLM*|*glm*|*DeepSeek*|*deepseek*) REASON_ARGS="--reasoning-parser glm45" ;; esac

M="models--${MODEL//\//--}"
HUB="$HOME/.cache/huggingface/hub"

if [ ! -d "$HUB/$M/snapshots" ] && [ -n "$PREFIX" ] && [ -n "$BUCKET" ] && [ -n "$AWS_ACCESS_KEY_ID" ]; then
  echo "warming $M from s3://$BUCKET/$PREFIX (8-way parallel)"
  mkdir -p "$HUB/$M" /tmp/warm
  aws s3 cp "s3://$BUCKET/$PREFIX/filelist.txt" /tmp/warm/filelist.txt
  while IFS= read -r rel; do
    aws s3 presign "s3://$BUCKET/$PREFIX/$rel" --expires-in 86400
  done < /tmp/warm/filelist.txt > /tmp/warm/urls.txt
  paste -d'\t' /tmp/warm/filelist.txt /tmp/warm/urls.txt | xargs -P 8 -n 2 bash -c '
    rel=$0; url=$1
    mkdir -p "$HUB/$M/$(dirname "$rel")"
    curl -sS -C - -o "$HUB/$M/$rel" "$url" || { echo "WARM FAIL $rel"; exit 1; }' _
  aws s3 cp "s3://$BUCKET/$PREFIX/manifest.txt" /tmp/warm/manifest.txt
  while IFS=$'\t' read -r p t; do
    mkdir -p "$HUB/$M/$(dirname "$p")"; ln -sf "$t" "$HUB/$M/$p"
  done < /tmp/warm/manifest.txt
  rm -rf /tmp/warm
  echo "warm complete: $(du -sh "$HUB/$M" | cut -f1)"
else
  echo "weight cache present or S3 warm not configured - vLLM will download from HF if needed"
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
  -v "$HUB:/root/.cache/huggingface" \
  $HF_OFFLINE \
  "$IMAGE" \
  vllm serve "$MODEL" \
    --served-model-name "$MODEL" \
    --api-key "$API_KEY" \
    --max-model-len "$CTX" --max-num-seqs "$MAX_SEQS" $REASON_ARGS \
    --gpu-memory-utilization 0.9 --kv-cache-dtype fp8 --max-num-batched-tokens 8192 \
    --host 0.0.0.0 --port "$PORT"
echo "container started"
