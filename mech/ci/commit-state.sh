#!/usr/bin/env bash
# Commit the automation's memory (data/) back to the repo, surviving a concurrent run's push.
set -euo pipefail
msg="${1:-update state}"
git config user.name "mech.melody bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data/
git diff --cached --quiet && { echo "state unchanged"; exit 0; }
git commit -qm "$msg"
for i in 1 2 3 4 5; do
  git pull -q --rebase origin "${GITHUB_REF_NAME:-main}" && git push -q origin "HEAD:${GITHUB_REF_NAME:-main}" && exit 0
  sleep $((i * 5))
done
echo "could not push the state" >&2; exit 1
