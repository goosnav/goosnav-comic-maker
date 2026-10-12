#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$PWD/app/src"
export PYTHONDONTWRITEBYTECODE=1
exec uv run --locked --group dev --python 3.12 python scripts/verify.py
