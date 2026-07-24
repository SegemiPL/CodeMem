#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
destination=${1:-"$repo_root/../.cache/network-toolchain"}
destination_parent=$(mkdir -p "$(dirname "$destination")" && cd "$(dirname "$destination")" && pwd)
destination="$destination_parent/$(basename "$destination")"
if [[ "$destination" == "/" || "$destination" == "$HOME" ]]; then
  echo "Refusing unsafe destination: $destination" >&2
  exit 1
fi
staging=$(mktemp -d "${destination}.tmp.XXXXXX")
trap 'rm -rf "$staging"' EXIT

for command_name in iptables ip6tables; do
  command_path=$(command -v "$command_name" || true)
  if [[ -z "$command_path" ]]; then
    echo "Missing host command: $command_name" >&2
    exit 1
  fi
  resolved=$(readlink -f "$command_path")
  mkdir -p "$staging/libexec"
  cp "$resolved" "$staging/libexec/$command_name"
done

mkdir -p "$staging/lib" "$staging/xtables" "$staging/bin"
while IFS= read -r library; do
  cp -L "$library" "$staging/lib/"
done < <(
  {
    ldd "$staging/libexec/iptables"
    ldd "$staging/libexec/ip6tables"
  } |
    awk '/=> \// {print $3} /^\// {print $1}' |
    sort -u
)

loader=$(ldd "$staging/libexec/iptables" | awk '/ld-linux/ {print $1; exit}')
if [[ -z "$loader" ]]; then
  echo "Could not locate the dynamic loader for iptables" >&2
  exit 1
fi
cp -L "$loader" "$staging/lib/loader"

xtables_dir=$(find /usr/lib /lib -type d -name xtables 2>/dev/null | head -n 1)
if [[ -z "$xtables_dir" ]]; then
  echo "Could not locate xtables extensions" >&2
  exit 1
fi
cp -a "$xtables_dir/." "$staging/xtables/"

for command_name in iptables ip6tables; do
  sed \
    -e "s|@COMMAND@|$command_name|g" \
    -e 's|@ROOT@|$(cd "$(dirname "${BASH_SOURCE[0]}")/.." \&\& pwd)|g' \
    "$repo_root/scripts/network-tool-wrapper.sh.in" \
    >"$staging/bin/$command_name"
  chmod 0755 "$staging/bin/$command_name"
done

rm -rf "$destination"
mv "$staging" "$destination"
trap - EXIT
echo "$destination"
