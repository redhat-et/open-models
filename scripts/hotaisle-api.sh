#!/usr/bin/env bash
# Hot Aisle (admin.hotaisle.app) VM lifecycle via their REST API.
# Billing runs while the VM EXISTS - power-off does NOT stop the meter, only
# DELETE does. Deleting loses the disk, so `up` re-provisions a bare VM and
# hotaisle.sh serve re-pulls the vLLM image + re-downloads the ~27GB weights
# on first run (~45-60 min total cold start).
#
#   ./scripts/hotaisle-api.sh spec     # record live VM specs to hotaisle-vm.json (run once)
#   ./scripts/hotaisle-api.sh off      # DELETE the VM - stops billing (disk gone)
#   ./scripts/hotaisle-api.sh up       # re-provision from specs + wait + serve (cold)
#   ./scripts/hotaisle-api.sh state    # VM state + IP
#   ./scripts/hotaisle-api.sh list     # team VMs (discovery)
#   ./scripts/hotaisle-api.sh pushkey  # register the pod's ssh key with the provider
#
# Needs HOTAISLE_API_TOKEN (panel -> Settings -> API keys, stored in
# secrets.env -> app secret), plus HOTAISLE_TEAM and HOTAISLE_VM.
set -euo pipefail

API="${HOTAISLE_API:-https://admin.hotaisle.app/api}"
TEAM="${HOTAISLE_TEAM:?set HOTAISLE_TEAM (panel team handle)}"
VM="${HOTAISLE_VM:?set HOTAISLE_VM (VM name from: hotaisle-api.sh list)}"
SPECS="${HOTAISLE_SPECS:-$(dirname "$0")/hotaisle-vm.json}"
PUBKEY="${HOTAISLE_PUBKEY:-/tmp/om-ssh-home/.ssh/id_ed25519.pub}"

api() { curl -sS -m 60 -H "Authorization: Token ${HOTAISLE_API_TOKEN:?set HOTAISLE_API_TOKEN}" "$@"; }

CFG_OPTS=()
[ -n "${HOTAISLE_SSH_CONFIG:-}" ] && [ -f "$HOTAISLE_SSH_CONFIG" ] && CFG_OPTS=(-F "$HOTAISLE_SSH_CONFIG")
sshbox() { ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  ${CFG_OPTS[@]+"${CFG_OPTS[@]}"} ${HOTAISLE_SSH_OPTS:-} "$HOTAISLE_SSH" "$@"; }

getvm() { api "$API/teams/$TEAM/virtual_machines/$VM/" 2>/dev/null || true; }

case "${1:-}" in
list)
  api "$API/teams/$TEAM/virtual_machines/" | python3 -m json.tool
  ;;
state)
  getvm | python3 -m json.tool
  ;;
spec)
  getvm | python3 -c "
import json, sys
vm = json.load(sys.stdin)
specs = {k: vm[k] for k in ('cpu_cores', 'ram_capacity', 'disk_capacity') if k in vm}
if vm.get('gpus'):
    specs['gpus'] = [{k: g[k] for k in ('count', 'manufacturer', 'model') if k in g} for g in vm['gpus']]
json.dump(specs, open('$SPECS', 'w'), indent=1)
print(json.dumps(specs, indent=1))
print('recorded to $SPECS - commit it so up can rebuild this box')"
  ;;
pushkey)
  key=$(cat "$PUBKEY" 2>/dev/null || ssh-keygen -y -f "${PUBKEY%.pub}")
  api -X POST -H 'Content-Type: application/json' \
    -d "{\"key\": $(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$key")}" \
    "$API/user/ssh_keys/" | head -c 300; echo
  ;;
off)
  if [ ! -f "$SPECS" ]; then
    getvm | grep -q . || { echo "no recorded specs and VM not found - cannot delete blind"; exit 1; }
    "$0" spec
  fi
  echo "deleting VM $VM - billing stops, disk is destroyed"
  api -X DELETE "$API/teams/$TEAM/virtual_machines/$VM/"
  echo "deleted. bring back with: ./scripts/hotaisle-api.sh up (cold ~45-60 min)"
  ;;
up)
  [ -f "$SPECS" ] || { echo "missing $SPECS - run 'spec' while a VM exists first"; exit 1; }
  existing=$(api "$API/teams/$TEAM/virtual_machines/" | python3 -c "import json,sys; vms=json.load(sys.stdin); print(f\"{vms[0]['name']} {vms[0].get('ip_address','')}\" if vms else '')")
  if [ -n "$existing" ]; then
    VM=$(echo "$existing" | cut -d' ' -f1)
    IP=$(echo "$existing" | cut -d' ' -f2)
    echo "VM already exists: $VM ($IP) - skipping provision"
  else
    echo "provisioning VM from $SPECS"
    body=$(SPECS="$SPECS" python3 -c "
import json, os
specs = json.load(open(os.environ['SPECS']))
if os.environ.get('HOTAISLE_USERDATA_URL'):
    specs['user_data_url'] = os.environ['HOTAISLE_USERDATA_URL']
print(json.dumps(specs))")
    created=$(echo "$body" | api -X POST -H 'Content-Type: application/json' -d @- \
      "$API/teams/$TEAM/virtual_machines/")
    VM=$(echo "$created" | python3 -c "import json,sys;print(json.load(sys.stdin)['name'])")
    echo "vm: $VM"
  fi
  echo -n "waiting for ip "
  for _ in $(seq 1 60); do
    IP=$(api "$API/teams/$TEAM/virtual_machines/$VM/" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ip_address") or "")' 2>/dev/null || true)
    [ -n "$IP" ] && break
    echo -n .; sleep 15
  done
  [ -n "$IP" ] || { echo " TIMEOUT waiting for IP"; exit 1; }
  echo " ip=$IP"
  export HOTAISLE_SSH="hotaisle@$IP"
  echo -n "waiting for ssh "
  for _ in $(seq 1 40); do
    if sshbox true 2>/dev/null; then echo " up"; break; fi
    echo -n .; sleep 15
  done
  sshbox "command -v docker >/dev/null || { curl -fsSL https://get.docker.com | sudo sh; sudo usermod -aG docker \$USER; }"
  # IP may differ from catalog api_base -> regenerate + redeploy if so:
  if [ "$IP" != "${HOTAISLE_IP:-}" ] && [ -n "${HOTAISLE_IP:-}" ]; then
    echo "NOTE: new ip $IP != catalog ip $HOTAISLE_IP - update catalog.yaml, then make gen && make deploy"
  fi
  exec "$(dirname "$0")/hotaisle.sh" serve
  ;;
*)
  grep '^#   ' "$0" | sed 's/^#   //'; exit 1
  ;;
esac
