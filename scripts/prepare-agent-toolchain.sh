#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
AGENT=""
AGENT_VERSION="latest"
NODE_VERSION="22.23.1"
PYTHON_VERSION="3.13"
BOOTSTRAP_IMAGE="ubuntu:22.04"
OUTPUT=""
FORCE=false

usage() {
    cat <<'EOF'
Prepare a shared host-side agent toolchain for local-Docker Harbor tasks.

Usage:
  scripts/prepare-agent-toolchain.sh --agent AGENT [options]

Agents: codex, claude-code, kimi-cli
Options:
  --agent AGENT              Agent to prepare (required)
  --agent-version VERSION    CLI version to install (default: latest)
  --output PATH              Host output directory (default: .cache/<agent>-toolchain)
  --node-version VERSION     Node.js version for codex/claude-code (default: 22.23.1)
  --python-version VERSION   Python version for kimi-cli (default: 3.13)
  --bootstrap-image IMAGE    Linux image used for the one-time install (default: ubuntu:22.04)
  --force                    Replace an existing toolchain after a successful rebuild
  -h, --help                 Show this help
EOF
}

while (($#)); do
    case "$1" in
        --agent) AGENT=$2; shift 2 ;;
        --agent-version) AGENT_VERSION=$2; shift 2 ;;
        --output) OUTPUT=$2; shift 2 ;;
        --node-version) NODE_VERSION=$2; shift 2 ;;
        --python-version) PYTHON_VERSION=$2; shift 2 ;;
        --bootstrap-image) BOOTSTRAP_IMAGE=$2; shift 2 ;;
        --force) FORCE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$AGENT" in
    codex|claude-code|kimi-cli) ;;
    *) echo "--agent must be one of: codex, claude-code, kimi-cli" >&2; exit 2 ;;
esac

OUTPUT=${OUTPUT:-"$ROOT_DIR/.cache/${AGENT}-toolchain"}
OUTPUT=$(mkdir -p "$(dirname "$OUTPUT")" && cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")
if [[ -e "$OUTPUT" && "$FORCE" != true ]]; then
    echo "Toolchain already exists: $OUTPUT (use --force to replace it)" >&2
    exit 1
fi

STAGING="$OUTPUT.build.$$"
cleanup() {
    if [[ -d "$STAGING" ]]; then
        docker run --rm -v "$STAGING:/staging" "$BOOTSTRAP_IMAGE" \
            bash -lc 'rm -rf /staging/* /staging/.[!.]* /staging/..?*' >/dev/null 2>&1 || true
        rmdir "$STAGING" 2>/dev/null || true
    fi
}
trap cleanup EXIT
mkdir -p "$STAGING"

docker run --rm \
    -e AGENT="$AGENT" -e AGENT_VERSION="$AGENT_VERSION" \
    -e NODE_VERSION="$NODE_VERSION" -e PYTHON_VERSION="$PYTHON_VERSION" \
    -e HTTP_PROXY="${HTTP_PROXY:-}" -e HTTPS_PROXY="${HTTPS_PROXY:-}" -e NO_PROXY="${NO_PROXY:-}" \
    -v "$STAGING:/opt/codemem-agent" "$BOOTSTRAP_IMAGE" bash -lc '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl ripgrep xz-utils
rm -rf /var/lib/apt/lists/*
mkdir -p /opt/codemem-agent/bin
install -m 0755 "$(command -v rg)" /opt/codemem-agent/bin/rg

install_node() {
    case "$(uname -m)" in x86_64) node_arch=x64 ;; aarch64|arm64) node_arch=arm64 ;; *) exit 1 ;; esac
    archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
    tmpdir=$(mktemp -d); trap "rm -rf \"$tmpdir\"" EXIT
    curl --fail --location --retry 5 --retry-all-errors "https://nodejs.org/dist/v${NODE_VERSION}/$archive" -o "$tmpdir/$archive"
    curl --fail --location --retry 5 --retry-all-errors "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$tmpdir/SHASUMS256.txt"
    (cd "$tmpdir" && grep "  $archive$" SHASUMS256.txt | sha256sum --check --strict)
    mkdir -p /opt/codemem-agent/node
    tar -xJf "$tmpdir/$archive" --strip-components=1 -C /opt/codemem-agent/node
    ln -s ../node/bin/node /opt/codemem-agent/bin/node
    export PATH="/opt/codemem-agent/node/bin:/opt/codemem-agent/bin:$PATH"
}

case "$AGENT" in
  codex)
    install_node
    npm install --global --prefix /opt/codemem-agent "@openai/codex@${AGENT_VERSION}"
    codex --version
    ;;
  claude-code)
    install_node
    npm install --global --prefix /opt/codemem-agent "@anthropic-ai/claude-code@${AGENT_VERSION}"
    claude --version
    ;;
  kimi-cli)
    export UV_TOOL_DIR=/opt/codemem-agent/uv/tools
    export UV_TOOL_BIN_DIR=/opt/codemem-agent/bin
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    version_spec=""; [[ "$AGENT_VERSION" != latest ]] && version_spec="==${AGENT_VERSION}"
    uv tool install --python "$PYTHON_VERSION" "kimi-cli${version_spec}"
    export PATH="/opt/codemem-agent/bin:$PATH"
    kimi --version
    ;;
esac

printf "%s\n" "$AGENT" > /opt/codemem-agent/AGENT
printf "%s\n" "$AGENT_VERSION" > /opt/codemem-agent/AGENT_VERSION
if [[ -x /opt/codemem-agent/bin/node ]]; then node --version > /opt/codemem-agent/NODE_VERSION; fi
'

if [[ -e "$OUTPUT" ]]; then
    BACKUP="$OUTPUT.backup.$(date +%Y%m%d%H%M%S)"
    mv "$OUTPUT" "$BACKUP"
    echo "Previous toolchain moved to: $BACKUP"
fi
mv "$STAGING" "$OUTPUT"
trap - EXIT
echo "Prepared shared $AGENT toolchain: $OUTPUT"
