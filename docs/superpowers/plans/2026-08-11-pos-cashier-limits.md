# POS Cashier Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each POS Profile three per-till settings — allow custom items, allow creating customers, and a maximum discount percentage — enforced in the POS UI and, where the architecture allows it, on the ERPNext server.

**Architecture:** Three custom fields on POS Profile flow down the pipeline `custom_cash_account` already uses: barakat fixtures -> proxy -> AP form -> POS profile pull -> POS UI gates. Two of the three also get a server-side gate in `barakat/overrides/pos_invoice.py`, keyed off the invoice's `pos_profile`. The third (customer creation) is gated by a `Customer.before_insert` hook reading a `custom_pos_profile` stamp the POS puts on the payload. All the decision logic lives in one pure, Frappe-free module so it is unit-testable on the Windows dev box.

**Tech Stack:** Frappe/ERPNext (Python), Elysia + Bun (proxy), React + TanStack Query + react-i18next (AP and POS renderer), Bun (POS main process), SQLite (POS local store).

**Spec:** `docs/superpowers/specs/2026-08-11-pos-cashier-limits-design.md` in this repo.

## Global Constraints

- **Repos are worktrees under `C:\Users\IzTech-OTbaileh\Desktop\barakat-qa\`.** Work only in the `-dev` folders (`barakat-dev`, `proxy-barakat-dev`, `admin_panel_barakat-dev`, `electrobun-pos-dev`), which are all on branch `dev`. Never check out a different branch in a plain `<repo>/` folder — that is QA's checkout.
- **Commit only. Do not push, do not promote, do not deploy.** Pushing the AP or the proxy deploys them.
- **Ship order is forced by the dependency:** barakat -> proxy -> AP -> POS. Do the tasks in numeric order.
- **Version bumps, in the same commit as the work.** Values below were read off `dev` on 2026-08-11. `dev` runs ahead of the last shipped tags, so **re-read each file before bumping** rather than trusting this table or the barakat skill's.

  | Component | File | On `dev` now | Bump to |
  |---|---|---|---|
  | barakat | `barakat/__init__.py` | `4.8.0` | `4.9.0` |
  | proxy | `package.json` | `6.3.2` | `6.4.0` |
  | AP | `package.json` | `1.31.2` | `1.32.0` |
  | POS | `electrobun.config.ts` | `2.14.0` | `2.15.0` |

  All minor: new fields and new settings, no breaking change to an existing shape.
- **Field names, exact:** `custom_allow_ad_hoc_item`, `custom_allow_customer_creation`, `custom_max_discount_percent` (all on POS Profile); `custom_pos_profile` (on Customer).
- **Defaults for new documents:** both Checks `0`, `custom_max_discount_percent` `100`.
- **Backfill for existing documents:** both Checks `0`, `custom_max_discount_percent` `100`.
- **The AP's `.husky/pre-commit` typechecks the whole project.** A commit that does not typecheck will not land. Budget time for it.
- **Elysia validates responses.** A field added to a proxy response must also be declared in that route's `response` schema, or it is silently stripped before the AP sees it.
- **Do not run `prettier --write` across either TS repo.** A handful of files are CRLF and a repo-wide format rewrites every line of them. Format only the files you touched.
- **Never operate on the `petromall` site.**

---

### Task 1: Custom fields in the barakat fixtures

Adds the four custom fields. Nothing reads them yet — this task exists on its own because every later task depends on the exact field names, and the fixture has a pure test that can prove them on Windows.

**Files:**
- Modify: `barakat/fixtures/custom_field.json`
- Test: `barakat/test_custom_fields.py`

**Interfaces:**
- Consumes: nothing.
- Produces: POS Profile fields `custom_cashier_limits_section` (Section Break), `custom_allow_ad_hoc_item` (Check), `custom_allow_customer_creation` (Check), `custom_max_discount_percent` (Percent); Customer field `custom_pos_profile` (Data, read-only).

- [ ] **Step 1: Write the failing test**

Append these two test classes to `barakat/test_custom_fields.py`, after the existing classes:

```python
class CashierLimitFieldsAreDeclared(unittest.TestCase):
    def setUp(self):
        self.rows = _rows()

    def test_section_break_exists_after_bank_account(self):
        f = _by_name(self.rows, "POS Profile-custom_cashier_limits_section")
        self.assertIsNotNone(f, "cashier limits section missing from fixtures")
        self.assertEqual(f["fieldtype"], "Section Break")
        self.assertEqual(f["insert_after"], "custom_bank_account")

    def test_both_toggles_are_checks_defaulting_off(self):
        for fieldname in ("custom_allow_ad_hoc_item", "custom_allow_customer_creation"):
            f = _by_name(self.rows, f"POS Profile-{fieldname}")
            self.assertIsNotNone(f, f"{fieldname} missing from fixtures")
            self.assertEqual(f["fieldtype"], "Check", fieldname)
            # A Check with no default is 0 anyway, but state it so the intent
            # survives an edit: the client asked for these OFF by default.
            self.assertEqual(f.get("default", "0"), "0", fieldname)

    def test_max_discount_is_percent_defaulting_to_100(self):
        f = _by_name(self.rows, "POS Profile-custom_max_discount_percent")
        self.assertIsNotNone(f, "custom_max_discount_percent missing from fixtures")
        self.assertEqual(f["fieldtype"], "Percent")
        # 100, never 0. A 0 default would make pos_invoice.py reject every
        # discounted sale at every shop the moment this ships.
        self.assertEqual(f["default"], "100")


