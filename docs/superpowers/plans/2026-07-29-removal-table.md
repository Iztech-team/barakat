# Removal table — persona least-privilege change

**Baseline:** `qa-test.test.barakat.iztech.net`, branch `test`, captured read-only on
2026-07-29 via `barakat/scripts/perm_audit.py`. No site was migrated or modified.

**Rule applied:** every permission a persona loses must map to a matrix cell that forbids
it. Anything unexplained is a bug in the new bundle, not an acceptable loss.

## Scale of the change

| Persona | Doctypes reachable before | After | Note |
|---|---|---|---|
| Manager | 296 | 57 | was carrying 11 native ERPNext role bundles |
| Branch Supervisor | 177 | 50 | |
| Accountant | 132 | 35 | |
| HR | 124 | 14 | |
| Inventory Keeper | 80 | 21 | |
| Cashier | 18 | 24 | rises: `select` added for pickers, self-service scoped reads |

The bulk of the drop is ~600 doctype grants across personas for ERPNext modules Barakat
does not use at all (Projects, Manufacturing, Assets, Quality, Support, Website…). Those
came from native roles and no AP or till surface touches them.

## Breaks found by this diff and fixed before shipping

The diff earned its place — it caught four classes of bug that no test would have shown
until a shop hit them.

| Found | Impact if shipped | Fix |
|---|---|---|
| Writer roles had no `submit` / `cancel` | `pos: write` could not submit a POS Invoice (**every sale**), `finance: write` could not post a Journal Entry (cash movements, day-closing), `suppliers: write` could not pay an invoice, `salary: write` could not submit a payslip | `SUBMITTABLE_DOCTYPES` in `persona_matrix.py` |
| Writer roles granted write on system-generated doctypes | Branch Supervisor, Inventory Keeper and Manager would gain create+write+**delete** on `Stock Ledger Entry`; Accountant and Manager the same on `GL Entry` | `READ_ONLY_DOCTYPES` clamps these to read |
| Cross-module form pickers had no read | Accountant's purchase-invoice form: item and warehouse pickers render **empty with no error** — the exact failure already hit twice before | `SHARED_PICKER_READS`, mirroring the proxy's `viewAny` |
| Report sub-keys missing their sales source | top-products and staff-performance reports read `POS Invoice`; without it Inventory Keeper and HR get an empty report | added to `reports.products` / `reports.staff` |
| `Price List` was picker-only under `accounting` | Accountant could not create or edit a price list, though the AP's price-list routes are `mutate('accounting')` | `Price List` is now owned by `accounting` |

## Remaining removals — all intentional

Grouped by the matrix cell that forbids them. `export` / `report` / `print` / `email` /
`share` are desk-only permissions the AP and till never use; they are dropped everywhere
and are not listed individually.

| Persona | Loses | Matrix cell |
|---|---|---|
| Accountant | `Company` write, `POS Profile` write | `settings: none`, `pos: read` |
| Accountant | `Pricing Rule`, `Item Price` write, `Item Group`/`Bin` read | `products: none` |
| Accountant | `Contact` write | `customers: read` |
| Accountant | `Stock Ledger Entry` read | `inventory: none` |
| Branch Supervisor | `Account`, `Sales Taxes and Charges Template` write | `accounting: read` |
| Branch Supervisor | `Journal Entry`, `Payment Entry` write+submit | `finance: read` |
| Branch Supervisor | `Purchase Invoice` write+submit | `suppliers: read` |
| Branch Supervisor | `Warehouse` write | `warehouses: read` |
| HR | `Employee`, `Designation`, `Holiday List`(+Assignment) write | `staff: read` — deliberate since 2026-07-22, only Manager creates logins |
| HR | `Branch` write | `branches: read` |
| HR | `Company` write | `settings: none` |
| HR | `Currency` read | `accounting: none` |
| Inventory Keeper | `Customer`, `Contact`, `Territory` read/write | `customers: none` |
| Inventory Keeper | `Currency`, `Currency Exchange`, `Fiscal Year` read | `accounting: none`, `finance: none` |
| Cashier | `Sales Invoice` read | `reports: none` — the Cashier's POS pages read `POS Invoice`, which it keeps |
| All | `Sales Invoice` write+submit | No AP route creates a Sales Invoice. The till writes `POS Invoice`; consolidation into a Sales Invoice is a scheduled system job running with `ignore_permissions`. **This is the one removal to confirm on the bench** — see below. |

## Open item for bench verification

`Sales Invoice` write+submit is removed from every persona, including Manager. The
reasoning above is sound but rests on reading the code, not on running it. Before this
ships, confirm on a bench that:

1. a POS sale still submits end to end, and
2. POS-invoice consolidation still produces a Sales Invoice.

If consolidation runs under the user's session rather than as a system job, Manager needs
`Sales Invoice` write+submit back — add it to `MODULE_DOCTYPES["pos"]`.
