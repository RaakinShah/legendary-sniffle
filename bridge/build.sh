#!/bin/bash
# Build the native Apple Foundation Models bridge (bridge/aide-fm).
#
# Requires Xcode with a macOS 26+ SDK (the FoundationModels framework). The
# binary runs the on-device model today; once Xcode ships the macOS 27 SDK with
# PrivateCloudComputeLanguageModel, rebuilding here turns on Private Cloud Compute
# for the apple backend (ASSISTANT_APPLE_CLOUD=1) with no Python change.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found — install Xcode (xcode-select --install or the full Xcode)." >&2
  exit 1
fi

echo "Building aide-fm with $(swiftc --version | head -1)…"
swiftc -O -parse-as-library aide-fm.swift -o aide-fm
echo "Built: $(pwd)/aide-fm"
