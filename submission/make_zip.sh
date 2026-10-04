#!/bin/sh
# Item 6: zipped code backup. Archives the committed tree at HEAD, so the zip
# matches the public repository exactly: no .git, no node_modules, no
# untracked scratch. Run from the repo root.
set -e
out="${1:-submission/rental-law-navigator-code.zip}"
git archive --format=zip -o "$out" HEAD
unzip -l "$out" | tail -2
echo "wrote $out"
