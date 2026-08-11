# POS cashier limits — design

**Date:** 2026-08-11
**Components:** barakat, proxy, AP, Electrobun POS
**Status:** approved, not implemented

## Why

The client does not want a cashier inventing prices or customers at the till. Three
concrete asks:

1. The register's "New item" button (F6) must be hideable — a cashier should not be able to
   type a name and a price and sell it.
2. Creating a customer must be hideable — from both places the POS offers it.
3. The cashier must not be able to type any discount they like; each till needs a maximum
   discount percentage.

All three are per-till settings, so all three live on the POS Profile. Both toggles are
**off** by default; the discount cap defaults to **100%** (unlimited).

Two further client notes — credit sales (شراء بالدين) and Visa / split-tender payment with
printable shift reports — are deliberately **out of scope here**. They are separate specs.

## Scope decisions taken during design

These were settled with the owner and are not open questions:

- **The add-item guard covers the F6 ad-hoc item dialog only.** The product picker, scanning
  and quantity edits are untouched. There is no manager-PIN override.
- **The discount cap covers the cashier's manual F3 order discount only.** Pricing Rules,
  bundle prices, loyalty redemption and gifted items are set by management or by the
  customer's own points, and are unaffected.
- **Enforcement is server-side wherever the architecture allows it**, not UI-only.
- **Existing POS Profiles are backfilled** to buttons-off / discount-100 (see Rollout).

## What was found in the code first

Two findings shaped the design and are recorded here so they are not re-derived.

**Role permissions cannot express this.** `barakat/persona_matrix.py` (the Cashier row)
records that the desktop POS runs under a **device session authenticated as Manager or
Branch Supervisor**, with the cashier identified only by a PIN. No till operation
authenticates as the Cashier's own user. Revoking `customers: write` from the Cashier role
changes nothing at the till; revoking it from Manager breaks the AP. Every guard here is
therefore keyed off the **POS Profile**, never off a role.

**Two of the three have an exact server-side signal, and one does not.**

| Guard | Signal visible to ERPNext | Strength |
|---|---|---|
| Ad-hoc item | Ad-hoc lines are pushed as `item_code: "MISC"` (`push-orders.ts:247`) | Exact |
| Discount | The F3 discount rides on the invoice header as `discount_amount` with `apply_discount_on: "Net Total"` (`push-orders.ts:388`) | Exact |
| Customer creation | The POS POSTs to `/api/resource/Customer` as Manager/Branch Supervisor — indistinguishable from an AP request unless the POS volunteers its identity | Guard, not a boundary |

The customer guard is therefore honest about its limit: the POS stamps the profile it is
running under, and the server enforces against that stamp. A client that lies about being a
till slips through. No cashier can do that, so it meets the client's need, but it is not a
security boundary and must not be described as one.

## Data model

### POS Profile custom fields

Added to `barakat/fixtures/custom_field.json`, in a new section after `custom_bank_account`.

| Fieldname | Type | Default (new docs) | Label |
|---|---|---|---|
| `custom_cashier_limits_section` | Section Break | — | Cashier Limits |
| `custom_allow_ad_hoc_item` | Check | `0` | Allow custom items |
| `custom_allow_customer_creation` | Check | `0` | Allow creating customers |
| `custom_max_discount_percent` | Percent | `100` | Maximum discount % |

`custom_max_discount_percent` is constrained to 0–100 by the AP and the proxy. `0` is a
legitimate, meaningful value: no discount at all.

### Customer custom field

| Fieldname | Type | Label |
|---|---|---|
| `custom_pos_profile` | Data, read-only | Created from POS profile |

**Data, not Link.** A Link would make a POS Profile undeletable once a till had created a
customer under it. `push-orders.ts` already documents that `LinkExistsError` pain for
Pricing Rules; there is no reason to repeat it. The field doubles as an audit trail of which
till created which customer.

## The pipeline

This is the path `custom_cash_account` already takes. Nothing new is invented.

```
barakat  fixtures/custom_field.json
   -> proxy   pos-profiles/types.ts + service.ts     (Create/Update bodies, mapper)
      -> AP      pos-profile-page.tsx                (2 checkboxes + 1 number, each with FieldHelp)
         -> POS     pull-pos-profile.ts              (3 fields into PosProfileData)
            -> POS     UI gates
   +  barakat  overrides/pos_invoice.py              (server enforcement, guards 1 and 3)
   +  barakat  hooks: Customer.before_insert         (server enforcement, guard 2)
   +  barakat  patches/backfill_...                  (existing profiles)
```

