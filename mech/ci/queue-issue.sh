#!/usr/bin/env bash
# Keep one GitHub issue, "🎤 Song requests", in step with data/queue.json (your to-do list).
set -euo pipefail
body=$(python3 -m mech queue)
num=$(gh issue list --state open --label song-requests --json number --jq '.[0].number' 2>/dev/null || true)
if [ -z "$num" ]; then
  gh label create song-requests --color FFC45A --description "Instagram song request queue" 2>/dev/null || true
  gh issue create --title "🎤 Song requests" --label song-requests --body "$body"
else
  gh issue edit "$num" --body "$body"
fi