class CustomerPosProfileStamp(unittest.TestCase):
    def setUp(self):
        self.rows = _rows()

    def test_stamp_is_data_not_link(self):
        f = _by_name(self.rows, "Customer-custom_pos_profile")
        self.assertIsNotNone(f, "Customer-custom_pos_profile missing from fixtures")
        # Data, not Link: a Link would make a POS Profile undeletable once a till
        # had created a customer under it (LinkExistsError).
        self.assertEqual(f["fieldtype"], "Data")
        self.assertEqual(f.get("read_only"), 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `C:\Users\IzTech-OTbaileh\Desktop\barakat-qa\barakat-dev`:

```bash
python -m unittest barakat.test_custom_fields -v
```

Expected: the four new tests FAIL with `AssertionError: ... missing from fixtures`. The pre-existing tests must still PASS.

- [ ] **Step 3: Add the fixtures**

In `barakat/fixtures/custom_field.json`, insert these five objects immediately after the `POS Profile-custom_bank_account` object. Match the surrounding file's formatting exactly (tabs, key order).

```json
{
 "doctype": "Custom Field",
 "name": "POS Profile-custom_cashier_limits_section",
 "dt": "POS Profile",
 "fieldname": "custom_cashier_limits_section",
 "label": "Cashier Limits",
 "fieldtype": "Section Break",
 "insert_after": "custom_bank_account"
},
{
 "doctype": "Custom Field",
 "name": "POS Profile-custom_allow_ad_hoc_item",
 "dt": "POS Profile",
 "fieldname": "custom_allow_ad_hoc_item",
 "label": "Allow custom items",
 "description": "Let the cashier add an item that is not in the catalogue, typing its name and price. Off by default.",
 "fieldtype": "Check",
 "default": "0",
 "insert_after": "custom_cashier_limits_section"
},
{
 "doctype": "Custom Field",
 "name": "POS Profile-custom_allow_customer_creation",
 "dt": "POS Profile",
 "fieldname": "custom_allow_customer_creation",
 "label": "Allow creating customers",
 "description": "Let the cashier create a new customer from the till. Off by default.",
 "fieldtype": "Check",
 "default": "0",
 "insert_after": "custom_allow_ad_hoc_item"
},
{
 "doctype": "Custom Field",
 "name": "POS Profile-custom_max_discount_percent",
 "dt": "POS Profile",
 "fieldname": "custom_max_discount_percent",
 "label": "Maximum discount %",
 "description": "The largest order discount the cashier may apply. 100 means no limit; 0 disables the discount button entirely.",
 "fieldtype": "Percent",
 "default": "100",
 "insert_after": "custom_allow_customer_creation"
},
{
 "doctype": "Custom Field",
 "name": "Customer-custom_pos_profile",
 "dt": "Customer",
 "fieldname": "custom_pos_profile",
 "label": "Created from POS profile",
 "description": "Set by the till when a cashier creates this customer. Blank for customers created in the admin panel.",
 "fieldtype": "Data",
 "read_only": 1,
 "insert_after": "custom_company"
}
```

Note: `Customer-custom_pos_profile` is a `Data` field whose name contains neither "company" nor "branch", so it does not trip the pre-existing `test_no_company_or_branch_marker_is_data` guard. Do not rename it to include either word.

- [ ] **Step 4: Run the test to verify it passes**

```bash
python -m unittest barakat.test_custom_fields -v
```

Expected: all tests PASS, old and new.

- [ ] **Step 5: Verify the JSON is still valid and the file did not get reordered**

```bash
python -c "import json;d=json.load(open('barakat/fixtures/custom_field.json'));print(len(d),'fields');print([f['fieldname'] for f in d if f['dt']=='POS Profile'])"
```

Expected: the POS Profile list ends with `custom_bank_account`, `custom_cashier_limits_section`, `custom_allow_ad_hoc_item`, `custom_allow_customer_creation`, `custom_max_discount_percent`.

- [ ] **Step 6: Commit**

```bash
git add barakat/fixtures/custom_field.json barakat/test_custom_fields.py
git commit -m "feat(pos-profile): add the three cashier-limit fields and the customer stamp"
```

---

### Task 2: The pure decision module

All three guards' logic, with no `frappe` import, so it can be tested on Windows. The Frappe hooks in Tasks 3 and 4 are thin wrappers over this.

**Files:**
- Create: `barakat/cashier_limits.py`
- Create: `barakat/test_cashier_limits.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MAX_DISCOUNT_UNLIMITED: float` (= `100.0`)
  - `discount_over_cap(discount_amount: float, total: float, max_percent: float, precision: int = 2) -> bool`
  - `has_ad_hoc_line(item_codes: Iterable[str]) -> bool`
  - `AD_HOC_ITEM_CODE: str` (= `"MISC"`)

- [ ] **Step 1: Write the failing test**

Create `barakat/test_cashier_limits.py`:

```python
"""Pure, Frappe-free tests for the cashier-limit decisions.

Runs locally:  python -m unittest barakat.test_cashier_limits

Every rule the POS Profile's cashier limits enforce is decided here, so the
Frappe hooks stay thin and the arithmetic is provable without a bench.
"""

import unittest

from barakat.cashier_limits import (
    AD_HOC_ITEM_CODE,
    MAX_DISCOUNT_UNLIMITED,
    discount_over_cap,
    has_ad_hoc_line,
)


class DiscountCap(unittest.TestCase):
    def test_under_the_cap_is_allowed(self):
        # 5 off 100 is 5%, cap is 10%.
        self.assertFalse(discount_over_cap(5.0, 100.0, 10.0))

    def test_over_the_cap_is_rejected(self):
        # 20 off 100 is 20%, cap is 10%.
        self.assertTrue(discount_over_cap(20.0, 100.0, 10.0))

    def test_exactly_at_the_cap_is_allowed(self):
        self.assertFalse(discount_over_cap(10.0, 100.0, 10.0))

    def test_exactly_at_the_cap_survives_rounding(self):
        # 10% of 33.33 is 3.333, which the POS rounds to 3.33 before sending.
        # A bare `>` comparison rejects this; the tolerance must not.
        self.assertFalse(discount_over_cap(3.33, 33.33, 10.0))
        # And the other side: rounding UP must still pass.
        self.assertFalse(discount_over_cap(3.34, 33.33, 10.0))

    def test_a_cent_over_the_cap_is_still_rejected(self):
        # The tolerance is one tenth of the smallest unit, so 10 agorot over
        # must not slip through as "rounding".
        self.assertTrue(discount_over_cap(3.44, 33.33, 10.0))

    def test_unlimited_cap_allows_a_full_discount(self):
        self.assertFalse(discount_over_cap(100.0, 100.0, MAX_DISCOUNT_UNLIMITED))

    def test_zero_cap_rejects_any_discount(self):
        self.assertTrue(discount_over_cap(0.01, 100.0, 0.0))

    def test_zero_cap_allows_no_discount(self):
        self.assertFalse(discount_over_cap(0.0, 100.0, 0.0))

    def test_zero_total_is_skipped(self):
        # Nothing to take a percentage of, and dividing would raise.
        self.assertFalse(discount_over_cap(0.0, 0.0, 10.0))
        self.assertFalse(discount_over_cap(5.0, 0.0, 10.0))

    def test_negative_total_is_skipped(self):
        self.assertFalse(discount_over_cap(5.0, -100.0, 10.0))

    def test_none_cap_is_treated_as_unlimited(self):
        # An un-backfilled profile reads None. It must NOT read as zero.
        self.assertFalse(discount_over_cap(50.0, 100.0, None))


class AdHocLines(unittest.TestCase):
    def test_detects_a_misc_line(self):
        self.assertTrue(has_ad_hoc_line(["APPLE", AD_HOC_ITEM_CODE]))

    def test_a_normal_cart_has_none(self):
        self.assertFalse(has_ad_hoc_line(["APPLE", "BREAD"]))

    def test_empty_cart_has_none(self):
        self.assertFalse(has_ad_hoc_line([]))

    def test_ignores_case_and_padding(self):
        # ERPNext item codes are stored as sent; be liberal about what arrives
        # rather than let a stray space defeat the guard.
        self.assertTrue(has_ad_hoc_line([" misc "]))

    def test_tolerates_none_entries(self):
        self.assertFalse(has_ad_hoc_line([None, "APPLE"]))
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m unittest barakat.test_cashier_limits -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'barakat.cashier_limits'`.

- [ ] **Step 3: Write the implementation**

Create `barakat/cashier_limits.py`:

```python
"""The cashier-limit decisions, with no Frappe dependency.

Three per-till settings live on the POS Profile — see
docs/superpowers/specs/2026-08-11-pos-cashier-limits-design.md. The rules they
imply are decided HERE so they can be tested without a bench, and so the two
call sites (the POS Invoice validate hook and the Customer before_insert hook)
cannot drift apart.
"""

AD_HOC_ITEM_CODE = "MISC"

# The value that means "no limit". Also the value the backfill patch writes to
# every profile that predates this feature — a 0 default would reject every
# discounted sale at every live shop.
MAX_DISCOUNT_UNLIMITED = 100.0


def has_ad_hoc_line(item_codes):
    """Does this cart contain a cashier-invented item?

    The POS pushes an ad-hoc line — one the cashier typed a name and price for —
    as item_code "MISC" (see push-orders.ts). Matching is case- and
    padding-insensitive so a stray space cannot defeat the guard.
    """
    target = AD_HOC_ITEM_CODE.casefold()
    for code in item_codes or []:
        if code is None:
            continue
        if str(code).strip().casefold() == target:
            return True
    return False


def discount_over_cap(discount_amount, total, max_percent, precision=2):
    """Does this order-level discount exceed the profile's cap?

    Compared in MONEY space rather than percentage space: the POS computes the
    discount as a rounded currency amount, so dividing it back into a percentage
    reintroduces the rounding error we then have to forgive. Comparing
    `discount_amount` against `total * max_percent / 100` needs only one
    tolerance, and it is the same shape as ERPNext's own paid-amount check in
    `SalesInvoice.validate_pos`.

    `max_percent` of None reads as unlimited, never as zero — an un-backfilled
    profile must not silently block every discount.
    """
    total = float(total or 0.0)
    # Nothing to take a percentage of. Belt and braces: the money-space
    # comparison below would also handle it, but a zero-total invoice is an
    # exemption in the spec and stating it here keeps the two aligned.
    if total <= 0:
        return False

    cap = MAX_DISCOUNT_UNLIMITED if max_percent is None else float(max_percent)
    allowed = total * cap / 100.0

    # One tenth of the smallest representable unit. Without it an order
    # discounted EXACTLY at the cap is rejected whenever the percentage does not
    # divide cleanly (10% of 33.33 is 3.333, sent as 3.33).
    tolerance = 1.0 / (10 ** (precision + 1))
    return float(discount_amount or 0.0) - allowed > tolerance
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python -m unittest barakat.test_cashier_limits -v
```

Expected: all 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add barakat/cashier_limits.py barakat/test_cashier_limits.py
git commit -m "feat(cashier-limits): pure, bench-free module for the three guard decisions"
```

---

### Task 3: Server enforcement on the POS Invoice

Wires Task 2's decisions into `BarakatPOSInvoice.validate` for the two guards ERPNext can see exactly: ad-hoc lines and the order discount.

**Files:**
- Modify: `barakat/overrides/pos_invoice.py` (the `validate` method at line 36, plus a new method)
- Create: `barakat/overrides/test_pos_invoice_cashier_limits.py`

**Interfaces:**
- Consumes: `barakat.cashier_limits.has_ad_hoc_line`, `discount_over_cap`, `AD_HOC_ITEM_CODE` from Task 2.
- Produces: `BarakatPOSInvoice.validate_cashier_limits(self)`, called from `validate`. Raises `frappe.throw` on violation.

- [ ] **Step 1: Write the failing test**

Create `barakat/overrides/test_pos_invoice_cashier_limits.py`:

```python
"""On-bench tests for the POS Invoice cashier-limit guards.

Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_pos_invoice_cashier_limits
Not runnable on the Windows dev box (imports `frappe`).

These stub the POS Profile lookup rather than creating real profiles, so they
assert the RULE, not one site's data — same approach as
test_pos_profile_warehouse_guard.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.pos_invoice import BarakatPOSInvoice

PROFILE = "Main Branch pos profile - Test Co"


def _invoice(items, discount_amount=0.0, total=0.0, grand_total=100.0, is_return=0):
    """A stand-in carrying only the fields the guard reads."""
    doc = MagicMock(spec=BarakatPOSInvoice)
    doc.pos_profile = PROFILE
    doc.items = [MagicMock(item_code=code) for code in items]
    doc.discount_amount = discount_amount
    doc.total = total
    doc.grand_total = grand_total
    doc.is_return = is_return
    doc.precision = lambda _field: 2
    return doc


def _limits(ad_hoc=0, max_discount=100.0):
    return {
        "custom_allow_ad_hoc_item": ad_hoc,
        "custom_max_discount_percent": max_discount,
    }


class AdHocItemGuard(FrappeTestCase):
    def test_misc_line_rejected_when_flag_off(self):
        doc = _invoice(["APPLE", "MISC"])
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(ad_hoc=0)):
            with self.assertRaises(frappe.ValidationError):
                BarakatPOSInvoice.validate_cashier_limits(doc)

    def test_misc_line_allowed_when_flag_on(self):
        doc = _invoice(["APPLE", "MISC"])
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(ad_hoc=1)):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_normal_cart_unaffected_when_flag_off(self):
        doc = _invoice(["APPLE", "BREAD"])
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(ad_hoc=0)):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise


class DiscountCapGuard(FrappeTestCase):
    def test_over_cap_rejected(self):
        doc = _invoice(["APPLE"], discount_amount=20.0, total=100.0)
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(max_discount=10.0)):
            with self.assertRaises(frappe.ValidationError):
                BarakatPOSInvoice.validate_cashier_limits(doc)

    def test_at_cap_allowed(self):
        doc = _invoice(["APPLE"], discount_amount=10.0, total=100.0)
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(max_discount=10.0)):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_refund_is_exempt(self):
        doc = _invoice(["APPLE"], discount_amount=99.0, total=100.0, is_return=1)
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(max_discount=10.0)):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_free_order_is_exempt(self):
        # push-orders.ts discounts the whole subtotal when a real-value order
        # rounds to a zero grand total, so it consolidates cleanly at shift
        # close. Without this exemption every such order is rejected.
        doc = _invoice(["APPLE"], discount_amount=0.5, total=0.5, grand_total=0.0)
        with patch("barakat.overrides.pos_invoice._profile_limits", return_value=_limits(max_discount=10.0)):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise


class NoProfile(FrappeTestCase):
    def test_invoice_without_a_profile_is_skipped(self):
        # Consolidated / manually created invoices may carry no pos_profile.
        # They are not a till and must never be blocked by a till's limits.
        doc = _invoice(["MISC"], discount_amount=99.0, total=100.0)
        doc.pos_profile = None
        BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

On the QA bench (see `barakat-local` skill to start it), against a test site:

```bash
bench --site <site> run-tests --module barakat.overrides.test_pos_invoice_cashier_limits
```

Expected: FAIL with `AttributeError: ... has no attribute 'validate_cashier_limits'`.

- [ ] **Step 3: Write the implementation**

In `barakat/overrides/pos_invoice.py`, add this import next to the existing `from barakat.overrides.loyalty import align_loyalty_spend`:

```python
from barakat.cashier_limits import discount_over_cap, has_ad_hoc_line
```

Add this module-level helper above `class BarakatPOSInvoice`:

```python
def _profile_limits(pos_profile):
	"""The cashier-limit fields of a POS Profile, as a plain dict.

	Its own function so the tests can stub the lookup instead of creating a
	profile — the rule under test is the comparison, not one site's data.
	"""
	return (
		frappe.db.get_value(
			"POS Profile",
			pos_profile,
			["custom_allow_ad_hoc_item", "custom_max_discount_percent"],
			as_dict=True,
		)
		or {}
	)
```

Change `validate` to call the new method:

```python
	def validate(self):
		super().validate()
		self.restore_pos_pricing_rule_details()
		self.validate_cashier_limits()
```

And add the method to the class:

```python
	def validate_cashier_limits(self):
		"""Enforce the selling profile's two server-visible cashier limits.

		Keyed off the POS Profile, never off a role: the till authenticates as a
		Manager or Branch Supervisor device session with the cashier identified
		only by a PIN, so the submitting user says nothing about who rang the
		sale. See barakat/persona_matrix.py (the Cashier row).
		"""
		if not self.pos_profile:
			# A consolidated or hand-made invoice is not a till. Never judge it
			# by a till's limits.
			return

		limits = _profile_limits(self.pos_profile)

		if not cint(limits.get("custom_allow_ad_hoc_item")):
			if has_ad_hoc_line(row.item_code for row in (self.items or [])):
				frappe.throw(
					_(
						"This till is not allowed to sell custom items. "
						"Remove the typed-in line, or enable "
						"'Allow custom items' on POS Profile {0}."
					).format(self.pos_profile),
					title=_("Custom items not allowed"),
				)

		# A refund's discount mirrors the original sale's — it was already judged
		# when that sale posted. A zero grand total is the rounding-collapse free
		# order push-orders.ts creates by discounting the whole subtotal.
		if cint(self.is_return) or not flt(self.grand_total):
			return

		max_percent = limits.get("custom_max_discount_percent")
		if discount_over_cap(
			self.discount_amount,
			self.total,
			max_percent,
			self.precision("grand_total"),
		):
			frappe.throw(
				_(
					"Discount is above the {0}% limit set on POS Profile {1}."
				).format(flt(max_percent), self.pos_profile),
				title=_("Discount too large"),
			)
```

Add `flt` to the existing `from frappe.utils import cint, getdate` import so it reads:

```python
from frappe.utils import cint, flt, getdate
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
bench --site <site> run-tests --module barakat.overrides.test_pos_invoice_cashier_limits
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Confirm the pure tests still pass on Windows**

```bash
python -m unittest barakat.test_cashier_limits barakat.test_custom_fields -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add barakat/overrides/pos_invoice.py barakat/overrides/test_pos_invoice_cashier_limits.py
git commit -m "feat(pos-invoice): reject ad-hoc lines and over-cap discounts per POS Profile"
```

---

### Task 4: Server enforcement on Customer creation

The POS stamps the profile it is running under; this hook reads the stamp and enforces. The AP path sends no stamp and is untouched.

**Files:**
- Create: `barakat/overrides/customer_pos_guard.py`
- Modify: `barakat/hooks.py` (the `Customer` entry in `doc_events`, line 153)
- Create: `barakat/overrides/test_customer_pos_guard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `barakat.overrides.customer_pos_guard.guard_pos_customer_creation(doc, method=None)`, registered as `Customer.before_insert`.

- [ ] **Step 1: Write the failing test**

Create `barakat/overrides/test_customer_pos_guard.py`:

```python
"""On-bench tests for the till's customer-creation guard.

Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_customer_pos_guard
Not runnable on the Windows dev box (imports `frappe`).
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.customer_pos_guard import guard_pos_customer_creation

PROFILE = "Main Branch pos profile - Test Co"


def _customer(stamp):
    doc = MagicMock()
    doc.custom_pos_profile = stamp
    doc.customer_name = "Walk In"
    return doc


class AdminPanelPathIsUntouched(FrappeTestCase):
    def test_no_stamp_is_allowed(self):
        # This is the AP creating a customer. It must never be blocked, and it
        # must not even cost a database read.
        with patch("barakat.overrides.customer_pos_guard.frappe.db.get_value") as get_value:
            guard_pos_customer_creation(_customer(None))
            get_value.assert_not_called()

    def test_blank_stamp_is_allowed(self):
        with patch("barakat.overrides.customer_pos_guard.frappe.db.get_value") as get_value:
            guard_pos_customer_creation(_customer("   "))
            get_value.assert_not_called()


class TillPath(FrappeTestCase):
    def test_allowed_when_flag_on(self):
        with patch("barakat.overrides.customer_pos_guard.frappe.db.get_value", return_value=1):
            guard_pos_customer_creation(_customer(PROFILE))  # must not raise

    def test_rejected_when_flag_off(self):
        with patch("barakat.overrides.customer_pos_guard.frappe.db.get_value", return_value=0):
            with self.assertRaises(frappe.ValidationError):
                guard_pos_customer_creation(_customer(PROFILE))

    def test_rejected_when_profile_does_not_exist(self):
        # Fails closed. A till that cannot name its own profile has no business
        # creating customers, and returning None here would otherwise read as
        # "flag off" by accident rather than by decision.
        with patch("barakat.overrides.customer_pos_guard.frappe.db.get_value", return_value=None):
            with self.assertRaises(frappe.ValidationError):
                guard_pos_customer_creation(_customer(PROFILE))
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
bench --site <site> run-tests --module barakat.overrides.test_customer_pos_guard
```

Expected: FAIL with `ModuleNotFoundError: No module named 'barakat.overrides.customer_pos_guard'`.

- [ ] **Step 3: Write the implementation**

Create `barakat/overrides/customer_pos_guard.py`:

```python
"""Refuse a till-created Customer when its POS Profile forbids it.

WHAT THIS IS AND IS NOT. The desktop POS authenticates as a Manager or Branch
Supervisor device session, with the cashier identified only by a PIN — see the
Cashier row in barakat/persona_matrix.py. So the server cannot tell a till's
`POST /api/resource/Customer` from the admin panel's by looking at the user, and
a role permission cannot express this rule at all.

What the till CAN do is say which profile it is running under, which it does by
stamping `custom_pos_profile`. This guard trusts that stamp. A client that lies
about not being a till slips through; no cashier operating the app can do that.
It is a guard against a cashier, not a security boundary, and must not be
described as one.
"""

import frappe
from frappe import _
from frappe.utils import cint


def guard_pos_customer_creation(doc, method=None):
	profile = (doc.get("custom_pos_profile") or "").strip()
	if not profile:
		# The admin panel. Not a till — nothing to enforce, and no read to pay for.
		return

	allowed = frappe.db.get_value(
		"POS Profile", profile, "custom_allow_customer_creation"
	)

	if allowed is None:
		# Fail closed. `get_value` returns None both for "no such profile" and
		# for a profile missing the field; neither is a state in which we can
		# prove the till is permitted.
		frappe.throw(
			_("POS Profile {0} was not found, so this till cannot add customers.").format(
				profile
			),
			title=_("Adding customers not allowed"),
		)

	if not cint(allowed):
		frappe.throw(
			_(
				"This till is not allowed to add customers. "
				"Enable 'Allow creating customers' on POS Profile {0}."
			).format(profile),
			title=_("Adding customers not allowed"),
		)
```

In `barakat/hooks.py`, change the `Customer` entry (line 153) from:

```python
	"Customer": {
		"validate": "barakat.validations.validate_customer_mobile_unique",
	},
```

to:

```python
	"Customer": {
		"validate": "barakat.validations.validate_customer_mobile_unique",
		# `before_insert`, not `validate`: the rule is about who may CREATE a
		# customer. Running it on every save would block edits to a customer a
		# till legitimately created before the flag was turned off.
		"before_insert": "barakat.overrides.customer_pos_guard.guard_pos_customer_creation",
	},
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
bench --site <site> run-tests --module barakat.overrides.test_customer_pos_guard
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Clear the hook cache on every site**

Adding a `doc_events` handler needs an explicit cache clear. `bench restart` and `bench migrate` both leave the stale hook cached, which shows up as saves that ignore the new rule.

```bash
bench --site <site> clear-cache
```

- [ ] **Step 6: Commit**

```bash
git add barakat/overrides/customer_pos_guard.py barakat/overrides/test_customer_pos_guard.py barakat/hooks.py
git commit -m "feat(customer): refuse till-created customers when the POS Profile forbids it"
```

---

### Task 5: The barakat version bump

**Superseded during execution.** This task originally added
`barakat/patches/backfill_pos_profile_cashier_limits.py`. It was measured on the QA bench
before being written, and **the patch is not needed and would have been dead code** — see
the Rollout section of the spec for the evidence.

In short: a POS Profile was created on `shop1.barakat.local` *before* the Task 1 fixture was
installed, then `bench migrate` ran. Frappe adds each column `NOT NULL` with the fixture's
`default` in the DDL, so the pre-existing row already read `0 / 0 / 100` — the intended
backfill, done by Frappe. The planned patch guarded on `value is not None`, which a
`NOT NULL` column can never satisfy, so it would have shipped as a permanent no-op.

The real guard on the safe value is the fixture `default` itself, already pinned by
`test_custom_fields.CashierLimitFieldsAreDeclared`.

**Files:**
- Modify: `barakat/__init__.py`

- [ ] **Step 1: Bump the version**

Change `__version__` from `4.8.0` to `4.9.0`. Read the current value first.

- [ ] **Step 2: Confirm the column defaults on a site with a pre-existing profile**

```bash
bench --site <site> migrate
```

Then check that a profile created before the fixture landed reads sane values:

```sql
SELECT name, custom_allow_ad_hoc_item, custom_allow_customer_creation,
       custom_max_discount_percent FROM `tabPOS Profile`;
```

Expected: `0`, `0`, `100`. If any pre-existing profile reads `0` for the discount cap, STOP —
that is the outage condition, and a backfill patch really is needed after all.

- [ ] **Step 3: Run the pure suites**

```bash
python -m unittest barakat.test_cashier_limits barakat.test_custom_fields
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add barakat/__init__.py docs/superpowers
git commit -m "chore(release): barakat 4.9.0, and record why no backfill patch is needed"
```

---

### Task 6: Carry the three fields through the proxy

**Files:**
- Modify: `proxy-barakat-dev/src/modules/pos-profiles/types.ts`
- Modify: `proxy-barakat-dev/src/modules/pos-profiles/service.ts` (the `ERPPosProfile` interface at line 135, the mapper at line 234, `PROFILE_FIELDS` at line 244, the create payloads at lines 515 and 615, the update mapper at line 632)
- Modify: `proxy-barakat-dev/src/modules/pos-profiles/index.ts` (the response schema at line 35)
- Modify: `proxy-barakat-dev/package.json`
- Create: `proxy-barakat-dev/src/modules/pos-profiles/cashier-limits.spec.ts`

**Interfaces:**
- Consumes: the ERPNext field names from Task 1.
- Produces: on the wire, `allowAdHocItem: boolean`, `allowCustomerCreation: boolean`, `maxDiscountPercent: number` on every POS Profile response, and the same three as optional inputs on create and update.

- [ ] **Step 1: Write the failing test**

Create `proxy-barakat-dev/src/modules/pos-profiles/cashier-limits.spec.ts`:

```ts
import { describe, expect, test } from 'bun:test'

import { mapPosProfile } from './service'

// The shape ERPNext returns. Only the fields the mapper reads are set.
function erpProfile(overrides: Record<string, unknown> = {}) {
  return {
    name: 'Main - TC',
    company: 'Test Co',
    warehouse: 'Stores - TC',
    selling_price_list: 'Standard Selling',
    write_off_account: 'Write Off - TC',
    write_off_cost_center: 'Main - TC',
    disabled: 0,
    ...overrides,
  } as never
}

describe('cashier limits mapping', () => {
  test('reads the three fields off the ERPNext doc', () => {
    const mapped = mapPosProfile(
      erpProfile({
        custom_allow_ad_hoc_item: 1,
        custom_allow_customer_creation: 1,
        custom_max_discount_percent: 15,
      }),
    )
    expect(mapped.allowAdHocItem).toBe(true)
    expect(mapped.allowCustomerCreation).toBe(true)
    expect(mapped.maxDiscountPercent).toBe(15)
  })

  test('a zero cap survives as zero, not as unlimited', () => {
    const mapped = mapPosProfile(erpProfile({ custom_max_discount_percent: 0 }))
    expect(mapped.maxDiscountPercent).toBe(0)
  })

  test('an un-backfilled profile reads as unlimited, never as zero', () => {
    // A profile the patch has not reached yet returns undefined. Defaulting to
    // 0 here would tell the AP and the till that discounts are banned.
    const mapped = mapPosProfile(erpProfile())
    expect(mapped.maxDiscountPercent).toBe(100)
    expect(mapped.allowAdHocItem).toBe(false)
    expect(mapped.allowCustomerCreation).toBe(false)
  })
})
```

If the mapper is not currently exported as `mapPosProfile`, export it — check its real name around `service.ts:225` first and use that name throughout this task.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/barakat-qa/proxy-barakat-dev && bun test src/modules/pos-profiles/cashier-limits.spec.ts
```

Expected: FAIL — `allowAdHocItem` is `undefined`.

- [ ] **Step 3: Extend the ERPNext interface and the mapper**

In `service.ts`, add to the `ERPPosProfile` interface, next to `allow_partial_payment?: 0 | 1`:

```ts
  custom_allow_ad_hoc_item?: 0 | 1
  custom_allow_customer_creation?: 0 | 1
  custom_max_discount_percent?: number
```

In the mapper, next to `allowPartialPayment: p.allow_partial_payment === 1,`:

```ts
    allowAdHocItem: p.custom_allow_ad_hoc_item === 1,
    allowCustomerCreation: p.custom_allow_customer_creation === 1,
    // `?? 100`, never `?? 0`. A profile the backfill patch has not reached yet
    // returns undefined, and reporting that as 0 would tell the till that every
    // discount is banned.
    maxDiscountPercent: p.custom_max_discount_percent ?? 100,
```

Add the three ERPNext fieldnames to `PROFILE_FIELDS`:

```ts
const PROFILE_FIELDS = JSON.stringify([
  'name', 'company', 'warehouse', 'selling_price_list',
  'taxes_and_charges', 'customer', 'write_off_account',
  'write_off_cost_center', 'disabled', 'allow_partial_payment',
  'custom_allow_ad_hoc_item', 'custom_allow_customer_creation',
  'custom_max_discount_percent',
])
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
bun test src/modules/pos-profiles/cashier-limits.spec.ts
```

Expected: 3 tests PASS.

- [ ] **Step 5: Declare the fields on the response schema**

In `index.ts`, add to the object at line 35, after `allowPartialPayment: t.Boolean(),`:

```ts
  allowAdHocItem: t.Boolean(),
  allowCustomerCreation: t.Boolean(),
  maxDiscountPercent: t.Number(),
```

Elysia validates responses: without this the mapper's new fields are stripped before the AP ever sees them, and the AP form silently shows defaults.

- [ ] **Step 6: Accept the fields on create and update**

In `types.ts`, add to **both** `CreatePosProfileBody` and `UpdatePosProfileBody`:

```ts
  allowAdHocItem: t.Optional(t.Boolean()),
  allowCustomerCreation: t.Optional(t.Boolean()),
  maxDiscountPercent: t.Optional(t.Number({ minimum: 0, maximum: 100 })),
```

In `service.ts`, in both create payload builders (around lines 515 and 615), next to `allow_partial_payment: 1,`:

```ts
    custom_allow_ad_hoc_item: body.allowAdHocItem ? 1 : 0,
    custom_allow_customer_creation: body.allowCustomerCreation ? 1 : 0,
    custom_max_discount_percent: body.maxDiscountPercent ?? 100,
```

And in the update mapper, next to the `allowPartialPayment` block at line 632:

```ts
  if (body.allowAdHocItem !== undefined)
    updateData.custom_allow_ad_hoc_item = body.allowAdHocItem ? 1 : 0
  if (body.allowCustomerCreation !== undefined)
    updateData.custom_allow_customer_creation = body.allowCustomerCreation ? 1 : 0
  if (body.maxDiscountPercent !== undefined)
    updateData.custom_max_discount_percent = body.maxDiscountPercent
```

Also widen the create-body type declarations at `service.ts:475` and `service.ts:593` with the same three optional properties, matching how `allowPartialPayment?: boolean` appears there.

- [ ] **Step 7: Bump the version and run the full suite**

In `package.json`, bump `version` from `6.3.2` to `6.4.0` (read the current value first).

```bash
bun test && bunx tsc --noEmit
```

Expected: all tests PASS, no type errors.

- [ ] **Step 8: Commit**

```bash
git add src/modules/pos-profiles package.json
git commit -m "feat(pos-profiles): carry the three cashier limits through the proxy, bump to 6.4.0"
```

---

### Task 7: The AP "Cashier Limits" section

**Files:**
- Modify: `admin_panel_barakat-dev/src/pages/app/pos/pos-profile-page.tsx` (the zod schema at line 103, the two default blocks at lines 243 and 272, and a new `PageSection` after the one ending at line 710)
- Modify: `admin_panel_barakat-dev/src/i18n/locales/en.json`, `ar.json`, `he.json`
- Modify: `admin_panel_barakat-dev/src/constants/common/release-notes.ts`
- Modify: `admin_panel_barakat-dev/package.json`

**Interfaces:**
- Consumes: `allowAdHocItem`, `allowCustomerCreation`, `maxDiscountPercent` from Task 6.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the i18n keys**

In each of the three locale files, add these keys inside the existing `posProfiles` object. English (`en.json`):

```json
"sectionCashierLimits": "Cashier limits",
"allowAdHocItem": "Allow custom items",
"allowAdHocItemHelp": "Lets the cashier add an item that is not in the catalogue by typing its name and price. Off means the \"New item\" button is hidden at the till.",
"allowCustomerCreation": "Allow creating customers",
"allowCustomerCreationHelp": "Lets the cashier add a new customer at the till, from the customers page and from the customer button on an order. Off means both buttons are hidden.",
"maxDiscountPercent": "Maximum discount %",
"maxDiscountPercentHelp": "The largest discount the cashier may apply to an order. 100 means no limit. 0 hides the discount button completely.",
"cashierLimitsTurnOffWarning": "Turning this off will reject any orders still waiting to sync from a till that used it. They stay in the till's history and will sync if you turn it back on."
```

Arabic (`ar.json`):

```json
"sectionCashierLimits": "صلاحيات الكاشير",
"allowAdHocItem": "السماح بإضافة أصناف يدوية",
"allowAdHocItemHelp": "يتيح للكاشير إضافة صنف غير موجود في الكتالوج بكتابة اسمه وسعره. عند الإيقاف يختفي زر \"صنف جديد\" في نقطة البيع.",
"allowCustomerCreation": "السماح بإضافة زبائن",
"allowCustomerCreationHelp": "يتيح للكاشير إضافة زبون جديد من نقطة البيع، من صفحة الزبائن ومن زر الزبون في الطلب. عند الإيقاف يختفي الزران معًا.",
"maxDiscountPercent": "أقصى نسبة خصم %",
"maxDiscountPercentHelp": "أكبر خصم يستطيع الكاشير تطبيقه على الطلب. 100 تعني بلا حد. 0 تُخفي زر الخصم نهائيًا.",
"cashierLimitsTurnOffWarning": "إيقاف هذا الخيار سيرفض أي طلبات ما زالت بانتظار المزامنة من جهاز استخدمه. تبقى في سجل الجهاز وتُزامَن إذا أعدت تشغيله."
```

Hebrew (`he.json`):

```json
"sectionCashierLimits": "הרשאות קופאי",
"allowAdHocItem": "אפשר פריטים ידניים",
"allowAdHocItemHelp": "מאפשר לקופאי להוסיף פריט שאינו בקטלוג על ידי הקלדת שם ומחיר. כשכבוי, כפתור \"פריט חדש\" מוסתר בקופה.",
"allowCustomerCreation": "אפשר יצירת לקוחות",
"allowCustomerCreationHelp": "מאפשר לקופאי להוסיף לקוח חדש בקופה, מדף הלקוחות ומכפתור הלקוח בהזמנה. כשכבוי, שני הכפתורים מוסתרים.",
"maxDiscountPercent": "הנחה מרבית %",
"maxDiscountPercentHelp": "ההנחה הגדולה ביותר שהקופאי רשאי להחיל על הזמנה. 100 פירושו ללא הגבלה. 0 מסתיר את כפתור ההנחה לגמרי.",
"cashierLimitsTurnOffWarning": "כיבוי האפשרות ידחה הזמנות שעדיין ממתינות לסנכרון מקופה שהשתמשה בה. הן נשארות בהיסטוריית הקופה ויסונכרנו אם תופעל שוב."
```

- [ ] **Step 2: Extend the form schema and defaults**

In `pos-profile-page.tsx`, add to the zod schema next to `allowPartialPayment: z.boolean().optional(),`:

```ts
    allowAdHocItem: z.boolean().optional(),
    allowCustomerCreation: z.boolean().optional(),
    maxDiscountPercent: z.coerce.number().min(0).max(100).optional(),
```

In **both** default blocks (lines 243 and 272), next to the `allowPartialPayment` entries:

```ts
      allowAdHocItem: profile?.allowAdHocItem ?? false,
      allowCustomerCreation: profile?.allowCustomerCreation ?? false,
      maxDiscountPercent: profile?.maxDiscountPercent ?? 100,
```

At line 272 the object uses `profile.` rather than `profile?.` — match whichever form the surrounding lines use.

- [ ] **Step 3: Add the section**

Import `FieldHelp` at the top of the file:

```tsx
import FieldHelp from '@/components/common/field-help';
```

Add a new `PageSection` immediately after the one that closes at line 710 (the section containing `allowPartialPayment`), and before the `sectionAccounting` one:

```tsx
        <PageSection label={t('posProfiles.sectionCashierLimits')} pureLabel>
          <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="allowAdHocItem"
              render={({ field }) => (
                <FormItem className="col-span-full">
                  <div className="flex flex-row items-center gap-3 space-y-0 pt-1">
                    <FormControl>
                      <Checkbox
                        checked={field.value ?? false}
                        onCheckedChange={field.onChange}
                        disabled={isDisabled}
                      />
                    </FormControl>
                    <FormLabel className="mt-0! cursor-pointer font-normal">
                      {t('posProfiles.allowAdHocItem')}
                    </FormLabel>
                    <FieldHelp text={t('posProfiles.allowAdHocItemHelp')} />
                  </div>
                  {profile?.allowAdHocItem && !field.value && (
                    <p className="text-accent-state-warning text-xs">
                      {t('posProfiles.cashierLimitsTurnOffWarning')}
                    </p>
                  )}
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="allowCustomerCreation"
              render={({ field }) => (
                <FormItem className="col-span-full">
                  <div className="flex flex-row items-center gap-3 space-y-0 pt-1">
                    <FormControl>
                      <Checkbox
                        checked={field.value ?? false}
                        onCheckedChange={field.onChange}
                        disabled={isDisabled}
                      />
                    </FormControl>
                    <FormLabel className="mt-0! cursor-pointer font-normal">
                      {t('posProfiles.allowCustomerCreation')}
                    </FormLabel>
                    <FieldHelp text={t('posProfiles.allowCustomerCreationHelp')} />
                  </div>
                  {profile?.allowCustomerCreation && !field.value && (
                    <p className="text-accent-state-warning text-xs">
                      {t('posProfiles.cashierLimitsTurnOffWarning')}
                    </p>
                  )}
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="maxDiscountPercent"
              render={({ field }) => (
                <FormItem>
                  <div className="flex flex-row items-center gap-2">
                    <FormLabel>{t('posProfiles.maxDiscountPercent')}</FormLabel>
                    <FieldHelp text={t('posProfiles.maxDiscountPercentHelp')} />
                  </div>
                  <FormControl>
                    <Input
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={field.value ?? 100}
                      onChange={field.onChange}
                      disabled={isDisabled}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
        </PageSection>
```

If `text-accent-state-warning` is not a token in this project, use whatever the file already uses for a warning line — check the existing classes before inventing one. If `Input` is not already imported in this file, import it from `@/components/ui/input`.

- [ ] **Step 4: Bump the version and write the release note**

In `package.json`, bump `version` from `1.31.2` to `1.32.0` (read the current value first).

In `src/constants/common/release-notes.ts`, add a new entry at the **top** of the array:

```ts
  {
    version: '1.32.0', // must match package.json exactly
    highlights: {
      ar: [
        'يمكنك الآن التحكم بما يستطيع الكاشير فعله من إعدادات نقطة البيع: إضافة أصناف يدوية، وإضافة زبائن.',
        'تستطيع تحديد أقصى نسبة خصم يطبّقها الكاشير على الطلب.'
      ],
      en: [
        'You can now control what a cashier may do from the POS profile: adding custom items, and adding customers.',
        'You can set the largest discount a cashier may apply to an order.'
      ],
      he: [
        'ניתן כעת לקבוע מה מותר לקופאי לעשות מתוך פרופיל הקופה: הוספת פריטים ידניים והוספת לקוחות.',
        'ניתן להגדיר את ההנחה המרבית שקופאי רשאי להחיל על הזמנה.'
      ]
    }
  },
```

The `version` string must match `package.json` exactly, or the dialog never appears.

- [ ] **Step 5: Typecheck**

The pre-commit hook typechecks the whole project, so run it yourself first:

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/barakat-qa/admin_panel_barakat-dev && bunx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: See it render**

Start the proxy then the AP (proxy first). Check nothing already owns the ports before starting — `netstat -ano | findstr ":8099 " | findstr LISTENING`, and the same for `:3000`. If QA's AP owns 3000, stop it first; the port is not negotiable.

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/barakat-qa/proxy-barakat-dev && bun run dev
```

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/barakat-qa/admin_panel_barakat-dev && bun run dev
```

Open `http://localhost:3000` (not `127.0.0.1`) and go to a POS profile's edit page. Confirm: the Cashier Limits section renders, all three "?" icons show their tooltip on hover, the two checkboxes are unchecked and the number reads 100 on a backfilled profile, and turning a previously-on toggle off shows the warning line.

If the AP serves a stale bundle, unregister its service worker and clear caches — local dev has a service worker that will happily serve yesterday's build.

- [ ] **Step 7: Commit**

```bash
git add src/pages/app/pos/pos-profile-page.tsx src/i18n/locales src/constants/common/release-notes.ts package.json
git commit -m "feat(pos-profiles): cashier limits section with help tooltips, bump to 1.32.0"
```

---

### Task 8: Pull the limits into the POS and expose them to the renderer

No UI change yet — this task only makes the three values available, and is worth its own gate because everything in Tasks 9 and 10 reads them.

**Files:**
- Modify: `electrobun-pos-dev/src/bun/auth/credentials-store.ts` (the `PosProfileData` type at line 45)
- Modify: `electrobun-pos-dev/src/bun/sync/pull-pos-profile.ts`
- Modify: `electrobun-pos-dev/src/bun/rpc/handlers.ts` (next to `getSiteSettings` at line 3052)
- Create: `electrobun-pos-dev/src/mainview/hooks/use-cashier-limits.ts`
- Create: `electrobun-pos-dev/src/bun/sync/pull-pos-profile-limits.spec.ts`

**Interfaces:**
- Consumes: the ERPNext field names from Task 1.
- Produces:
  - `PosProfileData` gains `allowAdHocItem: boolean`, `allowCustomerCreation: boolean`, `maxDiscountPercent: number`.
  - RPC `getCashierLimits(): { allowAdHocItem: boolean; allowCustomerCreation: boolean; maxDiscountPercent: number }`.
  - `useCashierLimits(): { allowAdHocItem: boolean; allowCustomerCreation: boolean; maxDiscountPercent: number }` — never returns undefined; falls back to the restrictive-but-safe defaults.

- [ ] **Step 1: Write the failing test**

Create `electrobun-pos-dev/src/bun/sync/pull-pos-profile-limits.spec.ts`:

```ts
import { describe, expect, test } from "bun:test";

import { readCashierLimits } from "./pull-pos-profile";

describe("readCashierLimits", () => {
	test("reads the three fields off the ERPNext doc", () => {
		expect(
			readCashierLimits({
				custom_allow_ad_hoc_item: 1,
				custom_allow_customer_creation: 1,
				custom_max_discount_percent: 15,
			}),
		).toEqual({
			allowAdHocItem: true,
			allowCustomerCreation: true,
			maxDiscountPercent: 15,
		});
	});

	test("a zero cap survives as zero, not as unlimited", () => {
		expect(readCashierLimits({ custom_max_discount_percent: 0 }).maxDiscountPercent).toBe(0);
	});

	test("an un-backfilled profile reads as unlimited, never as zero", () => {
		// A profile the backfill patch has not reached returns nothing for this
		// field. Reading it as 0 would silently ban every discount at the till.
		expect(readCashierLimits({}).maxDiscountPercent).toBe(100);
	});

	test("both toggles default to off", () => {
		expect(readCashierLimits({})).toMatchObject({
			allowAdHocItem: false,
			allowCustomerCreation: false,
		});
	});

	test("a cap outside 0-100 is clamped", () => {
		expect(readCashierLimits({ custom_max_discount_percent: 250 }).maxDiscountPercent).toBe(100);
		expect(readCashierLimits({ custom_max_discount_percent: -5 }).maxDiscountPercent).toBe(0);
	});
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/barakat-qa/electrobun-pos-dev && bun test src/bun/sync/pull-pos-profile-limits.spec.ts
```

Expected: FAIL — `readCashierLimits` is not exported.

- [ ] **Step 3: Extend the profile type**

In `src/bun/auth/credentials-store.ts`, add to `PosProfileData`, after `defaultPaymentMode`:

```ts
	/**
	 * Per-till cashier limits — see the POS Profile's "Cashier Limits" section.
	 *
	 * The defaults applied when a field is absent MIRROR the barakat backfill
	 * patch exactly (toggles off, cap 100), so a profile cached before this
	 * feature existed behaves identically to a freshly pulled one. A cap
	 * defaulting to 0 would silently ban every discount on an upgraded till.
	 */
	allowAdHocItem: boolean;
	allowCustomerCreation: boolean;
	maxDiscountPercent: number;
```

- [ ] **Step 4: Read them in the pull**

In `src/bun/sync/pull-pos-profile.ts`, add the three fields to `RawPosProfile["data"]`:

```ts
		custom_allow_ad_hoc_item?: number | null;
		custom_allow_customer_creation?: number | null;
		custom_max_discount_percent?: number | null;
```

Add this exported helper above `fetchPosProfileData`:

```ts
/**
 * The profile's cashier limits, with the defaults a missing field must take.
 *
 * Exported so the defaults are testable on their own: they mirror the barakat
 * backfill patch (toggles off, cap 100), and getting the cap's default wrong is
 * the difference between "no limit" and "no discounts allowed at all".
 */
export function readCashierLimits(d: RawPosProfile["data"]): {
	allowAdHocItem: boolean;
	allowCustomerCreation: boolean;
	maxDiscountPercent: number;
} {
	const raw = d.custom_max_discount_percent;
	const cap = typeof raw === "number" && Number.isFinite(raw) ? raw : 100;
	return {
		allowAdHocItem: Boolean(d.custom_allow_ad_hoc_item),
		allowCustomerCreation: Boolean(d.custom_allow_customer_creation),
		maxDiscountPercent: Math.min(100, Math.max(0, cap)),
	};
}
```

Add the three fieldnames to the `fields` array in the request query, and spread the helper into the returned object:

```ts
	return {
		...readCashierLimits(d),
		priceList: d.selling_price_list || null,
		// ...the rest unchanged
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
bun test src/bun/sync/pull-pos-profile-limits.spec.ts
```

Expected: 5 tests PASS.

- [ ] **Step 6: Add the RPC handler**

In `src/bun/rpc/handlers.ts`, next to `getSiteSettings: () => getSiteSettings(),` at line 3052:

```ts
		getCashierLimits: (): {
			allowAdHocItem: boolean;
			allowCustomerCreation: boolean;
			maxDiscountPercent: number;
		} => {
			// Falls back to the same defaults the profile pull applies, so a till
			// that has not yet pulled a profile behaves like a backfilled one
			// rather than banning every discount.
			try {
				const cfg = getErpnextConfig();
				return {
					allowAdHocItem: cfg.allowAdHocItem ?? false,
					allowCustomerCreation: cfg.allowCustomerCreation ?? false,
					maxDiscountPercent: cfg.maxDiscountPercent ?? 100,
				};
			} catch {
				return {
					allowAdHocItem: false,
					allowCustomerCreation: false,
					maxDiscountPercent: 100,
				};
			}
		},
```

This requires the three values on `ErpnextConfig`. In `src/bun/erpnext/config.ts`, add them to the `ErpnextConfig` type next to `cashAccount` (line 29), and populate them in the object built around line 184, in the same `profileData?.X ?? fallback` style:

```ts
		allowAdHocItem: profileData?.allowAdHocItem ?? false,
		allowCustomerCreation: profileData?.allowCustomerCreation ?? false,
		maxDiscountPercent: profileData?.maxDiscountPercent ?? 100,
```

- [ ] **Step 7: Add the renderer hook**

Create `src/mainview/hooks/use-cashier-limits.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { getBunRpc, isElectrobunAvailable } from "@/lib/barakat-rpc";

export type CashierLimits = {
	allowAdHocItem: boolean;
	allowCustomerCreation: boolean;
	maxDiscountPercent: number;
};

// The safe fallback, used before the first fetch and in the browser preview.
// It mirrors the barakat backfill patch: buttons off, discounts unlimited.
// Defaulting the cap to 0 here would make the discount button vanish for a
// split second on every cold start.
const FALLBACK: CashierLimits = {
	allowAdHocItem: false,
	allowCustomerCreation: false,
	maxDiscountPercent: 100,
};

export const cashierLimitsQueryKey = ["cashierLimits"] as const;

/**
 * The selling profile's cashier limits, kept current with the profile.
 *
 * Refetched on the same two events the site settings listen to — a sync tick and
 * a session change — because those are exactly when a re-pulled POS Profile can
 * have changed them. A toggle flipped in the admin panel therefore reaches the
 * till on its next sync, not instantly, which is the documented behaviour.
 */
export function useCashierLimits(): CashierLimits {
	const queryClient = useQueryClient();

	const { data } = useQuery({
		queryKey: cashierLimitsQueryKey,
		queryFn: async (): Promise<CashierLimits> => {
			if (!isElectrobunAvailable()) return FALLBACK;
			return await getBunRpc().request.getCashierLimits({});
		},
		staleTime: 30_000,
	});

	useEffect(() => {
		if (!isElectrobunAvailable()) return;
		const rpc = getBunRpc();
		const invalidate = () => {
			void queryClient.invalidateQueries({ queryKey: cashierLimitsQueryKey });
		};
		rpc.addMessageListener("syncOk", invalidate);
		rpc.addMessageListener("sessionChanged", invalidate);
		return () => {
			rpc.removeMessageListener("syncOk", invalidate);
			rpc.removeMessageListener("sessionChanged", invalidate);
		};
	}, [queryClient]);

	return data ?? FALLBACK;
}
```

- [ ] **Step 8: Verify the whole POS suite**

```bash
bun run verify
```

Expected: typecheck, lint, format check and tests all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/bun/auth/credentials-store.ts src/bun/sync/pull-pos-profile.ts src/bun/sync/pull-pos-profile-limits.spec.ts src/bun/erpnext/config.ts src/bun/rpc/handlers.ts src/mainview/hooks/use-cashier-limits.ts
git commit -m "feat(pos): pull the profile's cashier limits and expose them to the renderer"
```

---

### Task 9: Gate the F6 button and cap the discount dialog

**Files:**
- Modify: `electrobun-pos-dev/src/mainview/features/register/components/register-action-bar.tsx`
- Modify: `electrobun-pos-dev/src/mainview/features/register/components/register-discount-dialog.tsx` (the props at line 176, and the two sanitizers at lines 66 and 96)
- Modify: `electrobun-pos-dev/src/mainview/features/register/register-page.tsx` (`onNewItem` at line 327, the action bar render at line 673, the discount dialog render)
- Create: `electrobun-pos-dev/src/mainview/features/register/lib/discount-cap.test.ts`

**Interfaces:**
- Consumes: `useCashierLimits()` from Task 8.
- Produces:
  - `RegisterActionBar` gains props `showNewItem?: boolean` and `showDiscount?: boolean` (both default `true`).
  - `RegisterDiscountDialog` gains prop `maxPercent: number`.
  - `capDiscountAmount(subtotal: number, maxPercent: number, currency: string): number` in `lib/discount-cap.ts`.

- [ ] **Step 1: Write the failing test**

Create `electrobun-pos-dev/src/mainview/features/register/lib/discount-cap.test.ts`:

```ts
import { describe, expect, test } from "bun:test";

import { capDiscountAmount } from "./discount-cap";

describe("capDiscountAmount", () => {
	test("an unlimited cap allows the whole subtotal", () => {
		expect(capDiscountAmount(100, 100, "ILS")).toBe(100);
	});

	test("a 10% cap allows a tenth of the subtotal", () => {
		expect(capDiscountAmount(100, 10, "ILS")).toBe(10);
	});

	test("a zero cap allows nothing", () => {
		expect(capDiscountAmount(100, 0, "ILS")).toBe(0);
	});

	test("the cap is rounded to the currency, matching what the server checks", () => {
		// 10% of 33.33 is 3.333. The server compares the rounded amount we send,
		// so the ceiling the keypad enforces must be the rounded one too.
		expect(capDiscountAmount(33.33, 10, "ILS")).toBe(3.33);
	});

	test("never exceeds the subtotal even at 100%", () => {
		expect(capDiscountAmount(19.99, 100, "ILS")).toBe(19.99);
	});

	test("a zero subtotal caps at zero", () => {
		expect(capDiscountAmount(0, 50, "ILS")).toBe(0);
	});
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
bun test src/mainview/features/register/lib/discount-cap.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Write the helper**

Create `src/mainview/features/register/lib/discount-cap.ts`:

```ts
import { roundMoney } from "@shared/money";

/**
 * The largest discount amount this profile permits on this subtotal.
 *
 * Rounded to the currency because that is the number the POS actually sends,
 * and barakat's `pos_invoice.py` compares the sent amount against the same
 * ceiling. A keypad that allowed an unrounded 3.333 would let the cashier
 * build an order the server then rejects.
 */
export function capDiscountAmount(
	subtotal: number,
	maxPercent: number,
	currency: string,
): number {
	const pct = Math.min(100, Math.max(0, maxPercent));
	return roundMoney(Math.max(0, subtotal) * (pct / 100), currency);
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
bun test src/mainview/features/register/lib/discount-cap.test.ts
```

Expected: 6 tests PASS.

- [ ] **Step 5: Make the two action tiles optional**

In `register-action-bar.tsx`, add to the props of `RegisterActionBar`:

```tsx
	showNewItem = true,
	showDiscount = true,
```

with types:

```tsx
	showNewItem?: boolean;
	showDiscount?: boolean;
```

Wrap the two tiles in the render:

```tsx
				{showDiscount && (
					<ActionButton
						icon={CirclePercent}
						label={t("registerActionDiscount")}
						shortcut="F3"
						onClick={onDiscount}
						disabled={disabled}
					/>
				)}

				{showNewItem && (
					<ActionButton
						icon={CircleFadingPlus}
						label={t("registerActionNewItem")}
						shortcut="F6"
						onClick={onNewItem}
						disabled={disabled}
					/>
				)}
```

The remaining tiles are `flex-1`, so they widen to fill the row. No other layout change is needed.

- [ ] **Step 6: Cap the dialog**

In `register-discount-dialog.tsx`, add `maxPercent: number;` to the props type and `maxPercent,` to the destructure.

Change `sanitizePercentAppend` to take the cap. Its two existing `<= 100` checks become `<= cap`:

```ts
function sanitizePercentAppend(
	prev: string,
	char: string,
	maxPercent: number,
): { value: string; digitLimitReached: boolean } {
```

Inside, replace both `n <= 100` comparisons with `n <= maxPercent`, and pass `maxPercent` at every call site in the file.

Where the component computes the amount tab's ceiling, replace the bare subtotal with the capped one:

```ts
	const maxDiscountMajor = capDiscountAmount(subtotal, maxPercent, currency);
```

and pass `maxDiscountMajor` wherever `subtotal` was previously handed to `sanitizeDiscountAmountAppend` / `discountAmountWithinMax`. Import the helper:

```ts
import { capDiscountAmount } from "../lib/discount-cap";
```

- [ ] **Step 7: Wire the register page**

In `register-page.tsx`, add near the other hooks:

```tsx
	const cashierLimits = useCashierLimits();
```

with the import:

```tsx
import { useCashierLimits } from "@/hooks/use-cashier-limits";
```

Gate `onNewItem` (line 327) so the hotkey cannot reach a hidden button:

```tsx
	const onNewItem = useCallback(() => {
		if (isPreviewMode) return;
		if (!cashierLimits.allowAdHocItem) return;
		setAdHocOpen(true);
	}, [isPreviewMode, cashierLimits.allowAdHocItem]);
```

Do the same for the discount opener — find the callback that opens the discount dialog and add:

```tsx
		if (cashierLimits.maxDiscountPercent <= 0) return;
```

Pass the flags to the action bar (line 673):

```tsx
				showNewItem={cashierLimits.allowAdHocItem}
				showDiscount={cashierLimits.maxDiscountPercent > 0}
```

And the cap to the dialog:

```tsx
				maxPercent={cashierLimits.maxDiscountPercent}
```

- [ ] **Step 8: Verify**

```bash
bun run verify
```

Expected: everything PASS.

- [ ] **Step 9: Commit**

```bash
git add src/mainview/features/register
git commit -m "feat(register): hide the custom-item tile and cap the discount per POS Profile"
```

---

### Task 10: Gate customer creation and stamp the profile

**Files:**
- Modify: `electrobun-pos-dev/src/mainview/features/customers/customers-page.tsx` (`onAddCustomer` at line 189, the hotkey at line 224, the button at line 283)
- Modify: `electrobun-pos-dev/src/mainview/features/register/components/register-customer-dialog.tsx` (`openAddForm` at line 134, the button at line 365)
- Modify: `electrobun-pos-dev/src/bun/sync/create-customer.ts`
- Modify: `electrobun-pos-dev/electrobun.config.ts`
- Create: `electrobun-pos-dev/src/bun/sync/create-customer-guard.spec.ts`

**Interfaces:**
- Consumes: `useCashierLimits()` from Task 8; the `custom_pos_profile` field from Task 1; the `before_insert` hook from Task 4.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Create `electrobun-pos-dev/src/bun/sync/create-customer-guard.spec.ts`:

```ts
import { describe, expect, test } from "bun:test";

import { buildCustomerPayload } from "./create-customer";

describe("buildCustomerPayload", () => {
	test("stamps the POS profile so the server can enforce the limit", () => {
		const body = buildCustomerPayload({
			customerName: "Walk In",
			mobileNo: "",
			company: "Test Co",
			posProfile: "Main - TC",
		});
		expect(body.custom_pos_profile).toBe("Main - TC");
		expect(body.custom_company).toBe("Test Co");
	});

	test("omits the stamp when the till has no profile", () => {
		// Never send an empty string: the server reads a blank stamp as "this is
		// the admin panel" and would wave the request straight through.
		const body = buildCustomerPayload({
			customerName: "Walk In",
			mobileNo: "",
			company: "Test Co",
			posProfile: null,
		});
		expect("custom_pos_profile" in body).toBe(false);
	});
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
bun test src/bun/sync/create-customer-guard.spec.ts
```

Expected: FAIL — `buildCustomerPayload` is not exported.

- [ ] **Step 3: Extract and stamp**

In `src/bun/sync/create-customer.ts`, extract the body construction (currently inline, around lines 118–143) into an exported function, and add the stamp:

```ts
/**
 * The Customer payload the till POSTs to ERPNext.
 *
 * `custom_pos_profile` is what lets barakat's `Customer.before_insert` hook tell
 * a till's request from the admin panel's — the till authenticates as a Manager
 * or Branch Supervisor device session, so the user says nothing about who is
 * asking. Omitted entirely when there is no profile: a BLANK stamp reads as
 * "admin panel" on the server and would bypass the guard.
 */
export function buildCustomerPayload(input: {
	customerName: string;
	mobileNo: string;
	company: string;
	posProfile: string | null;
	customerGroup?: string;
	territory?: string;
	emailId?: string;
}): Record<string, unknown> {
	// ...move the existing body construction here unchanged, then:
	const profile = input.posProfile?.trim();
	if (profile) body.custom_pos_profile = profile;
	return body;
}
```

Call it from `createCustomer`, passing `config.posProfile`. Keep every existing validation (name length, duplicate mobile, the `noCompany` refusal) exactly where it is — this is an extraction, not a rewrite.

Add the local refusal at the top of `createCustomer`, after `const config = getErpnextConfig();`:

```ts
	// Defense-in-depth, mirroring the name-length rules below: the UI hides both
	// add-customer buttons when this is off, so reaching here means a malformed
	// RPC payload. The server enforces it too.
	if (!config.allowCustomerCreation) {
		throw new CreateCustomerValidationError(
			"notAllowed",
			"This till is not allowed to add customers.",
		);
	}
```

Add `notAllowed` to whatever union `CreateCustomerValidationError`'s first parameter is typed as, and give it a translated message in `create-customer-error.ts` alongside the existing codes.

- [ ] **Step 4: Run the test to verify it passes**

```bash
bun test src/bun/sync/create-customer-guard.spec.ts
```

Expected: 2 tests PASS.

- [ ] **Step 5: Hide both triggers**

In `customers-page.tsx`, add the hook and gate the callback:

```tsx
	const cashierLimits = useCashierLimits();

	const onAddCustomer = useCallback(() => {
		if (!cashierLimits.allowCustomerCreation) return;
		if (!online) {
			toast.message(t("customersAddOffline"));
			return;
		}
		setAddOpen(true);
	}, [cashierLimits.allowCustomerCreation, online, t]);
```

Gating the callback also disarms the `ctrl+k` hotkey at line 224, which calls it. Then wrap the button at line 283:

```tsx
					{cashierLimits.allowCustomerCreation && (
						<Button
							type="button"
							variant="default"
							className="h-14 shrink-0 gap-2 rounded-md px-4! py-2! typo-pos-stat font-medium text-primary-foreground [&_svg]:size-7"
							onClick={onAddCustomer}
						>
							{/* ...children unchanged... */}
						</Button>
					)}
```

In `register-customer-dialog.tsx`, the same shape — add the hook, gate `openAddForm` at line 134 with an early `return` when `!cashierLimits.allowCustomerCreation`, and wrap the add button at line 365 in the same conditional.

- [ ] **Step 6: Bump the POS version**

In `electrobun.config.ts`, bump `version` from `2.14.0` to `2.15.0` (read the current value first). This is what every installed till auto-downloads, so make sure it is the number you intend.

- [ ] **Step 7: Verify**

```bash
bun run verify
```

Expected: everything PASS.

- [ ] **Step 8: Commit**

```bash
git add src/mainview/features/customers src/mainview/features/register/components/register-customer-dialog.tsx src/bun/sync/create-customer.ts src/bun/sync/create-customer-guard.spec.ts src/mainview/features/register/lib/create-customer-error.ts electrobun.config.ts
git commit -m "feat(customers): hide both add-customer triggers and stamp the profile, bump to 2.15.0"
```

---

### Task 11: End-to-end verification on the local QA environment

Nothing here changes code. It is the gate that proves the feature works on a profile that predates it — the check whose absence took prod down on 2026-07-28.

**Files:** none.

**Interfaces:**
- Consumes: every prior task.
- Produces: a pass/fail verdict.

- [ ] **Step 1: Bring the environment up**

Use the `barakat-local` skill to start the local ERPNext, then pull the barakat changes inside the container (the host's `barakat-dev` worktree is NOT what local ERPNext runs). Start the dev proxy and the dev AP as in Task 7 Step 6, and run the POS with `bun run dev`.

- [ ] **Step 2: Identify a pre-existing profile**

Find a POS Profile created before this work — one that existed in the site before the fixture was installed. **Do not create a new profile for this test.** A fresh profile picks up the field defaults and will pass while every live shop fails.

```bash
bench --site <site> console
```

```python
frappe.get_all("POS Profile", fields=["name", "creation", "custom_max_discount_percent"], order_by="creation asc")
```

Confirm the oldest one now reads `custom_max_discount_percent = 100` after the migrate in Task 5.

- [ ] **Step 3: Baseline — nothing broke**

On that pre-existing profile, with all three settings at their backfilled values, at the till:
- Sell a normal order. It syncs.
- Apply a 50% discount and sell. It syncs.
- Confirm the F6 tile is **hidden** and both add-customer buttons are **hidden**.

- [ ] **Step 4: Each toggle turns its button back on**

In the AP, turn on "Allow custom items". Wait for the till's next sync (or restart the POS). The F6 tile reappears and adds a typed line that sells and syncs.

Repeat for "Allow creating customers": both the customers-page button and the customer dialog's add button reappear, and a created customer reaches ERPNext with `custom_pos_profile` set.

- [ ] **Step 5: The discount cap, both sides**

Set the cap to 10 and sync.
- The percent keypad refuses to accept 11.
- The amount keypad refuses to exceed 10% of the subtotal.
- A discount of exactly 10% on a subtotal of 33.33 **sells and syncs** — this is the tolerance test.

Then bypass the UI to prove the server gate. In the bench console, build a POS Invoice against that profile with a 20% `discount_amount` and submit it:

```python
doc = frappe.get_doc({...})  # a minimal POS Invoice on the profile, discount_amount = 20% of total
doc.insert()
```

Expected: `ValidationError` with "Discount is above the 10% limit".

Do the same with an item row of `item_code = "MISC"` while "Allow custom items" is off. Expected: `ValidationError` with "This till is not allowed to sell custom items".

- [ ] **Step 6: Zero cap hides the button**

Set the cap to 0 and sync. The F3 discount tile is gone from the register.

- [ ] **Step 7: The exemptions still work**

With the cap at 10:
- Refund a previous order. It posts.
- Sell an order whose total rounds to zero (e.g. a single 0.5 item, with rounding on). It posts.
- Sell an order containing a gifted item. It posts — gifts are per-line `discount_percentage: 100` and never touch the header discount.

- [ ] **Step 8: The admin panel is untouched**

With "Allow creating customers" off on every profile, create a customer from the AP. It must succeed, and its `custom_pos_profile` must be blank.

- [ ] **Step 9: Report**

Summarise: which steps passed, and for any that failed, the exact error and the file it points at. Do not proceed to any deploy — this plan ends at verified, committed work on four `dev` branches.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: data model -> Task 1; pure decisions -> Task 2; guard 1 and guard 3 server side -> Task 3; guard 2 server side -> Task 4; rollout (measured: no patch needed) and the barakat bump -> Task 5; proxy -> Task 6; AP surface, help tooltips, i18n, release note -> Task 7; POS profile pull and renderer plumbing -> Task 8; guard 1 and guard 3 POS side -> Task 9; guard 2 POS side and the POS bump -> Task 10; the verification list -> Task 11. The "accepted risk" section is surfaced to users by the AP warning line in Task 7 Step 3.

**Type consistency.** The wire names `allowAdHocItem` / `allowCustomerCreation` / `maxDiscountPercent` are identical in the proxy (Task 6), the AP form (Task 7), `PosProfileData` and the RPC (Task 8), and both POS UI tasks. The ERPNext fieldnames `custom_allow_ad_hoc_item` / `custom_allow_customer_creation` / `custom_max_discount_percent` / `custom_pos_profile` are identical in Tasks 1, 3, 4, 5, 6, 8 and 10. `_profile_limits` is defined in Task 3 and patched by that name in its own tests only.

**The default `100`, never `0`,** is asserted independently in four places — the fixture test (Task 1), the pure module (Task 2), the proxy mapper test (Task 6) and the POS pull test (Task 8). That redundancy is deliberate: a `0` slipping in at any one layer silently bans discounting at every shop. The fixture test is the load-bearing one, because the fixture's `default` is what Frappe writes into the column DDL and therefore what every pre-existing profile inherits (Task 5).