## Guard 1 — custom item

**Field:** `custom_allow_ad_hoc_item`

**POS.** When the flag is off, `RegisterActionBar` does not render the F6 tile and the F6
hotkey is not registered. The remaining two tiles are `flex-1`, so they widen to fill the
row — no layout work. `onNewItem` also early-returns, matching the existing `isPreviewMode`
guard in `register-page.tsx`.

**Server.** In `barakat/overrides/pos_invoice.py`, on validate: read the invoice's
`pos_profile`; if `custom_allow_ad_hoc_item` is falsy and any item row has
`item_code == "MISC"`, throw.

## Guard 2 — create customer

**Field:** `custom_allow_customer_creation`

**POS.** When the flag is off, the "Add customer" trigger is hidden in both places that
offer it — `features/customers/customers-page.tsx` and
`features/register/components/register-customer-dialog.tsx`. Both render the same
`AddCustomerForm`, so this is one flag hiding one trigger twice. There is no *edit* customer
path in the POS, so nothing else is affected.

`src/bun/sync/create-customer.ts` also refuses when the flag is off, mirroring the
defence-in-depth style already documented in that file for the name-length rules.

**Server.** `create-customer.ts` already calls `getErpnextConfig()` (which carries
`posProfile`) and already stamps `custom_company` on the payload. It additionally stamps
`custom_pos_profile`.

A `Customer.before_insert` hook then:

- field empty -> no-op. This is the AP path and it stays untouched.
- field set, profile's `custom_allow_customer_creation` truthy -> allow.
- field set, flag falsy -> throw.
- field set, profile does not exist -> **throw** (fail closed). A till that cannot name its
  own profile should not be creating customers.

## Guard 3 — max discount

**Field:** `custom_max_discount_percent`

**POS.** `RegisterDiscountDialog` gains a `maxPercent` prop:

- The percent tab clamps entry to `maxPercent`.
- The amount tab's existing `maxMajor` becomes `subtotal x maxPercent / 100`. The clamp
  machinery already exists (`discountAmountWithinMax`); it only gets a smaller ceiling.
- At `maxPercent === 0` the F3 tile is **hidden entirely**, like guard 1. A dialog that can
  only enter zero is worse than no dialog.

**Server.** In `pos_invoice.py`, on validate:

```
if total <= 0:            -> skip, nothing to take a percentage of
effective_pct = discount_amount / total * 100
throw if effective_pct > custom_max_discount_percent + TOLERANCE
```

`total` is the sum of line amounts before the header discount — the same base the POS dialog
uses, so the two agree exactly. Line `rate` already carries promo-effective pricing.

Two numeric details that are otherwise a source of false rejections:

- **`total <= 0` skips the check.** A zero-total invoice has no percentage to compute, and
  dividing would raise.
- **The comparison needs a tolerance**, not a bare `>`. The POS computes its discount as a
  rounded money amount and the server divides it back out, so an exactly-at-the-cap order
  can land at 10.0000001%. Compare with a tolerance of one unit at the `grand_total`
  precision, the same way ERPNext's own `validate_pos` compares paid amount to grand total.
  Without it, a cashier who applies exactly the maximum is rejected roughly at random.

Two exemptions:

- `is_return` — refunds.
- `grand_total == 0` — the rounding-collapse free order. `push-orders.ts` sets
  `freeOrder` when a real-value order rounds to zero (e.g. a 0.5 total) and discounts the
  full subtotal to make it consolidate cleanly at shift close. Without this exemption every
  such order would be rejected.

Gifted items need **no** exemption: they are per-line `discount_percentage: 100`, and never
touch the header `discount_amount`.

## AP surface

One new `PageSection`, "Cashier Limits", in `pos-profile-page.tsx` — the file backs both the
create and the edit route, so it is written once.

It holds two checkboxes and a 0–100 number input. Each of the three gets the existing
`<FieldHelp text={...} />` from `src/components/common/field-help.tsx`. That is the client's
requested "? sign", and the component's own doc comment says to use it in place of an inline
hint line. (The neighbouring `allowPartialPayment` field still uses an inline `<p>` hint;
it is not changed by this work.)

Create-form defaults: unchecked, unchecked, `100`.

