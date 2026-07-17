#!/bin/bash

# Print a catalog of the stuff in the spool directory and tell which payload type each file has in it.
# requires ripgrep (rg) to be installed and in the path.

shopt -s nullglob

for f in spool/*; do
  base=$(basename "$f")
  if [[ "$base" == bugzilla-* ]]; then
    echo "bugzilla  $f"
  elif [[ "$base" == discord-* ]]; then
    echo "discord   $f"
  elif rg -q '"event"\s*:' "$f"; then
    echo "bugzilla  $f"
  elif rg -q '"embeds"\s*:|"attachments"\s*:' "$f"; then
    echo "discord   $f"
  else
    echo "unknown   $f"
  fi
done
