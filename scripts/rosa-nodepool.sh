#!/usr/bin/env bash
# Scale the ROSA machine pool that hosts OCTO GPU nodes (models-arch cluster).
#
# Usage: rosa-nodepool.sh <replicas> [--pool NAME] [--cluster models-arch-ocp] [--wait]
#
#   rosa-nodepool.sh 1 --wait   # one GPU node, block until it's Ready
#   rosa-nodepool.sh 0          # scale pool to zero (see PVC warning below)
#
# Requires: rosa CLI logged in (rosa login), oc context on the target cluster
# when --wait is used. Picking --pool by hand skips the GPU-pool autodetect.
#
# WARNING for scale-to-0: the serving PVC is gp3-csi with Delete reclaim -
# when its node dies the EBS volume goes with it, so the next boot re-downloads
# weights (~15 min for the NVFP4 checkpoint) and you may need to delete the
# stale (Lost) PVC:  oc delete pvc flash-hf-cache -n octo-models-compute
set -euo pipefail

REPLICAS="${1:?usage: rosa-nodepool.sh <replicas> [--pool NAME] [--cluster NAME] [--wait]}"
POOL="" CLUSTER="models-arch-ocp" WAIT=0
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --pool) POOL="$2"; shift 2 ;;
    --cluster) CLUSTER="$2"; shift 2 ;;
    --wait) WAIT=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v rosa >/dev/null || { echo "rosa CLI not installed" >&2; exit 1; }

if [ -z "$POOL" ]; then
  POOL=$(rosa list machinepools --cluster "$CLUSTER" -o json | python3 -c '
import json, sys
d = json.load(sys.stdin)
pools = d if isinstance(d, list) else d.get("items", [])
gpu = [p for p in pools if str(p.get("instance_type", "")).startswith(("g5", "g6", "g7"))]
if not gpu:
    sys.exit(f"no GPU machinepool (g5/g6/g7*) in {\"$CLUSTER\"}: {[(p.get(\"name\"), p.get(\"instance_type\")) for p in pools]}")
print(gpu[0]["name"])')
fi

echo "scaling machinepool '$POOL' in cluster '$CLUSTER' -> $REPLICAS replica(s)"
rosa scale machinepool --cluster "$CLUSTER" --name "$POOL" --replicas "$REPLICAS"

if [ "$WAIT" = 1 ]; then
  echo "waiting up to 30 min for $REPLICAS GPU node(s) Ready (oc context: $(oc config current-context 2>/dev/null || echo none))"
  deadline=$(( $(date +%s) + 1800 ))
  n=0
  while [ "$(date +%s)" -lt "$deadline" ]; do
    n=$(oc get nodes -l nvidia.com/gpu.present=true --no-headers 2>/dev/null | grep -c " Ready" || true)
    if [ "$n" -ge "$REPLICAS" ]; then
      echo "ready: $n GPU node(s)"
      exit 0
    fi
    sleep 30
  done
  echo "timed out: only $n GPU node(s) Ready after 30 min" >&2
  exit 1
fi
