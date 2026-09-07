#!/usr/bin/env bash
# OCTO Open Models - per-user setup. Run as the user who will use the models
# (no sudo needed). Idempotent: safe to re-run; existing configs are backed up.
#
#   ./scripts/setup-user.sh              # interactive (asks for your key)
#   ./scripts/setup-user.sh --key sk-…   # non-interactive
#   OCTO_KEY=sk-… curl -fsSL <portal>/setup.sh | bash   # one-paste setup
#   ./scripts/setup-user.sh --check      # re-verify an existing setup
#
# What it does:
#   1. installs opencode (official installer -> ~/.local/bin, no sudo)
#   2. writes ~/.config/opencode/opencode.json with our gateway models
#   3. stores your portal key (~/.config/octo/key, 0600) + exports $OCTO_KEY
#   4. smoke-tests the gateway end to end
set -euo pipefail

APP_URL="https://octo-app-octo-models.apps.emerg.pcbk.p1.openshiftapps.com"
GW_URL="https://litellm-octo-models.apps.emerg.pcbk.p1.openshiftapps.com/v1"
KEY_FILE="$HOME/.config/octo/key"
OC_CFG="$HOME/.config/opencode/opencode.json"
SELF_PATH="${BASH_SOURCE[0]:-${0:-}}"
REPO_DIR=""
case "$SELF_PATH" in
  */*) REPO_DIR="$(cd "$(dirname "$SELF_PATH")/.." 2>/dev/null && pwd || true)" ;;
esac
# when piped via curl|bash there is no real path; validate repo dir
[ -n "$REPO_DIR" ] && [ ! -f "$REPO_DIR/scripts/setup-user.sh" ] && REPO_DIR=""
KEY=""
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY="$2"; shift 2 ;;
    --check) CHECK=1; shift ;;
    *) echo "unknown flag: $1"; exit 1 ;;
  esac
done

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] && die "do not run as root - run as your model-using self (e.g. omtest)"

# ---------------------------------------------------------------- key
KEY="${KEY:-${OCTO_KEY:-}}"
if [ -z "$KEY" ] && [ -f "$KEY_FILE" ]; then KEY=$(cat "$KEY_FILE"); fi
if [ -z "$KEY" ] && [ "$CHECK" = 1 ]; then die "no key found ($KEY_FILE) - re-run without --check"; fi
if [ -z "$KEY" ]; then
  cat <<EOF

  1. Open:  $APP_URL
  2. Log in with GitHub (needs redhat-et org membership)
  3. Create an API key (shown ONCE) and paste it below.

EOF
  read -rsp "  OCTO key (sk-...): " KEY; echo
  [ -n "$KEY" ] || die "no key given"
fi

# ---------------------------------------------------------------- smoke test FIRST (bad key = stop early)
log "testing gateway with your key..."
SMOKE=$(curl -sf -m 60 "$GW_URL/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"qwen38-flash-next","messages":[{"role":"user","content":"reply with exactly: setup works"}],"max_tokens":400,"reasoning_effort":"minimal"}' \
  ) || die "gateway call failed - is the key correct / gateway up?"
echo "$SMOKE" | grep -q "choices" || die "unexpected response: $(echo "$SMOKE" | head -c 200)"
log "gateway OK ✓  (spend for this call shows up in the portal)"
if [ "$CHECK" = 1 ]; then
  if command -v opencode >/dev/null 2>&1; then
    log "setup looks good - key works, gateway reachable, opencode on PATH"
  else
    log "key works but opencode isn't on this shell's PATH - run:"
    echo '    export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"'
    echo "  (or open a new terminal) and re-run --check"
  fi
  exit 0
fi
PATH_HAD_OC=0; command -v opencode >/dev/null 2>&1 && PATH_HAD_OC=1

# ---------------------------------------------------------------- key storage + env
mkdir -p "$(dirname "$KEY_FILE")"; chmod 700 "$(dirname "$KEY_FILE")"
printf '%s' "$KEY" > "$KEY_FILE"; chmod 600 "$KEY_FILE"
# cover bash (interactive), sh (login) and zsh - whichever rc the user's shell reads
for RC in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc"; do
  if ! grep -q "OCTO_KEY from octo-setup" "$RC" 2>/dev/null; then
    cat >> "$RC" <<'EOF'
# OCTO_KEY from octo-setup
export OCTO_KEY="$(cat "$HOME/.config/octo/key" 2>/dev/null)"
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
EOF
    log "exported OCTO_KEY + PATH in ~/${RC##*/}"
  fi
done

