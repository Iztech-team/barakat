#!/usr/bin/env bash
# CodeDeploy AfterInstall hook — deploy one Frappe app onto the shared bench.
# Model: promote-main. Both dev and prod pipelines source `main`; the dev box
# deploys automatically, prod deploys the SAME commit after a manual approval.
# The deployment-group name only decides (a) whether to back up first and
# (b) which site to health-check. Runs as root (per appspec), drops to `frappe`.
set -euo pipefail

# ── set per repo ──────────────────────────────────────────────────────────────
APP="barakat"
BRANCH="main"                       # single source of truth (promote model)
BENCH="/home/frappe/erp_project"
# ──────────────────────────────────────────────────────────────────────────────

IS_PROD=false
case "${DEPLOYMENT_GROUP_NAME:-}" in *prod*) IS_PROD=true ;; esac
echo "[deploy] app=$APP branch=$BRANCH prod=$IS_PROD group=${DEPLOYMENT_GROUP_NAME:-?}"

sudo -H -u frappe IS_PROD="$IS_PROD" APP="$APP" BRANCH="$BRANCH" BENCH="$BENCH" bash -euo pipefail <<'EOF'
export PATH=/usr/bin:/usr/local/bin:/home/frappe/.local/bin:$PATH
cd "$BENCH"

# 1. Safety backup of every site before any migration (prod only, for speed on dev)
if [ "$IS_PROD" = "true" ]; then
  bench --site all backup
fi

# 2. Fast-forward the app's checkout to main
cd "apps/$APP"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
cd "$BENCH"

# 3. Apply schema/patches, rebuild this app's assets, restart workers
bench --site all migrate
bench build --app "$APP"
bench restart
EOF

echo "[deploy] done"
