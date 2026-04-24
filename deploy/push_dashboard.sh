#!/usr/bin/env bash
# Push the latest dashboard.html to the `render-dashboard` orphan branch.
#
# Render Static Site watches that branch. We force-push a single-file branch
# every run, so main branch stays clean (source only) and the render branch
# doesn't accumulate noisy history.
#
# Only pushes if dashboard.html actually changed since the last push — avoids
# hammering GitHub with no-op commits every 5 minutes.
#
# Runs as the `bot` user from systemd timer (dashboard-push.timer).
# Requires: an SSH deploy key on the droplet with WRITE access to the repo,
# OR a GitHub PAT in ~/.git-credentials.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/bot/crypto-bot}"
BRANCH="${BRANCH:-render-dashboard}"
DASHBOARD_FILE="${REPO_DIR}/dashboard.html"
WORKTREE="${REPO_DIR}/.render-worktree"

cd "${REPO_DIR}"

if [[ ! -f "${DASHBOARD_FILE}" ]]; then
  echo "No dashboard.html yet — bots haven't run a cycle. Skipping."
  exit 0
fi

# Ensure origin has the branch (first run will create it if missing)
if ! git ls-remote --exit-code --heads origin "${BRANCH}" >/dev/null 2>&1; then
  echo "Creating render-dashboard branch on origin (first run)..."
  git worktree remove -f "${WORKTREE}" 2>/dev/null || true
  git worktree add --orphan -b "${BRANCH}" "${WORKTREE}"
  cp "${DASHBOARD_FILE}" "${WORKTREE}/index.html"
  cp "${DASHBOARD_FILE}" "${WORKTREE}/dashboard.html"
  cd "${WORKTREE}"
  git add index.html dashboard.html
  git commit -q -m "dashboard: initial deploy"
  git push -u origin "${BRANCH}"
  cd "${REPO_DIR}"
  git worktree remove -f "${WORKTREE}"
  echo "Created branch and pushed."
  exit 0
fi

# Fetch the latest render-dashboard to compare
git fetch -q origin "${BRANCH}"
LAST_REMOTE_SHA=$(git rev-parse "origin/${BRANCH}:dashboard.html" 2>/dev/null || echo "")
CURRENT_SHA=$(git hash-object "${DASHBOARD_FILE}")

if [[ "${LAST_REMOTE_SHA}" == "${CURRENT_SHA}" ]]; then
  echo "Dashboard unchanged since last push. Skipping."
  exit 0
fi

echo "Dashboard changed (${CURRENT_SHA:0:8} vs ${LAST_REMOTE_SHA:0:8}). Pushing..."

# Use a worktree so we don't disturb main's working copy
git worktree remove -f "${WORKTREE}" 2>/dev/null || true
git worktree add "${WORKTREE}" "${BRANCH}"

# Copy the fresh dashboard in. Keep both filenames so Render can serve either.
cp "${DASHBOARD_FILE}" "${WORKTREE}/dashboard.html"
cp "${DASHBOARD_FILE}" "${WORKTREE}/index.html"

cd "${WORKTREE}"
git add dashboard.html index.html
if git diff --cached --quiet; then
  echo "No content change after copy — skipping commit."
else
  TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  git commit -q -m "dashboard: auto-update ${TS}"
  git push -q origin "${BRANCH}"
  echo "Pushed to origin/${BRANCH} at ${TS}"
fi

cd "${REPO_DIR}"
git worktree remove -f "${WORKTREE}"
