#!/usr/bin/env bash
# GLM weight-cache S3 seed/restore (run ON the box; driven by
# scripts/hotaisle-seed-laptop.sh for seeding, hotaisle.sh warm for restore).
#
#   hotaisle-seed.sh copy             # docker cp weights -> /tmp/glm-cache, emit file lists
#   hotaisle-seed.sh upload URLTSV    # parallel curl PUT of relpath<TAB>url rows, then clean up
#
# S3 layout: <bucket>/glm-45-air-fp8-cache/{blobs/<file>,refs/<ref>,manifest.txt,filelist.txt}
# (mirrors ~/.cache/huggingface/hub/models--zai-org--GLM-4.5-Air-FP8)
set -euo pipefail

CACHE_DIR="${GLM_CACHE_DIR:-/tmp/glm-cache}"
MODEL_DIR="models--zai-org--GLM-4.5-Air-FP8"

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
upload)
  [ -f "${1:?URLTSV arg: relpath<TAB>url}" ] || exit 1
  cd "$CACHE_DIR/$MODEL_DIR"
  echo "uploading $(wc -l < "$1") files, 8 in parallel"
  xargs -P 8 -n 2 bash -c '
    rel=$0; url=$1
    case "$rel" in
      manifest.txt) src=/tmp/glm-manifest.txt ;;
      filelist.txt) src=/tmp/glm-files.tsv ;;
      *)            src="$CACHE_DIR/$MODEL_DIR/$rel" ;;
    esac
    [ -f "$src" ] || { echo "MISSING $rel"; exit 1; }
    curl -sS -X PUT -T "$src" "$url" || { echo "FAILED $rel"; exit 1; }' _ < "$1"
  echo "uploaded: $(find blobs -type f | wc -l) blobs"
  rm -rf "$CACHE_DIR"
  echo "seed upload complete"
  ;;
*)
  grep '^#   ' "$0" | sed 's/^#   //'; exit 1
  ;;
esac
