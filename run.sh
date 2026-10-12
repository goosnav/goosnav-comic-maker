#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    echo 'First launch needs uv. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh'
    exit 1
  fi
fi
export PYTHONPATH="$PWD/app/src"
exec uv run --locked --python 3.12 python -m comic_maker "$@"
