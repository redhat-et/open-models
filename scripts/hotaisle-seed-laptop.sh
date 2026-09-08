#!/usr/bin/env bash
# Drive the GLM weight-cache seed from a machine with S3 write creds.
#   1) box: docker cp weights out of the container into ~/glm-cache
#   2) box: aws s3 sync ~/glm-cache -> s3 (scoped write user, static creds
#      passed via a temp env file; presigned PUTs are broken on this account)
#   3) verify blob count on S3 (presigned GETs work, so the read user is enough)
#
# Env: HA_SSH (default hotaisle@23.183.40.67), HA_BUCKET.
#      HOTAISLE_S3_WRITE_KEY_ID/SECRET (or sourced from secrets.env).
set -euo pipefail
cd "$(dirname "$0")/.."

BUCKET="${HA_BUCKET:-octo-models-weights-us-east-1}"
PFX="glm-45-air-fp8-cache"
SSH="${HA_SSH:-hotaisle@23.183.40.67}"
SECRETS="${HOTAISLE_SECRETS:-$HOME/.config/octo-models/secrets.env}"
[ -n "${HOTAISLE_S3_WRITE_KEY_ID:-}" ] || [ ! -f "$SECRETS" ] || . "$SECRETS"
[ -n "${HOTAISLE_S3_WRITE_KEY_ID:-}" ] || { echo "HOTAISLE_S3_WRITE_KEY_ID/SECRET required"; exit 1; }
run() { ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} "$SSH" "$@"; }

echo "==> 1/3: copy weights out of container (on box)"
scp -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} scripts/hotaisle-seed.sh "$SSH":/tmp/hotaisle-seed.sh
run "bash /tmp/hotaisle-seed.sh copy"

echo "==> 2/3: sync to S3 from box (write creds via temp file)"
umask 077; printf 'export AWS_ACCESS_KEY_ID=%s\nexport AWS_SECRET_ACCESS_KEY=%s\nexport AWS_DEFAULT_REGION=us-east-1\n' "$HOTAISLE_S3_WRITE_KEY_ID" "$HOTAISLE_S3_WRITE_KEY_SECRET" > /tmp/ha-aws.env
scp -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new ${HOTAISLE_SSH_OPTS:-} /tmp/ha-aws.env "$SSH":/tmp/ha-aws.env
rm -f /tmp/ha-aws.env
run "set -a; . /tmp/ha-aws.env; set +a; rm -f /tmp/ha-aws.env; HOTAISLE_S3_BUCKET=$BUCKET bash /tmp/hotaisle-seed.sh sync"

echo "==> 3/3: verify"
want=$(run "grep -c '^blobs/' /tmp/glm-files.tsv || true")
got=$(aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "$PFX/blobs/" --query 'length(Contents)' 2>/dev/null || echo "?")
echo "    blobs on S3: $got (expected $want)"
[ "$got" = "$want" ] && echo "SEED OK" || echo "SEED MISMATCH - check"
