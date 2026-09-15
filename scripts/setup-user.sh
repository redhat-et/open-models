#!/usr/bin/env bash
# OCTO Open Models - per-user setup. Run as the user who will use the models
# (no sudo needed). Idempotent: safe to re-run; existing configs are backed up.
#
#   ./scripts/setup-user.sh              # interactive (asks for your key)
#   ./scripts/setup-user.sh --key sk-…   # non-interactive
#   curl -fsSL <portal>/setup.sh | bash                        # one-paste setup (prompts on your tty)
#   curl -fsSL <portal>/setup.sh | OCTO_KEY=sk-… bash          # one-paste, no prompt
#   ./scripts/setup-user.sh --check      # re-verify an existing setup
#
# What it does:
#   1. smoke-tests the gateway end to end (bad key = stop before touching anything)
#   2. stores your portal key (~/.config/octo/key, 0600) + exports $OCTO_KEY
#   3. installs opencode (official installer -> ~/.local/bin, no sudo)
#   4. writes ~/.config/opencode/opencode.json with our gateway models
set -euo pipefail

# ---------------------------------------------------------------- configuration
APP_URL="https://octo-app-octo-models.apps.emerg.pcbk.p1.openshiftapps.com"
GW_URL="https://litellm-octo-models.apps.emerg.pcbk.p1.openshiftapps.com/v1"
KEY_FILE="$HOME/.config/octo/key"
OC_CFG="$HOME/.config/opencode/opencode.json"

# ---------------------------------------------------------------- helpers
log() {
  if [ -t 1 ]; then printf '\033[1;36m==>\033[0m %s\n' "$*"; else printf '==> %s\n' "$*"; fi
}
die() {
  if [ -t 2 ]; then printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; else printf 'ERROR: %s\n' "$*" >&2; fi
  exit 1
}

