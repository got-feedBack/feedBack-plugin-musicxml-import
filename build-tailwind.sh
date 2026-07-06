#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Pin the same Tailwind 3.x core uses so output stays diff-stable.
exec npx -y tailwindcss@3.4.19 \
    -c tailwind.config.js \
    -i _plugin.src.css \
    -o assets/plugin.css \
    --minify
