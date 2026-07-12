#!/bin/bash

# Print a catalog of the stuff in the spool directory and tell which payload type each file has in it.
# requires ripgrep (rg) to be installed and in the path.

for f in spool/*; do
  if rg -q '"event"\s*:' "$f"; then
    echo "bugzilla  $f"
  elif rg -q '"embeds"\s*:|"attachments"\s*:' "$f"; then
    echo "discord   $f"
  else
    echo "unknown   $f"
  fi
done
