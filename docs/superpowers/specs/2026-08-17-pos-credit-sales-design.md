# الدفع بالدين — credit sales with a per-customer limit

**Date:** 2026-08-17
**Repos:** barakat (Frappe app), proxy, AP, Electrobun POS
**Status:** approved, ready to plan

## What the client asked for

A cashier can sell to a known customer without taking the money now ("شراء بالدين"),
up to a limit set for that customer. Repayment (تسديد) is explicitly **out of scope** for
this spec — the debt is created and tracked here; collecting it happens in the ERPNext desk
until we spec a collection surface.

## What ERPNext already gives us

Read from the source on the local bench (`erpnext` on `frappe-bench`), not from memory:

1. **Partial payment is supported.** `POS Profile.allow_partial_payment` gates it
   (`sales_invoice.py::validate_full_payment`). Our proxy already hardcodes it to `1` on
   every profile it creates *and* updates, so no migration is needed for profiles the AP
   manages. A POS Invoice whose `paid_amount` is below its total stores the shortfall in
   `outstanding_amount`.
2. **A per-customer credit limit exists**: the `Customer.credit_limits` child table
   (`Customer Credit Limit`), keyed per company, with a `bypass_credit_limit_check` flag.
   `get_credit_limit()` falls back Customer → Customer Group → `Company.credit_limit`.
3. **The debt books itself.** At shift close the merge log groups POS invoices by customer
   (`get_invoice_customer_map`), so an unpaid balance lands in Accounts Receivable against
   that customer with no work from us.

## What ERPNext does NOT give us

