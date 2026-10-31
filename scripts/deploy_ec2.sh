#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed on this host" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin is not available (try installing docker-compose-plugin)" >&2
  exit 1
fi

command -v python3 >/dev/null
python3 "$(dirname "$0")/deploy.py"
