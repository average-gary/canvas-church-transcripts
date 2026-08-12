#!/usr/bin/env bash
# Build the static site into docs/ and index it for search.
#
# The Pagefind step is not optional: without it the search page loads a missing
# script and silently does nothing.
#
#   ./site.sh          # build + index
#   ./site.sh serve    # build + index, then preview at localhost:8000

set -euo pipefail
cd "$(dirname "$0")"

python3 scripts/build_site.py
npx -y pagefind@latest --site docs

if [ "${1:-}" = "serve" ]; then
  echo
  echo "preview: http://localhost:8000/  (ctrl-c to stop)"
  python3 -m http.server 8000 --directory docs
fi