4. **A POS credit sale is never credit-checked.** `POS Invoice.on_submit` replaces
   `SalesInvoice.on_submit` wholesale and never calls `check_credit_limit`. Any limit at the
   till is ours to enforce. (Same shape as the loyalty-redemption gap: ERPNext does not
   validate a POS Invoice's redemption either.)

## The finding that decides the design

`check_credit_limit` → `get_customer_outstanding` is **GL-based**:
`sum(debit) - sum(credit)` over `tabGL Entry` for that customer.

**A POS Invoice posts no GL entries until it is consolidated.** `POS Invoice.on_submit`
contains no `make_gl_entries` call; the GL is written when the merge log builds the
consolidated Sales Invoice at shift close.

Therefore a customer's debt lives in three places, and only the first is visible to
ERPNext's own check:

| Term | Where it lives | Visible to `get_customer_outstanding`? |
|---|---|---|
| **Consolidated** | GL entries | yes |
| **Pushed, not consolidated** | `POS Invoice` rows with `outstanding_amount > 0` and `consolidated_invoice IS NULL` | **no** |
| **Not pushed** | this till's local order queue | no — only this till knows |

### The consequence: a shift-close bomb

A customer with a ₪500 limit can take ₪500 of credit ten times in one shift. Every check
passes, because none of it has reached the GL. At shift close, consolidation posts the lot,
`SalesInvoice.on_submit` calls `check_credit_limit` (`sales_invoice.py:498`), the customer is
₪4,500 over, and **consolidation throws — jamming the whole shift**, with an error nobody can
trace back to a sale from hours earlier.

This only bites shops that set credit limits, i.e. exactly the shops using this feature.

So the question the till must answer is never "what does ERPNext say he owes" but
**"what will he owe once everything in flight has landed"** — all three terms.

## Architecture

Three layers, mirroring the cashier-limits feature shipped 2026-08-11.

| Layer | Responsibility | Why here |
|---|---|---|
| **Till (POS)** | Offer or refuse دين; show balance and headroom; live re-check at pay time | UX. Fast, and explains itself before the cashier promises the customer anything |
| **`POS Invoice.validate` (barakat)** | **The authority.** Recompute all three terms server-side and refuse | The only place that sees every till |
| **ERPNext consolidation** | `check_credit_limit` on the merged Sales Invoice | Already exists. Our job is to guarantee it never fires |

The server layer is what makes offline safe: a sale approved against a stale balance is
**rejected when the till pushes it**, landing in the failed-orders queue the cashier already
understands — instead of detonating at shift close.

## Data model

Nothing invented.

- **The limit** — ERPNext's native `Customer.credit_limits` for the selling company.
  **Opt-in: no row, or a limit of 0, means no credit.** This deliberately diverges from
  ERPNext, where `get_credit_limit` returning 0 disables the check entirely; that default
  would let a shop that enables credit and forgets to set limits accrue unbounded debt.
- **The per-till gate** — one new custom field `custom_allow_credit_sale` (Check, default 0)
  in the existing `custom_cashier_limits_section` on POS Profile, beside
  `custom_allow_ad_hoc_item`, `custom_allow_customer_creation` and
  `custom_max_discount_percent`.
- **The debt** — the POS Invoice's own `outstanding_amount`, produced by sending
  `paid_amount` = the tenders only. No new doctype and no ledger of ours.

### دين is not a Mode of Payment

If the debt were recorded as a payment row, `paid_amount` would cover the total,
`outstanding_amount` would be zero, and **no debt would ever reach Accounts Receivable**.
The debt *is* the gap between the total and what was tendered. On screen and on paper it may
sit beside the tenders; in the data it must never be one.

## The rule

A POS Invoice with `outstanding_amount > 0` is accepted only when **all** hold:

1. The selling POS Profile has `custom_allow_credit_sale` set.
2. The customer is not the profile's default/walk-in customer — debt must attach to a person.
3. A credit limit > 0 exists for (customer, company).
4. `consolidated + unconsolidated + this invoice's outstanding <= limit`, compared at the
   currency's precision with the same half-up rounding `cashier_limits.py` uses.

Returns (`is_return`) are exempt: a credit note reduces debt.

## Components

### barakat (Frappe app)

- `barakat/credit_limits.py` — **pure, Frappe-free**, mirroring `cashier_limits.py`:
  headroom arithmetic, the over-limit predicate, rounding. Unit-tested without a bench.
- `barakat/overrides/pos_invoice.py` — `validate_credit_sale()`, called from the existing
  `validate()` beside `validate_cashier_limits()`. Reads the three terms and applies the rule.
- `barakat/fixtures/custom_field.json` — the new POS Profile field, plus its name added to
  the `fixtures` export allowlist in `hooks.py`.

### proxy

- POS Profile: carry `allowCreditSale` through `mapProfile`, `PROFILE_FIELDS`, the create and
  update payloads, and the response schemas (Elysia strips undeclared fields).
- Customers: read and write the per-company credit limit, and report the customer's current
  debt (consolidated + unconsolidated) so the AP and the till can show it.

### Electrobun POS

- Pull `allowCreditSale` with the rest of the profile; expose it to the renderer beside the
  other cashier limits.
- A pay-time RPC that asks the proxy for the customer's live debt and headroom.
- Pay dialog: a دين action on the next free F-key meaning *leave the rest unpaid*, enabled
  only when the profile allows it, a named customer is selected, the till is online, and the
  re-check returns headroom.
- `push-orders`: send `paid_amount` = tenders only, so ERPNext derives the outstanding.
- Receipt: `الدين` for this sale and `الرصيد الإجمالي` for the new balance, as their own
  lines — separate from the tender rows, so the invariant that the tender rows sum to
  المبلغ المدفوع survives.

### AP

- POS Profile page: the toggle in the Cashier Limits section, with the same `?` help.
- Customer: set the per-company credit limit, and show the current balance.

## Error handling

- **Offline, or the re-check fails or times out** → دين is disabled and says why. Never
  assume headroom. The sale still completes for cash.
- **Two tills racing** → the server check refuses the loser; the order fails with a legible
  reason and the cashier takes cash.
- **Refund of a credit sale** — a return reduces the outstanding first and pays out cash only
  for what was actually paid. The current refund flow collapses every tender to cash, so
  without this a customer could return goods they never paid for and be handed cash. In scope.
- **Default/walk-in customer** → دين never offered.
- **Shift close** — credit puts nothing in the drawer, and `getShiftCashSummary` already sums
  only cash-typed payments, so the expected balance is right with no change.

## Testing

- **Pure module**: headroom, boundaries (exactly at the limit accepted, a penny over
  refused), rounding, zero and missing limits.
- **Server**: over-limit refused; at-limit accepted; **unconsolidated POS invoices counted**;
  customer with no limit refused; default customer refused; profile toggle off refused;
  returns exempt.
- **Till**: tender maths with a credit remainder; dialog gating; offline behaviour; the
  receipt lines.
- **End-to-end on local QA** — the test that proves the bomb is defused: two credit sales for
  the same customer in one shift, then close the shift and confirm consolidation succeeds and
  the debt is in Accounts Receivable.

## Out of scope

Repayment (تسديد), interest, due dates, ageing, and statements.
