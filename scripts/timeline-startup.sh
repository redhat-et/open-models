#!/usr/bin/env bash
# Instrument vLLM startup phases (see docs/rfe-vllm-startup.md).
# Usage: ./timeline-startup.sh <container-name> <health-url> [output.json]
# Starts (or restarts) the container, polls /health every second, and mines
# the container log for phase markers. Works with docker or podman.
set -euo pipefail
NAME="${1:?container name}"
HEALTH="${2:-http://localhost:8000/health}"
OUT="${3:-startup-$(date +%Y-%m-%d-%H%M).json}"

RUN="$(command -v docker || command -v podman)"
START=$(date +%s)

"$RUN" restart "$NAME" >/dev/null 2>&1 || "$RUN" start "$NAME" >/dev/null

phase=""
phases="{}"
ready=0
while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  LOG="$("$RUN" logs "$NAME" 2>&1 | tail -5)"
  case "$LOG" in
    *"Loading safetensors checkpoint shards: 100%"*)
      [[ "$phase" == "" || "$phase" == "init" ]] && { phase="weights_loaded"; phases=$(echo "$phases" | sed "s|}|\"weights_loaded\":$ELAPSED,}|"); } ;;
    *"Capturing CUDA graphs"*)
      [[ "$phase" == "weights_loaded" || "$phase" == "init" ]] && { phase="graph_capture"; phases=$(echo "$phases" | sed "s|}|\"graph_capture\":$ELAPSED,}|"); } ;;
    *"Uvicorn running"*|*"Application startup complete"*)
      [[ "$phase" != "ready" ]] && { phase="ready"; phases=$(echo "$phases" | sed "s|}|\"api_up\":$ELAPSED,}|"); } ;;
  esac
  if curl -sf -m 2 "$HEALTH" >/dev/null 2>&1; then
    phases=$(echo "$phases" | sed "s|}|\"health_ok\":$ELAPSED}|")
    ready=1
    break
  fi
  (( ELAPSED > 1800 )) && break
  sleep 1
done

echo "{\"container\":\"$NAME\",\"ready\":$ready,\"seconds\":$ELAPSED,\"phases\":$phases}" | tee "$OUT"
