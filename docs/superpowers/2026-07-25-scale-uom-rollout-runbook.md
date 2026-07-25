# Scale + UOM-Scoping Rollout Runbook (test → prod, petromall excluded)

Ships together: Piece 1 (UOM company-scoping, patch `scope_uom_company`) +
Piece 2 (scale config). The AP unit picker is EMPTY until Piece 1's patch
runs — never ship the AP/proxy without migrating barakat first.

For anything not spelled out here (SSH keys, exact server IPs, port rules,
"is it up" checks), the **barakat skill** (`~/.claude/skills/barakat/SKILL.md`)
is the source of truth — this runbook does not duplicate secrets or keys.

## 0. Preconditions
- [ ] All four repos' `dev` pushed (barakat, proxy, AP, electrobun).
- [ ] Versions bumped per the barakat versioning rules **BEFORE promoting**
  (AP `package.json`, proxy `package.json`, barakat `barakat/__init__.py`,
  POS `electrobun.config.ts` if it's being released this round). Bumping
  after promoting is the "ordering trap" — for AP/proxy, pushing IS
  deploying, so a late bump means prod briefly reports the wrong version.

## 1. TEST environment
1. Promote barakat `dev → test` (same two-command convention as the other
   three repos):
   ```bash
   git checkout test && git merge dev && git push
   ```
2. SSH to the **test EC2** (`52.59.253.35`, key `~/.ssh/barakat-test.pem`,
   user `ubuntu` — see the barakat skill's Servers table for the exact
   `ssh -i ...` command).
3. Pull as the `frappe` user (bench runs at `/home/frappe/erp_project`,
   remote is `upstream`, and the test box tracks the `test` branch):
   ```bash
   sudo -u frappe git -C apps/barakat pull upstream test
   ```
   Verify HEAD advanced to the promoted commit (`git log -1`).
4. Enumerate sites: `ls sites` (from `/home/frappe/erp_project`) — write
   the list here at run time.
5. For EACH site EXCEPT petromall:
   ```bash
   sudo -u frappe bench --site <site> migrate
   ```
   - Watch for the patch line: `scope_uom_company ... leftover_items=0`.
   - The patch skips petromall by name even if run (`SKIP_SITES`) — the
     site-list exclusion is belt one, `SKIP_SITES` is belt two.
6. This ships Python changes, so restart the bench once, after all sites
   are migrated:
   ```bash
   sudo -u frappe bench restart
   ```
   Without this, workers still running the old code return 417 "No module
   named ..." for anything the patch/new code touches.
7. Confirm what's live on the bench:
   ```bash
   sudo -u frappe bench version   # → barakat <version> test (<sha>)
   ```
   (Also visible in the desk's Installed Applications.)
8. Promote proxy `dev → test` (push **is** the deploy — no separate release
   step, no confirmation):
   ```bash
   git checkout test && git merge dev && git push
   ```
   Verify the deployed version: `GET /api/system/health` on the test proxy
   returns `version` (needs auth); the Swagger page shows it too.
9. Smoke each site:
   ```bash
   PROXY=<test proxy url> USR=<manager> PWD=<pwd> BRANCH="<branch>" node scripts/smoke-scale-settings.mjs
   ```
   All checks must pass before continuing.
10. Promote AP `dev → test` (push **is** the deploy here too):
    ```bash
    git checkout test && git merge dev && git push
    ```
    Open https://test.barakat.iztech.net/sign-in/ : Products → Units of
    Measure shows the site's units; Settings → Scale & Balances loads; a
    non-Manager cannot see either the page or the API (spot-check one
    Cashier login). Confirm the deployed version in the AP's sidebar
    footer (bottom-left of every page).
11. POS: promote to `test`, then build + release **from the `test`
    branch** (there is no dev build/release — those scripts exist but are
    unused):
    ```bash
    git checkout test && git merge dev && git push
    bun run build:test && bun run release:test
    ```
    On one till: Sync, check Settings → Device info shows the synced
    scale values; scan one weighed barcode on a Kg item. The installed
    app's own version (baked into the build from `electrobun.config.ts`)
    is what confirms the release landed.

## 2. PROD environment
Repeat 1-11 with: the **prod EC2** (`52.59.163.201`, key
`~/.ssh/barakat-prod.pem`), the prod box pulling `upstream main` (not
`test`), prod proxy/AP URLs (proxy health check still needs auth; AP is
https://console.barakat.iztech.net/), the prod site list from `ls sites`
(EXCLUDE petromall), and `bun run build:prod && bun run release:prod` for
the POS off the `main` branch. Do bm.iztech.net first (fresh customer,
lowest risk), then the remaining sites.

After the prod POS release lands, tag `main` (same convention as the other
three components):
```bash
git tag -a v<version> -m "POS v<version>" && git push origin v<version>
```
Tag `main` only, never `dev` or `test` — a tag means "this shipped to
prod." Do the equivalent tag for AP, proxy, and barakat if this rollout is
also their deploying push (bump-then-promote-then-tag, per the barakat
skill's versioning section).

## 3. Rollback
- Scale artifacts are additive (Company field + empty table): safe to leave
  in place; disable by simply not configuring branches.
- `scope_uom_company` is copy+repoint (originals are NOT deleted): rollback
  = repoint `Item.stock_uom` / `UOM Conversion Detail` / Item Prices back to
  the bare names and clear `Company.custom_scale_uom`. The bm dry run
  (38 units / 1021 items / 1694 prices) is the reference for expected scale.
- Proxy/AP: revert = push the previous commit to the same branch (push IS
  deploy). POS: the previous release remains installable from the release
  page/S3 bucket — no separate rollback build needed. barakat: `git -C
  apps/barakat pull upstream <branch>` to the reverted commit, re-migrate
  if the patch needs undoing, then `bench restart`.
