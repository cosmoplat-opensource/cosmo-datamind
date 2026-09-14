#!/usr/bin/env bash
# Persistent Gunicorn deployment. Configuration is read literally from .env.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python scripts/service.py start "$@"