When an admin flips a toggle from **on to off** on an existing profile, an inline note warns
that queued offline orders relying on it will be rejected on sync — see Accepted risk.

Labels and help text in ar / en / he. A release-note entry for the AP version bump, in all
three languages, per the standing rule.

## Proxy

`allowAdHocItem`, `allowCustomerCreation` and `maxDiscountPercent` are added to
`CreatePosProfileBody` and `UpdatePosProfileBody` in `src/modules/pos-profiles/types.ts`,
with `maxDiscountPercent` bounded 0–100, and to the mapper in `service.ts` in both
directions.

**Elysia validates responses.** A field added to a GET response must also be declared in
that route's `response` schema, or it is silently stripped before the AP sees it.

## Rollout

**Existing POS Profiles carry none of these fields and would read `0`** — meaning both
buttons vanish (intended) *and* a max discount of `0` would make `pos_invoice.py` reject
every discounted invoice at every live shop. That is the failure mode that took prod down on
2026-07-28.

A patch, `barakat/patches/backfill_pos_profile_cashier_limits.py`, therefore sets on every
existing POS Profile:

| Field | Backfilled to |
|---|---|
| `custom_allow_ad_hoc_item` | `0` |
| `custom_allow_customer_creation` | `0` |
| `custom_max_discount_percent` | `100` |

The patch is **idempotent**: it writes only where the value is `NULL`, so re-running never
stomps a deliberate `0`. It is site-scoped; prod migration excludes `petromall` as always.

**Stale cached profiles.** A POS that upgrades before its next profile refresh reads a
cached `PosProfileData` written without these fields. The defaults applied on read mirror
the backfill exactly — buttons off, discount 100 — so a stale cache and a fresh pull behave
identically. Toggles take effect at the next profile refresh, not instantly.

**Ship order is forced by the dependency:** barakat -> proxy -> AP -> POS. The fields must
exist before anything writes them.

Versions: barakat `4.8.0`, proxy `6.4.0`, AP `1.32.0` (+ release note), POS `2.11.0`. All
minor — new fields, new settings, no breaking change to an existing shape.

## Accepted risk

A till that has been offline with unsynced ad-hoc or discounted orders queued, whose profile
then has a toggle flipped off, will have those orders rejected on sync. They land in history
as `failed` and stay retriable — re-enabling the toggle drains them. The AP warns when a
toggle is turned off. This is named rather than hidden; the alternative (grandfathering by
timestamp) needs per-flag history the POS Profile does not have.

## Verification

On the local QA environment (`barakat-local`).

**The test that matters: use a POS Profile that existed before the fixture landed.** A
profile created afterwards picks up the field defaults and will pass while every live shop
fails. This is the exact shape of the 2026-07-28 prod outage.

1. Pre-existing profile, post-patch — sells normally, discounts normally, both buttons
   hidden.
2. Toggle each flag on in the AP — the corresponding button reappears after a profile
   refresh.
3. `custom_max_discount_percent = 10` — the dialog refuses 11%; an invoice hand-crafted with
   a 20% header discount is rejected by `pos_invoice.py`.
4. `custom_max_discount_percent = 0` — the F3 tile is gone.
5. With the cap at 10: a refund still posts, and a 0.5-total rounding-collapse order still
   posts.
6. Creating a customer from the AP still works while the flag is off on every profile.
7. With `custom_allow_ad_hoc_item` off, an invoice hand-crafted with a `MISC` line is
   rejected.
8. **Exactly at the cap passes.** With the cap at 10, an order discounted exactly 10% must
   post — run it on a subtotal that does not divide cleanly (e.g. 33.33) to exercise the
   tolerance.
9. Unit tests: the effective-percentage calculation, the `total <= 0` skip, the tolerance,
   and both exemptions; the `before_insert` hook's four branches.

## Out of scope

- Credit sales / شراء بالدين with a per-customer limit. Needs an ERPNext research spike
  first: `customer.py` (`credit_limits`, `check_credit_limit`) and whether a POS Invoice can
  carry an outstanding balance at all given `validate_full_payment`.
- Visa and split-tender payment, and the printable cash-inspection and shift-close reports.
  The POS today hardcodes `submitOrder({ modeOfPayment: "Cash" })`, pulls only one
  `defaultPaymentMode`, and `getShiftCashSummary` filters out every non-cash payment — that
  is a new payment screen plus a reporting rework, not an extension of this work.
