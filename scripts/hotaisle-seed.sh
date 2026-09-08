#!/usr/bin/env bash
# GLM weight-cache S3 seed (run ON the box; driven by scripts/hotaisle-seed-laptop.sh).
# Presigned PUTs are broken on this account (SignatureDoesNotMatch), so upload
# uses `aws s3 sync` with the scoped octo-models-s3-write creds in the env.
#
#   hotaisle-seed.sh copy    # docker cp weights -> $GLM_CACHE_DIR, emit file lists
#   hotaisle-seed.sh sync    # aws s3 sync cache dir -> s3 (needs AWS_* write creds)
#
# S3 layout: <bucket>/glm-45-air-fp8-cache/{blobs/<file>,refs/<ref>,manifest.txt,filelist.txt}
# (mirrors ~/.cache/huggingface/hub/models--zai-org--GLM-4.5-Air-FP8)
set -euo pipefail

CACHE_DIR="${GLM_CACHE_DIR:-/home/hotaisle/glm-cache}"
MODEL_DIR="models--zai-org--GLM-4.5-Air-FP8"
BUCKET="${HOTAISLE_S3_BUCKET:?}"
PFX="glm-45-air-fp8-cache"

case "${1:-}" in
copy)
  echo "copying $MODEL_DIR out of om-vllm -> $CACHE_DIR"
  rm -rf "$CACHE_DIR"
  mkdir -p "$CACHE_DIR"
  docker cp "om-vllm:/root/.cache/huggingface/hub/$MODEL_DIR" "$CACHE_DIR/"
  cd "$CACHE_DIR/$MODEL_DIR"
  { find blobs -type f; find refs -type f; } | sort > /tmp/glm-files.tsv
  find snapshots -type l -printf '%p\t%l\n' | sort > /tmp/glm-manifest.txt
  echo "files: $(wc -l < /tmp/glm-files.tsv), bytes: $(du -sb . | cut -f1)"
  ;;
sync)
  : "${AWS_ACCESS_KEY_ID:?write creds required (see HOTAISLE_S3_WRITE_KEY_*)}"
  cd "$CACHE_DIR/$MODEL_DIR"
  echo "syncing $(wc -l < /tmp/glm-files.tsv) files -> s3://$BUCKET/$PFX"
  aws s3 sync . "s3://$BUCKET/$PFX" --exclude "snapshots/*" --only-show-errors
  aws s3 cp /tmp/glm-manifest.txt "s3://$BUCKET/$PFX/manifest.txt" --only-show-errors
  aws s3 cp /tmp/glm-files.tsv "s3://$BUCKET/$PFX/filelist.txt" --only-show-errors
  echo "blobs synced: $(find blobs -type f | wc -l)"
  echo "seed sync complete"
  ;;
*)
  grep '^#   ' "$0" | sed 's/^#   //'; exit 1
  ;;
esac
