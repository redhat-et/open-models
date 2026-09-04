#!/usr/bin/env bash
# Benchmark a vLLM endpoint with guidellm (same tool that produced
# qwen3-serving/benchmarks.{csv,json}). Results land in benchmarks/results/.
#
# Usage: ./bench.sh <base_url> <served_model> <profile> [extra guidellm args...]
# Profiles:
#   latency     - single streaming stream, interactive feel (tok/s, TTFT)
#   load        - ramped concurrency, find the practical capacity
#   long-context- big prompts on the 256k cluster models
set -euo pipefail
BASE="${1:?base url, e.g. http://136.47.153.236:8000}"
MODEL="${2:?served model id, e.g. Qwen/Qwen3.8-27B}"
PROFILE="${3:?latency|load|long-context}"
shift 3

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="$(echo "$BASE" | sed -E 's#https?://##; s#[/:]#_#g')"
STAMP="$(date +%Y-%m-%d-%H%M)"
OUT="$DIR/results/$HOST-$MODEL-$PROFILE-$STAMP"
mkdir -p "$DIR/results"

case "$PROFILE" in
  latency)      SCENARIO="streaming-paused --rate 1" ;;
  load)         SCENARIO="streaming-ramp" ;;
  long-context) SCENARIO="synthetic --prompt-tokens 32000 --max-tokens 512" ;;
  *) echo "unknown profile: $PROFILE" >&2; exit 1 ;;
esac

# Adjust scenario/profile syntax to `guidellm run --help` for the installed
# version (0.7.x used here). Keep the rest stable so runs stay comparable.
guidellm run \
  --backend openai_http \
  --target "$BASE" \
  --model "$MODEL" \
  --scenario "$SCENARIO" \
  --output-path "$OUT" \
  "$@"

echo "results: $OUT"
