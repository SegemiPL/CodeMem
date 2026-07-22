#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUTPUT="$ROOT_DIR/.cache/codex-toolchain"
CODEX_VERSION="latest"
NODE_VERSION="22.23.1"
BOOTSTRAP_IMAGE="ubuntu:22.04"
FORCE=false

usage() {
    cat <<'EOF'
Prepare one host-side Codex toolchain for all local-Docker Harbor tasks.

Usage:
  scripts/prepare-codex-toolchain.sh [options]

Options:
  --output PATH             Host output directory (default: .cache/codex-toolchain)
  --codex-version VERSION   Codex npm version to install (default: latest)
  --node-version VERSION    Node.js version to install (default: 22.23.1)
  --bootstrap-image IMAGE   Linux image used for the one-time install (default: ubuntu:22.04)
  --force                   Replace an existing toolchain after a successful rebuild
  -h, --help                Show this help
EOF
}

while (($#)); do
    case "$1" in
        --output)
            OUTPUT=$2
            shift 2
            ;;
        --codex-version)
            CODEX_VERSION=$2
            shift 2
            ;;
        --node-version)
            NODE_VERSION=$2
            shift 2
            ;;
        --bootstrap-image)
            BOOTSTRAP_IMAGE=$2
            shift 2
            ;;
        --force)
            FORCE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

OUTPUT=$(mkdir -p "$(dirname "$OUTPUT")" && cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")
if [[ -e "$OUTPUT" && "$FORCE" != true ]]; then
    echo "Toolchain already exists: $OUTPUT (use --force to replace it)" >&2
    exit 1
fi

STAGING="$OUTPUT.build.$$"
cleanup() {
    if [[ -d "$STAGING" ]]; then
        if ! rmdir "$STAGING" 2>/dev/null; then
            if docker image inspect "$BOOTSTRAP_IMAGE" >/dev/null 2>&1; then
                docker run --rm -v "$STAGING:/staging" "$BOOTSTRAP_IMAGE" \
                    bash -lc 'rm -rf /staging/* /staging/.[!.]* /staging/..?*' \
                    >/dev/null 2>&1 || true
                rmdir "$STAGING" 2>/dev/null || true
            else
                echo "Incomplete staging directory retained for inspection: $STAGING" >&2
            fi
        fi
    fi
}
trap cleanup EXIT
mkdir -p "$STAGING"

docker run --rm \
    -e CODEX_VERSION="$CODEX_VERSION" \
    -e NODE_VERSION="$NODE_VERSION" \
    -e HTTP_PROXY="${HTTP_PROXY:-}" \
    -e HTTPS_PROXY="${HTTPS_PROXY:-}" \
    -e NO_PROXY="${NO_PROXY:-}" \
    -v "$STAGING:/opt/codemem-agent" \
    "$BOOTSTRAP_IMAGE" \
    bash -lc '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl ripgrep xz-utils
rm -rf /var/lib/apt/lists/*

case "$(uname -m)" in
    x86_64) node_arch=x64 ;;
    aarch64|arm64) node_arch=arm64 ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
base_url="https://nodejs.org/dist/v${NODE_VERSION}"
tmpdir=$(mktemp -d)
trap '\''rm -rf "$tmpdir"'\'' EXIT
curl --fail --location --retry 5 --retry-all-errors \
    "$base_url/$archive" -o "$tmpdir/$archive"
curl --fail --location --retry 5 --retry-all-errors \
    "$base_url/SHASUMS256.txt" -o "$tmpdir/SHASUMS256.txt"
(cd "$tmpdir" && grep "  $archive$" SHASUMS256.txt | sha256sum --check --strict)

mkdir -p /opt/codemem-agent/node /opt/codemem-agent/bin
tar -xJf "$tmpdir/$archive" --strip-components=1 -C /opt/codemem-agent/node
ln -s ../node/bin/node /opt/codemem-agent/bin/node
export PATH="/opt/codemem-agent/node/bin:/opt/codemem-agent/bin:$PATH"
npm install --global --prefix /opt/codemem-agent "@openai/codex@${CODEX_VERSION}"
install -m 0755 "$(command -v rg)" /opt/codemem-agent/bin/rg

node --version > /opt/codemem-agent/NODE_VERSION
codex --version > /opt/codemem-agent/CODEX_VERSION
cat /opt/codemem-agent/NODE_VERSION
cat /opt/codemem-agent/CODEX_VERSION
'

if [[ -e "$OUTPUT" ]]; then
    BACKUP="$OUTPUT.backup.$(date +%Y%m%d%H%M%S)"
    mv "$OUTPUT" "$BACKUP"
    echo "Previous toolchain moved to: $BACKUP"
fi
mv "$STAGING" "$OUTPUT"
trap - EXIT
echo "Prepared shared Codex toolchain: $OUTPUT"
