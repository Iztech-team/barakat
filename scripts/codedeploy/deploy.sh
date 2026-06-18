#!/usr/bin/env bash
# CodeDeploy AfterInstall hook — deploy one Frappe app onto the shared bench.
# Branch-per-environment (decided by the CodeDeploy deployment-group name):
#   *-dev-dg  -> branch dev   on baraka-erp-dev
#   *-test-dg -> branch test  on the test bench
#   *-prod-dg -> branch main  on barakat-erp-prod (backs up first)
# Runs as root (per appspec), drops to the `frappe` user for all bench commands.
set -euo pipefail

# ── set per repo ──────────────────────────────────────────────────────────────
APP="barakat"
BENCH="/home/frappe/erp_project"
# ──────────────────────────────────────────────────────────────────────────────

IS_PROD=false
case "${DEPLOYMENT_GROUP_NAME:-}" in
  *prod*) BRANCH="main"; IS_PROD=true ;;
  *test*) BRANCH="test" ;;
  *dev*)  BRANCH="dev"  ;;
  *)      BRANCH="main" ;;          # safe default
esac
echo "[deploy] app=$APP branch=$BRANCH prod=$IS_PROD group=${DEPLOYMENT_GROUP_NAME:-?}"

sudo -H -u frappe IS_PROD="$IS_PROD" APP="$APP" BRANCH="$BRANCH" BENCH="$BENCH" bash -euo pipefail <<'EOF'
export PATH=/usr/bin:/usr/local/bin:/home/frappe/.local/bin:$PATH
cd "$BENCH"

# 1. Safety backup of every site before migrating (prod only, for speed on lower envs)
if [ "$IS_PROD" = "true" ]; then
  bench --site all backup
fi

# 2. Fast-forward the app's checkout to the env's branch
cd "apps/$APP"
# Bench apps may track the GitHub repo under "origin" or "upstream" — use whichever exists.
REMOTE=origin
git remote get-url "$REMOTE" >/dev/null 2>&1 || REMOTE=upstream
git fetch "$REMOTE" "$BRANCH"
git reset --hard FETCH_HEAD   # FETCH_HEAD = just-fetched branch tip (no remote-tracking ref needed)
cd "$BENCH"

# 3. Apply schema/patches, rebuild this app's assets, restart workers
bench --site all migrate
bench build --app "$APP"
bench restart
EOF

echo "[deploy] done"
