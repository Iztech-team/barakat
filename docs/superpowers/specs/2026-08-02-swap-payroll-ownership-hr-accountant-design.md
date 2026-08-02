# Swap payroll ownership: Accountant runs it, HR watches

**Date:** 2026-08-02
**Repos:** `proxy-barakat`, `barakat`, `admin_panel_barakat`

## The change

Two cells of the persona matrix trade places:

| Persona | `salary` before | `salary` after |
| --- | --- | --- |
| HR | `write` | `read` |
| Accountant | `read` | `write` |

Nothing else moves. `reports.salary` is already `read` for both personas and stays that way.

## Why it is not a cosmetic edit

`salary: write` is the grant behind the `Barakat Salary Writer` role, which carries
create, write, delete, submit and cancel on the five doctypes `MODULE_DOCTYPES["salary"]`
names: Salary Slip, Salary Structure, Salary Component, Salary Structure Assignment and
Payroll Settings. Submit is what actually issues a payslip — `SUBMITTABLE_DOCTYPES`
exists precisely because a writer without it saves drafts and fails at the last step.

So after this change the Accountant issues and submits payslips and defines salary
structures, and HR can open a payslip but not create, edit or submit one. HR keeps
`attendance: write`, so it still records the attendance that payroll is computed from —
it just no longer turns that attendance into money.

This is the owner's explicit decision, confirmed 2026-08-02.

## Where the matrix lives

The matrix is one source of truth mirrored in four files across two repos, each pair
guarded by a snapshot test. All four must move together or a test goes red.

| File | Repo | Role |
| --- | --- | --- |
| `src/modules/roles/catalog.ts` | proxy | The TypeScript matrix the proxy enforces |
| `src/modules/roles/persona-matrix.json` | proxy | Snapshot, checked by `matrix-snapshot.test.ts` |
| `barakat/persona_matrix.py` | barakat | The Python twin that derives ERPNext DocPerms |
| `barakat/persona_matrix.json` | barakat | Snapshot, checked by `test_persona_guard.py` |

### What needs no change

- **The admin panel.** `role-matrix-dialog.tsx` renders `role.modules` straight from the
  API, and the sidebar and route guards read the same map off `GET /api/session`. The
  dialog in the screenshot updates itself the moment the proxy ships.
- **The ERPNext role bundles.** `_build_persona_bundles()` in `barakat/permissions.py`
  transcribes each matrix row mechanically, so HR's `Barakat Salary Writer` becomes
  `Barakat Salary Reader` and the Accountant's the reverse with no hand-editing. Both
  roles already exist and already carry the right DocPerms — the Manager holds the
  Writer today and the Accountant holds the Reader. No role is created and no DocPerm
  is altered; only which persona holds which.
- **`EXTRA_ROLES`.** HR keeps `Barakat Attendance Manager` (Attendance submit/cancel) and
  `Barakat Self Service`. Neither touches payroll authoring.
- **Any persona special-case.** There are none. The only `HR` / `Accountant` string
  literals outside the matrix are Swagger tags and the bundle keys.

## A pre-existing failure in the way

`barakat/persona_matrix.json` is stale on **all three branches, including `main`**: it
records Cashier `pos: "read"` while `persona_matrix.py` records `"none"`. The Cashier
change of 2026-07-30 updated three of the four mirrors and missed this one, so
`test_matrix_matches_the_json_snapshot` is currently red in production.

It is unrelated to this work but sits in a file this work edits, and the snapshot test
cannot prove the payroll swap correct while it is failing for another reason. It is
corrected here, in its own commit, ahead of the swap. The correct value is not in doubt:
the Python matrix and both proxy mirrors already agree on `"none"`.

## Versions

The versioning rule lists *"a role's meaning changes"* as a major bump, which is a
literal description of this change.

| Component | From | To | Why |
| --- | --- | --- | --- |
| proxy | `5.0.1` | `6.0.0` | Two personas change what they may do |
| barakat | `3.3.0` | `4.0.0` | Same, at the ERPNext layer |
| AP | `1.25.2` | `1.25.3` | No code change; patch bump exists to carry the release note |

The AP bump is deliberate and is the one place this rule is stretched. Nothing in the AP
changes, but the people affected — an HR user who opens the payroll page tomorrow and
finds the buttons gone — learn about it nowhere else. The release note is the
announcement, in all three languages, written for a shop operator:

- payroll is now issued by the Accountant
- HR can still record attendance and view payslips

## Deployment

Standard promotion for all three, with one step that must not be skipped.

**barakat requires `bench migrate`, not merely `bench restart`.** `after_migrate` runs
`_backfill_persona_roles`, which re-stamps the ERPNext roles of *existing* staff. Without
it, an HR user created before this change keeps `Barakat Salary Writer` on their account:
the admin panel would hide payroll from them while `/api/resource/Salary Slip` still let
them submit one. A half-applied permission change is the failure mode this whole matrix
exists to prevent.

Enumerate sites explicitly and exclude `petromall.iztech.net`, which is not a Barakat
site despite sharing the prod bench.

## Verification

| Check | How |
| --- | --- |
| Proxy matrix and snapshot agree | `bun test src/modules/roles/` |
| Proxy typechecks | `bunx tsc --noEmit` |
| Python matrix and snapshot agree | `PersonaMatrixData` is plain `unittest`, so it runs standalone |
| Bundles match the matrix rows, both directions | `barakat.test_persona_matches_matrix` on the local QA bench |
| Every persona can still log in | Same module — guards that each bundle keeps a `desk_access` role |
| The dialog actually swapped | Open the role matrix in the local AP and read the two chips |

The two-directional bundle test matters more than usual here. Too wide means HR keeps a
payroll grant it should have lost; too narrow means the Accountant gets `salary: write`
with no reachable write permission, which fails silently — the AP renders a dead button
rather than an error.
