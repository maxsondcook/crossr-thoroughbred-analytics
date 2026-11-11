#!/bin/sh
# Local API. All data comes from Postgres: point DATABASE_URL at the crossr_dev
# database (or a copy) first.
set -e
cd "$(dirname "$0")/.."
if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL is not set (Crossr reads everything from Postgres)." >&2
  exit 1
fi
uvicorn crossr_api.app:app --reload --port 8000
