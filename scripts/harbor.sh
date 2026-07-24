#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEFAULT_HARBOR=/data/zhangpeilin/miniconda3/envs/CodeMem/bin/harbor

if [[ -n "${HARBOR_BIN:-}" ]]; then
    harbor_bin=$HARBOR_BIN
elif command -v harbor >/dev/null 2>&1; then
    harbor_bin=$(command -v harbor)
elif [[ -x "$DEFAULT_HARBOR" ]]; then
    harbor_bin=$DEFAULT_HARBOR
else
    echo "Harbor executable not found; set HARBOR_BIN" >&2
    exit 1
fi

export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$harbor_bin" "$@"
