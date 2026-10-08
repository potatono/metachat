#!/usr/bin/env bash
# Bootstrap the metachat channel into the current project:
#   curl -fsSL http://anarchy:9000/setup/channel.sh | bash
#
# Fetches the channel code from metachat into ~/.metachat/channel, installs
# its dependencies, then hands off to setup/install.ts for the project side.
# Base tooling (bun, python3, git, claude) must already be installed.

# Everything lives in main so a truncated download never runs half a script.
main() {
  set -euo pipefail

  # Filled in by metachat's web server with the URL this script came from.
  local base_url="${METACHAT_SETUP_URL:-__METACHAT_SETUP_URL__}"
  local home_dir="${METACHAT_HOME:-$HOME/.metachat}"
  local project_dir
  project_dir="$(pwd)"

  local missing=()
  for tool in bun python3 git claude curl tar; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo "metachat setup needs these on PATH first: ${missing[*]}" >&2
    exit 1
  fi

  echo "Fetching channel from $base_url ..."
  mkdir -p "$home_dir"
  # Global, not local: the EXIT trap fires after main has returned.
  staging="$(mktemp -d "$home_dir/channel.new.XXXXXX")"
  trap 'rm -rf "$staging"' EXIT

  curl -fsSL "$base_url/setup/channel.tgz" | tar -xzf - -C "$staging"
  (cd "$staging" && bun install --frozen-lockfile)

  # Swap the fresh copy in; a failed update leaves the old install alone.
  if [ -d "$home_dir/channel" ]; then
    rm -rf "$home_dir/channel.old"
    mv "$home_dir/channel" "$home_dir/channel.old"
  fi
  mv "$staging" "$home_dir/channel"
  rm -rf "$home_dir/channel.old"
  trap - EXIT

  # The installer prompts for the bridge token; stdin is the pipe, so give
  # it the terminal when there is one (else it relies on env overrides).
  if (exec </dev/tty) 2>/dev/null; then
    bun run "$home_dir/channel/setup/install.ts" "$project_dir" </dev/tty
  else
    bun run "$home_dir/channel/setup/install.ts" "$project_dir" </dev/null
  fi
}

main "$@"
