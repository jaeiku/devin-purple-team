#!/usr/bin/env bash
# One-command demo: build the stack, bring it up, run the full simulated flow.
#
#   ./scripts/demo.sh            # demo mode (no credentials needed)
#   ./scripts/demo.sh --all      # inject the entire catalogue
#
# Any extra arguments are forwarded to scripts/simulate.py.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "==> no .env found, copying .env.example (demo mode, no credentials)"
  cp .env.example .env
fi

echo "==> building and starting the stack"
docker compose up -d --build

echo "==> running the end-to-end simulation"
python3 scripts/simulate.py "$@"

echo
echo "==> stack is still running. Dashboard: http://localhost:${DASHBOARD_PORT:-8003}"
echo "    Stop it with: docker compose down          (add -v to wipe the datastore)"
