#!/usr/bin/env bash
# Capture a deterministic content digest from one disposable rehearsal cluster.
#
# Usage: rehearsal_capture_digest.sh <unix-socket-directory> <port> <output-file>
#
# The grep is not cosmetic. psql can emit command tags and notices on the same
# stream as the result rows, and a digest file that contains one line of noise
# would either be rejected later or, worse, quietly change a comparison. Only
# well-formed `table|rows|checksum` records survive, and an empty result is an
# error rather than an empty comparison that would trivially succeed.
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <socket-directory> <port> <output-file>" >&2
  exit 2
fi

socket_directory="$1"
port="$2"
output="$3"
query="$(dirname "$0")/rehearsal_content_digest.sql"

raw="$(mktemp)"
trap 'rm -f "$raw"' EXIT

psql \
  --no-psqlrc --quiet --no-align --tuples-only \
  --host="$socket_directory" --port="$port" \
  --username=postgres --dbname=postgres \
  --file="$query" >"$raw"

grep -E '^[^|]+\|[0-9]+\|[0-9a-f]{32}$' "$raw" | LC_ALL=C sort >"$output" || true

entries="$(wc -l <"$output" | tr -d ' ')"
if [ "$entries" -eq 0 ]; then
  echo "::error title=Empty content digest::No table digests were captured from port $port."
  exit 1
fi
echo "Captured $entries table digests from port $port."