# ---------------------------------------------------------------- opencode install
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
if ! command -v opencode >/dev/null 2>&1; then
  log "installing opencode to ~/.local/bin ..."
  curl -fsSL https://opencode.ai/install | bash >/dev/null || die "opencode install failed"
  export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
  command -v opencode >/dev/null || die "opencode not on PATH after install (check ~/.opencode/bin)"
fi
log "opencode: $(opencode --version 2>/dev/null | head -1)"

# ---------------------------------------------------------------- config
mkdir -p "$(dirname "$OC_CFG")"
[ -f "$OC_CFG" ] && [ ! -f "$OC_CFG.octo-backup" ] && cp "$OC_CFG" "$OC_CFG.octo-backup" && log "backed up existing config -> $OC_CFG.octo-backup"
MODELS_JSON=$(mktemp /tmp/octo-models.XXXXXX)
if curl -sf -m 10 "$APP_URL/setup/models.json" -o "$MODELS_JSON" && command -v python3 >/dev/null 2>&1; then
  python3 - "$OC_CFG" "$GW_URL" "$MODELS_JSON" <<'PY'
import json, sys
cfg_path, gw, mpath = sys.argv[1:4]
m = json.load(open(mpath))
prov = {"octo-gateway": {
  "npm": "@ai-sdk/openai-compatible",
  "name": "OCTO Open Models gateway (metered; key from the web app)",
  "options": {"baseURL": gw, "apiKey": "{env:OCTO_KEY}"},
  "models": m["models"],
}}
for lab in m.get("lab", []):
  prov[lab["provider"]] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": lab["name"],
    "options": {"baseURL": lab["base_url"], "apiKey": lab.get("api_key") or "none"},
    "models": {lab["served_model"]: lab["model"]},
  }
json.dump({
  "$schema": "https://opencode.ai/config.json",
  "model": f"octo-gateway/{m['default']}",
  "small_model": f"octo-gateway/{m['small']}",
  "provider": prov,
}, open(cfg_path, "w"), indent=2)
PY
  COUNTS=$(python3 -c "import json;d=json.load(open('$MODELS_JSON'));print(str(len(d['models'])) + ' gateway models + ' + str(len(d.get('lab', []))) + ' lab providers')" 2>/dev/null || echo "gateway models")
  log "installed opencode.json built from the LIVE catalog ($COUNTS)"
elif [ -n "$REPO_DIR" ] && [ -f "$REPO_DIR/configs/opencode.json" ]; then
  cp "$REPO_DIR/configs/opencode.json" "$OC_CFG"
  log "installed opencode.json from repo ($REPO_DIR)"
else
  cat > "$OC_CFG" <<'EOF'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "octo-gateway/qwen38-27b-fast",
  "small_model": "octo-gateway/qwen38-flash-next",
  "provider": {
    "octo-gateway": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OCTO Open Models gateway (metered; key from the web app)",
      "options": { "baseURL": "https://litellm-octo-models.apps.emerg.pcbk.p1.openshiftapps.com/v1", "apiKey": "{env:OCTO_KEY}" },
      "models": {
        "qwen38-flash-next": { "name": "Qwen 3.8 Flash Next (262k ctx)", "limit": { "context": 262144, "output": 8192 }, "tool_call": true, "reasoning": true, "temperature": true },
        "qwen38-27b-fast": { "name": "Qwen 3.8 27B (fast)", "limit": { "context": 32768, "output": 8192 }, "tool_call": true, "reasoning": true, "temperature": true }
      }
    }
  }
}
EOF
  log "installed gateway-only opencode.json (embedded fallback copy)"
fi
rm -f "$MODELS_JSON"
# point the config at the key FILE (not the env) so it survives reboots/launchers
sed -i "s|{env:OCTO_KEY}|{file:$KEY_FILE}|" "$OC_CFG"
grep -q "{file:$KEY_FILE}" "$OC_CFG" || die "apiKey rewrite failed"
log "opencode apiKey -> {file:$KEY_FILE} (no shell env needed)"

log "done! try it:  opencode   (models: /models -> octo-gateway/*)"
if [ "$PATH_HAD_OC" = 0 ]; then
  log "first time only - this terminal can't be changed by a piped script, so run:"
  echo '    export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"'
  echo "  ...or just open a new terminal (bash/zsh pick it up automatically from now on)"
fi
log "(key is read from file at startup - works in any shell, after any reboot)"
echo "  portal:   $APP_URL"
echo "  grafana:  https://grafana-octo-models.apps.emerg.pcbk.p1.openshiftapps.com"
echo "  re-check: $0 --check"
