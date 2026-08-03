"""Tests for `BarakatCompany.set_mode_of_payment_account`.

The behaviour under test is mostly a REFUSAL, so these are written the way the
bug was found: stand up a Cash-type Mode of Payment that belongs to somebody
else, run the method, and assert the mode came back untouched.

## The trap these tests had to avoid

The method returns early when the company already has its row, and on a site that
has been used at all it always does. A test that just calls the method and checks
a foreign mode therefore passes without the method deciding anything — green
proving nothing. So every test here first CLEARS the company's existing rows, to
put the method in the one state where it actually has to choose a mode.

Nothing is committed — `FrappeTestCase` rolls the whole case back — and every
document created here is prefixed `_Test Barakat MOP`.

Why not a pure unit test: the method's entire job is deciding WHICH row in the
database to write to, and the bug was a query with no company filter. A test with
the database mocked out would have passed against the broken code.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.company import MODE_COMPANY_FIELD

FOREIGN_MODE = "_Test Barakat MOP Foreign"
SHARED_MODE = "_Test Barakat MOP Shared"
OWN_MODE = "_Test Barakat MOP Own"


def _company_with_cash_account():
	"""A company with a cash account, plus another company to play the neighbour."""
	companies = frappe.get_all("Company", fields=["name", "default_cash_account"])
	usable = [c for c in companies if c.default_cash_account]
	if not usable:
		return None, None
	company = usable[0].name
	return company, next((c.name for c in companies if c.name != company), None)


def _make_mode(name, owner_company):
	"""A Cash-type Mode of Payment with no `accounts` rows yet."""
	frappe.get_doc(
		{
			"doctype": "Mode of Payment",
			"mode_of_payment": name,
			"type": "Cash",
			MODE_COMPANY_FIELD: owner_company,
		}
	).insert(ignore_permissions=True)


def _parents_holding(company):
	"""Every Mode of Payment carrying a row for this company."""
	return sorted(
		frappe.get_all("Mode of Payment Account", filters={"company": company}, pluck="parent")
	)


class TestCompanyModeOfPaymentAccount(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company, cls.neighbour = _company_with_cash_account()
		if not cls.company:
			# An ERROR, not a skip: a site where no company has a cash account cannot
			# exercise this path at all, and a green run that asserted nothing is
			# exactly how the original bug survived.
			raise RuntimeError(
				"No company on this site has a default_cash_account — cannot test "
				"set_mode_of_payment_account."
			)
		if not frappe.db.has_column("Mode of Payment", MODE_COMPANY_FIELD):
			raise RuntimeError(
				f"Mode of Payment has no {MODE_COMPANY_FIELD} column — run `bench migrate` first."
			)

	def setUp(self):
		super().setUp()
		# FrappeTestCase rolls back once per CLASS, not per test, so a mode built by
		# one test is still there for the next one — which showed up as a duplicate
		# key, not as a wrong result. Clear ours first.
		for mode in (FOREIGN_MODE, SHARED_MODE, OWN_MODE):
			frappe.db.delete("Mode of Payment Account", {"parent": mode})
			frappe.db.delete("Mode of Payment", {"name": mode})
		# See the module docstring: without this the method short-circuits and the
		# assertions below prove nothing. Rolled back with the rest of the case.
		frappe.db.delete("Mode of Payment Account", {"company": self.company})

	def _run(self):
		frappe.get_doc("Company", self.company).set_mode_of_payment_account()

	def test_never_lands_on_a_mode_that_is_not_ours(self):
		"""The bug — ticket 0001-595.

		A stray row makes the mode unreadable to every persona, because a staff
		User Permission pins them to one company and Frappe's document check walks
		child rows too. So the rule is absolute: a row for this company may only
		ever sit on a mode tagged with this company.
		"""
		_make_mode(FOREIGN_MODE, self.neighbour)  # None on a single-shop site
		_make_mode(SHARED_MODE, None)  # stock ERPNext's global "Cash"
		self._run()

		for parent in _parents_holding(self.company):
			self.assertEqual(
				frappe.db.get_value("Mode of Payment", parent, MODE_COMPANY_FIELD),
				self.company,
				f"{self.company} was written into {parent}, which is not its mode",
			)
		self.assertNotIn(SHARED_MODE, _parents_holding(self.company))
		if self.neighbour:
			self.assertNotIn(FOREIGN_MODE, _parents_holding(self.company))

	def test_writes_into_our_own_mode(self):
		"""The behaviour ERPNext intended, kept — just scoped to our own mode.

		Asserts the row IS written, so the refusal tests above cannot be passing
		simply because the method never writes anything at all.
		"""
		_make_mode(OWN_MODE, self.company)
		self._run()

		parents = _parents_holding(self.company)
		self.assertEqual(len(parents), 1, f"expected exactly one row, got {parents}")
		self.assertEqual(
			frappe.db.get_value("Mode of Payment", parents[0], MODE_COMPANY_FIELD), self.company
		)

	def test_is_idempotent(self):
		"""Every company save calls this. Twice must not mean two rows."""
		_make_mode(OWN_MODE, self.company)
		self._run()
		self._run()
		self.assertEqual(len(_parents_holding(self.company)), 1)

	def test_writes_nowhere_when_we_have_no_mode_of_our_own(self):
		"""No own mode = no row anywhere.

		Writing into the shared mode instead would only move the pollution: the
		setup readiness gate counts modes by `custom_company`, so a row there
		satisfies no setup step while breaking the shared mode for every persona.
		Creating a shop's payment modes belongs to the Company Accounts
		walkthrough, where a human picks the account.
		"""
		# Untag the company's real modes so it genuinely has none, then put them back.
		# Restored explicitly rather than left to the class rollback, so this test
		# cannot leak into a later one (they share one transaction).
		untagged = frappe.get_all(
			"Mode of Payment", filters={"type": "Cash", MODE_COMPANY_FIELD: self.company}, pluck="name"
		)
		for mode in untagged:
			frappe.db.set_value("Mode of Payment", mode, MODE_COMPANY_FIELD, None)
		self.addCleanup(
			lambda: [
				frappe.db.set_value("Mode of Payment", m, MODE_COMPANY_FIELD, self.company)
				for m in untagged
			]
		)
		_make_mode(SHARED_MODE, None)
		self._run()

		self.assertEqual(_parents_holding(self.company), [])