# ---------------------------------------------------------------- steps
# Locate the repo this script lives in; empty when piped via curl|bash.
find_repo_dir() {
  local self="${BASH_SOURCE[0]:-${0:-}}"
  REPO_DIR=""
  case "$self" in
    */*) REPO_DIR="$(cd "$(dirname "$self")/.." 2>/dev/null && pwd || true)" ;;
  esac
  # when piped via curl|bash there is no real path; validate the repo dir
  if [ -n "$REPO_DIR" ] && [ ! -f "$REPO_DIR/scripts/setup-user.sh" ]; then
    REPO_DIR=""
  fi
}

# Parse --key/--check into KEY/CHECK.
parse_args() {
  KEY=""
  CHECK=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --key)
        [ $# -ge 2 ] || die "--key needs a value (e.g. --key sk-...)"
        KEY="$2"
        shift 2
        ;;
      --check) CHECK=1; shift ;;
      *) die "unknown flag: $1 (usage at top of script)" ;;
    esac
  done
}

# Refuse to run as root - the installed tooling is per-user.
require_non_root() {
  if [ "$(id -u)" = 0 ]; then
    die "do not run as root - run as your model-using self (e.g. omtest)"
  fi
}

# Determine KEY: --key flag, $OCTO_KEY, existing key file, else prompt on the tty.
resolve_key() {
  KEY="${KEY:-${OCTO_KEY:-}}"
  if [ -z "$KEY" ] && [ -f "$KEY_FILE" ]; then
    KEY=$(cat "$KEY_FILE")
  fi
  if [ -z "$KEY" ] && [ "$CHECK" = 1 ]; then
    die "no key found ($KEY_FILE) - re-run without --check"
  fi
  if [ -z "$KEY" ]; then
    cat <<EOF

  1. Open:  $APP_URL
  2. Log in with GitHub (needs redhat-et org membership)
  3. Create an API key (shown ONCE) and paste it below.

EOF
    # read from /dev/tty: under curl|bash, stdin is the piped script itself
    if ! read -rsp "  OCTO key (sk-...): " KEY </dev/tty; then
      echo
      die "no tty for key entry - pass --key sk-... or set OCTO_KEY"
    fi
    echo
    [ -n "$KEY" ] || die "no key given"
  fi
}

# Verify the gateway with the real key FIRST (bad key = stop before touching anything).
smoke_test_gateway() {
  log "testing gateway with your key..."
  # Note: smoke model is hardcoded; the installed config's default comes from the LIVE
  # catalog (currently qwen38-flash-next). If qwen38-27b-fast is retired from the
  # gateway, a valid key would fail the smoke test. It runs before the catalog fetch,
  # so it's a single literal knob for now.
  SMOKE=$(curl -sf -m 60 "$GW_URL/chat/completions" \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d '{"model":"qwen38-27b-fast","messages":[{"role":"user","content":"reply with exactly: setup works"}],"max_tokens":50}' \
    ) || die "gateway call failed - is the key correct / gateway up?"
  echo "$SMOKE" | grep -q "choices" || die "unexpected response: $(echo "$SMOKE" | head -c 200)"
  log "gateway OK ✓  (spend for this call shows up in the portal)"
}

# In --check mode: report verification state and exit (never installs anything).
# Otherwise just remember whether opencode was already on PATH for print_done.
run_check() {
  if [ "$CHECK" = 0 ]; then
    PATH_HAD_OC=0
    command -v opencode >/dev/null 2>&1 && PATH_HAD_OC=1
    return 0
  fi
  if command -v opencode >/dev/null 2>&1; then
    log "setup looks good - key works, gateway reachable, opencode on PATH"
  elif [ -x "$HOME/.opencode/bin/opencode" ] || [ -x "$HOME/.local/bin/opencode" ]; then
    log "key works and gateway is reachable; opencode is installed but not on this shell's PATH - run:"
    # shellcheck disable=SC2016  # printed verbatim for the user to paste
    echo '    export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"'
    echo "  (or open a new terminal) and re-run --check"
  else
    log "key works and gateway is reachable, but opencode isn't installed - run this script without --check"
  fi
  exit 0
}

# Persist the key (0600) and export it in the user's shell rc files.
store_key_and_env() {
  mkdir -p "$(dirname "$KEY_FILE")"
  chmod 700 "$(dirname "$KEY_FILE")"
  printf '%s' "$KEY" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
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
}

# Install opencode via the official installer if it isn't on PATH yet.
install_opencode() {
  export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
  if ! command -v opencode >/dev/null 2>&1; then
    log "installing opencode to ~/.local/bin ..."
    curl -fsSL https://opencode.ai/install | bash >/dev/null || die "opencode install failed"
    export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
    command -v opencode >/dev/null || die "opencode not on PATH after install (check ~/.opencode/bin)"
  fi
  log "opencode: $(opencode --version 2>/dev/null | head -1)"
}

# Write opencode.json: live catalog first, repo copy second, embedded fallback last.
write_opencode_config() {
  mkdir -p "$(dirname "$OC_CFG")"
  if [ -f "$OC_CFG" ] && [ ! -f "$OC_CFG.octo-backup" ]; then
    cp "$OC_CFG" "$OC_CFG.octo-backup"
    log "backed up existing config -> $OC_CFG.octo-backup"
  fi
  MODELS_JSON=$(mktemp /tmp/octo-models.XXXXXX)
  trap 'rm -f "$MODELS_JSON"' EXIT
  if curl -sf -m 10 "$APP_URL/setup/models.json" -o "$MODELS_JSON" && command -v python3 >/dev/null 2>&1; then
    # config is written with apiKey -> the key file directly; prints a model summary
    COUNTS=$(python3 - "$OC_CFG" "$GW_URL" "$MODELS_JSON" "$KEY_FILE" <<'PY'
import json, sys
cfg_path, gw, mpath, key_file = sys.argv[1:5]
m = json.load(open(mpath))
prov = {"octo-gateway": {
  "npm": "@ai-sdk/openai-compatible",
  "name": "OCTO Open Models gateway (metered; key from the web app)",
  "options": {"baseURL": gw, "apiKey": f"{{file:{key_file}}}"},
  "models": m["models"],
}}
for lab in m.get("lab", []):
  prov[lab["provider"]] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": lab["name"],
    "options": {"baseURL": lab["base_url"], "apiKey": lab.get("api_key") or "none"},
    "models": {lab["served_model"]: lab["model"]},
  }
cfg = {
  "$schema": "https://opencode.ai/config.json",
  "model": f"octo-gateway/{m['default']}",
  "small_model": f"octo-gateway/{m['small']}",
  "provider": prov,
}
with open(cfg_path, "w") as f:
  json.dump(cfg, f, indent=2)
print(f"{len(m['models'])} gateway models + {len(m.get('lab', []))} lab providers")
PY
) || die "catalog build failed (check the gateway fetch / python3)"
    log "installed opencode.json built from the LIVE catalog ($COUNTS)"
  elif [ -n "$REPO_DIR" ] && [ -f "$REPO_DIR/configs/opencode.json" ]; then
    cp "$REPO_DIR/configs/opencode.json" "$OC_CFG"
    TMP_CFG="$OC_CFG.octo-tmp.$$"
    sed "s|{env:OCTO_KEY}|{file:$KEY_FILE}|" "$OC_CFG" > "$TMP_CFG"
    mv "$TMP_CFG" "$OC_CFG"
    log "installed opencode.json from repo ($REPO_DIR)"
  else
    # embedded gateway-only fallback (note \$schema is escaped, not expanded)
    cat > "$OC_CFG" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "octo-gateway/qwen38-27b-fast",
  "small_model": "octo-gateway/qwen38-27b-fast",
  "provider": {
    "octo-gateway": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OCTO Open Models gateway (metered; key from the web app)",
      "options": { "baseURL": "$GW_URL", "apiKey": "{file:$KEY_FILE}" },
      "models": {
        "qwen38-27b-fast": { "name": "Qwen 3.8 27B (fast)", "limit": { "context": 32768, "output": 8192 }, "tool_call": true, "reasoning": true, "temperature": true }
      }
    }
  }
}
EOF
    log "installed gateway-only opencode.json (embedded fallback copy)"
  fi
  # apiKey must point at the key FILE (not the env) so it survives reboots/launchers
  grep -q "{file:$KEY_FILE}" "$OC_CFG" || die "apiKey not wired to $KEY_FILE"
  log "opencode apiKey -> {file:$KEY_FILE} (no shell env needed)"
}

# Closing hints and pointers.
print_done() {
  log "done! try it:  opencode   (models: /models -> octo-gateway/*)"
  if [ "$PATH_HAD_OC" = 0 ]; then
    log "first time only - this terminal can't be changed by a piped script, so run:"
    # shellcheck disable=SC2016  # printed verbatim for the user to paste
    echo '    export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"'
    echo "  ...or just open a new terminal (bash/zsh pick it up automatically from now on)"
  fi
  log "(key is read from file at startup - works in any shell, after any reboot)"
  echo "  portal:   $APP_URL"
  echo "  grafana:  https://grafana-octo-models.apps.emerg.pcbk.p1.openshiftapps.com"
  echo "  re-check: rerun this script with --check"
}

# ---------------------------------------------------------------- main
find_repo_dir
parse_args "$@"
require_non_root
resolve_key
smoke_test_gateway
run_check
store_key_and_env
install_opencode
write_opencode_config
print_done
