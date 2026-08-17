#!/usr/bin/env bash
# CodeDeploy ValidateService / ApplicationStop hook — confirm the bench is serving.
# Non-fatal on ApplicationStop (pre-deploy); fatal on ValidateService (post-deploy).
set -uo pipefail

# Pick a representative site per env to health-check.
#
# The site named here must EXIST on the bench. `pos2.35.158.120.8.nip.io` was mapped for
# dev and test long after it had been removed, so every deployment reached this hook,
# got a 404 for a site that is not there, and was marked Failed — which then fired an
# automatic rollback that failed at the same line. Both dev and test had been red since
# 2026-08-16 while the code was deploying perfectly well, and a genuinely broken deploy
# would have looked exactly the same.
#
# `master` rather than a shop: it is the tenant registry, so it is the one site that
# cannot be deleted during ordinary QA. A per-shop site is somebody's to remove.
case "${DEPLOYMENT_GROUP_NAME:-}" in
  *prod*) SITE="barakat.iztech.net" ;;
  *test*) SITE="master.35.158.120.8.nip.io" ;;   # site name resolves to localhost below
  *dev*)  SITE="master.35.158.120.8.nip.io" ;;
  *)      SITE="" ;;
esac
[ -z "$SITE" ] && { echo "[validate] no site mapped, skipping"; exit 0; }

# Say WHICH of the two things went wrong. A missing site and a sick site both answer 404
# through nginx, and last time that ambiguity cost a fortnight of red deployments nobody
# could read.
BENCH_SITES="/home/frappe/erp_project/sites"
if [ -d "$BENCH_SITES" ] && [ ! -d "$BENCH_SITES/$SITE" ]; then
  echo "[validate] '$SITE' is not a site on this bench - update validate.sh"
  echo "[validate] sites here: $(ls "$BENCH_SITES" | grep -v '^assets$\|^apps\|^common_site' | tr '\n' ' ')"
  [ "${LIFECYCLE_EVENT:-}" = "ValidateService" ] && exit 1
  exit 0
fi

# Resolve the site to localhost and follow the http->https redirect so we hit the
# real serving path (nginx 301s http->https; the app answers 200 on https).
code=$(curl -sk -L -o /dev/null -w '%{http_code}' \
  --resolve "$SITE:80:127.0.0.1" --resolve "$SITE:443:127.0.0.1" \
  "http://$SITE/api/method/frappe.ping" || echo 000)
echo "[validate] $SITE -> HTTP $code"

# ValidateService must fail the deploy if the site is down; ApplicationStop should not.
if [ "${LIFECYCLE_EVENT:-}" = "ValidateService" ] && [ "$code" != "200" ]; then
  echo "[validate] site not healthy after deploy"; exit 1
fi
exit 0
