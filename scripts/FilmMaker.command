#!/bin/bash
# Double-click in Finder to open the Film Maker UI (macOS).
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
exec .venv/bin/filmmaker ui "$@"
