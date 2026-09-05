#!/usr/bin/env bash
# Offline lab: full stack on podman with fake vLLM backends. No GPUs, no VPN.
#   ./scripts/lab.sh up | down | status | logs [name]
# Uses static IPs + --add-host (not network DNS) so it works even where
# aardvark-dns is broken.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

NET=om-lab
SUBNET=10.89.44
IMG=octo-lab-backend
P=4080   # litellm
PROM=4081
APP=4082
MK=4083  # mocks -> 4083 flash, 4084 fast-a, 4085 fast-b
MASTER=sk-lab-master

IP_PG=$SUBNET.2 IP_FLASH=$SUBNET.4 IP_FASTA=$SUBNET.5 IP_FASTB=$SUBNET.6 IP_LLM=$SUBNET.7 IP_APP=$SUBNET.9
HOSTS="--add-host om-lab-pg:$IP_PG --add-host mock-flash:$IP_FLASH --add-host mock-fast-a:$IP_FASTA --add-host mock-fast-b:$IP_FASTB --add-host om-lab-litellm:$IP_LLM"

rm_named() { podman rm -f "om-lab-$1" >/dev/null 2>&1 || true; }

up() {
  down
  podman network rm -f "$NET" >/dev/null 2>&1 || true
  podman network create "$NET" --subnet "$SUBNET.0/24" --disable-dns >/dev/null
  podman build -t "$IMG" -f backend/Containerfile . >/dev/null

  podman run -d --name om-lab-pg --network "$NET" --ip "$IP_PG" $HOSTS \
    -e POSTGRES_DB=litellm -e POSTGRES_USER=litellm -e POSTGRES_PASSWORD=litellm \
    docker.io/library/postgres:16 >/dev/null

  local pair name hostport ip
  for pair in "flash:$MK:$IP_FLASH" "fast-a:$((MK+1)):$IP_FASTA" "fast-b:$((MK+2)):$IP_FASTB"; do
    IFS=: read -r name hostport ip <<<"$pair"
    podman run -d --name "om-lab-mock-$name" --network "$NET" --ip "$ip" $HOSTS \
      -e MOCK_ID="$name" -e MOCK_MODEL="mock-$name" \
      -p "$hostport":8000 -v "$PWD/scripts/mock_vllm.py":/app/mock.py:ro,z \
      docker.io/library/python:3.12-slim python /app/mock.py >/dev/null
  done

  # wait for PG before litellm's prisma boots (static IPs + --restart anyway)
  for _ in $(seq 1 30); do
    podman exec om-lab-pg pg_isready -q >/dev/null 2>&1 && break
    sleep 2
  done

  podman run -d --restart unless-stopped --name om-lab-litellm --network "$NET" --ip "$IP_LLM" $HOSTS \
    -p "$P":4000 \
    -e LITELLM_MASTER_KEY="$MASTER" \
    -e DATABASE_URL=postgresql://litellm:litellm@$IP_PG:5432/litellm \
    -v "$PWD/lab/litellm-config.yaml":/app/config.yaml:ro,z \
    ghcr.io/berriai/litellm:v1.99.1 --config /app/config.yaml --port 4000 >/dev/null

  podman run -d --name om-lab-prometheus --network "$NET" $HOSTS -p "$PROM":9090 \
    -v "$PWD/lab/prometheus.yml":/etc/prometheus/prometheus.yml:ro,z \
    quay.io/prometheus/prometheus:latest >/dev/null

  podman run -d --restart unless-stopped --name om-lab-app --network "$NET" --ip "$IP_APP" $HOSTS \
    -p "$APP":8020 \
    -e LITELLM_URL=http://$IP_LLM:4000 \
    -e LITELLM_MASTER_KEY="$MASTER" \
    -e SESSION_SECRET=lab -e GITHUB_CLIENT_ID=lab -e GITHUB_CLIENT_SECRET=lab \
    "$IMG" >/dev/null

  echo "gateway    http://localhost:$P  (master key: $MASTER)"
  echo "portal     http://localhost:$APP/api/health"
  echo "prometheus http://localhost:$PROM"
  echo "mocks      flash=$MK fast-a=$((MK+1)) fast-b=$((MK+2)) (admin fail: POST /admin/fail)"
}

down() {
  local c
  for c in $(podman ps -aq --filter "name=om-lab-" 2>/dev/null); do podman rm -f "$c" >/dev/null 2>&1 || true; done
}

status() { podman ps --filter "name=om-lab-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }

case "${1:-up}" in
  up) up ;;
  down) down ;;
  status) status ;;
  logs) podman logs --tail=50 "om-lab-${2:-litellm}" ;;
  *) echo "usage: $0 up|down|status|logs [name]"; exit 1 ;;
esac
