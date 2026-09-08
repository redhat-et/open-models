#!/usr/bin/env bash
# Drive the GLM weight-cache seed from a machine with the aws CLI + S3 creds.
#   1) box: docker cp weights out of the container
#   2) laptop: presign 7-day PUT URLs for every file
#   3) box: parallel curl PUT (8-wide)
#   4) verify blob count on S3
#
# Env: HA_SSH (default hotaisle@23.183.40.67), HA_SSH_OPTS, HA_BUCKET.
set -euo pipefail
cd "$(dirname "$0")/.."

BUCKET="${HA_BUCKET:-octo-models-weights-us-east-1}"
PFX="glm-45-air-fp8-cache"
SSH="${HA_SSH:-hotaisle@23.183.40.67}"
run() { ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} "$SSH" "$@"; }
WORK=$(mktemp -d /tmp/glm-seed.XXXX)
trap 'rm -rf "$WORK"' EXIT

echo "==> 1/4: copy weights out of container (on box)"
scp -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} scripts/hotaisle-seed.sh "$SSH":/tmp/hotaisle-seed.sh
run "bash /tmp/hotaisle-seed.sh copy"

echo "==> 2/4: presign PUT urls"
scp -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} "$SSH":/tmp/glm-files.tsv "$SSH":/tmp/glm-manifest.txt "$WORK/"
: > "$WORK/urls.tsv"
while IFS= read -r rel; do
  url=$(aws s3 presign "s3://$BUCKET/$PFX/$rel" --expires-in 604800)
  printf '%s\t%s\n' "$rel" "$url" >> "$WORK/urls.tsv"
done < "$WORK/glm-files.tsv"
for meta in manifest.txt filelist.txt; do
  url=$(aws s3 presign "s3://$BUCKET/$PFX/$meta" --expires-in 604800)
  printf '%s\t%s\n' "$meta" "$url" >> "$WORK/urls.tsv"
done
echo "    $(wc -l < "$WORK/urls.tsv") urls signed"

echo "==> 3/4: parallel upload from box"
scp -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} "$WORK/urls.tsv" "$SSH":/tmp/glm-urls.tsv
run "bash /tmp/hotaisle-seed.sh upload /tmp/glm-urls.tsv"

echo "==> 4/4: verify"
want=$(grep -c '^blobs/' "$WORK/glm-files.tsv") || true
got=$(aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "$PFX/blobs/" --query 'length(Contents)' 2>/dev/null || echo "?")
echo "    blobs on S3: $got (expected $want)"
[ "$got" = "$want" ] && echo "SEED OK" || echo "SEED MISMATCH - check"
