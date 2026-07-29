"""The guard: a persona's ERPNext DocPerms must equal its AP matrix row.

Runs under the bench test runner because it reads the site's DocPerm table:

    bench --site <site> run-tests --module barakat.test_persona_matches_matrix

Without this test the two layers drift the moment someone adds a doctype, which is
exactly how the production finding of 2026-07-29 survived unnoticed: the admin panel
and the proxy both honoured the matrix, and ERPNext quietly did not.

Two directions are checked, and the second matters as much as the first:
  - too WIDE  -> a data-exposure bug (the cashier reading every payslip)
  - too NARROW -> a silent breakage. The AP renders an empty dropdown with no error,
    and the POS till try/catches every pull and falls back to defaults.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.permissions import PERSONA_ROLE_BUNDLES
from barakat.persona_matrix import MODULE_DOCTYPES, PERSONA_MATRIX, TILL_REQUIRED_READS
from barakat.scripts.perm_audit import effective_perms

WRITE_PERMS = {"write", "create", "delete", "submit", "cancel"}

# Doctypes a persona may hold beyond its matrix, with the reason. Anything else is a leak.
#
#   Employee / Salary Slip -> Barakat Self Service. Present doctype-wide but row-scoped
#       to the caller by overrides/self_service.py; proven by test_self_service_scope.
#   Purchase Invoice       -> Barakat Purchase Invoice Clerk (submit/cancel, which the
#       generated suppliers Writer cannot express).
#   GL Entry               -> Barakat Supplier Ledger Reader, filtered to supplier rows.
#   TILL_REQUIRED_READS    -> Barakat POS Operator. See persona_matrix.TILL_REQUIRED_READS:
#       Branch Supervisor is `settings: none`, but its till must read System Settings /
#       Global Defaults or it silently rounds differently from a Manager till.
ALLOWED_EXTRA = {
	"Manager": set(TILL_REQUIRED_READS) | {"Employee", "Salary Slip", "Purchase Invoice"},
	"Branch Supervisor": set(TILL_REQUIRED_READS) | {"Employee", "Salary Slip"},
	"Cashier": {"Employee", "Salary Slip", "Loyalty Program", "Loyalty Point Entry"},
	"Accountant": {
		"Employee",
		"Salary Slip",
		"Purchase Invoice",
		"Loyalty Program",
		"Loyalty Point Entry",
		"Mode of Payment",
		"Currency",
		"Currency Exchange",
	},
	"Inventory Keeper": {"Employee", "Salary Slip", "Purchase Invoice", "GL Entry"},
	"HR": {"Employee", "Salary Slip", "Branch"},
}


def _matrix_allows(persona, doctype):
	"""(may_read, may_write) for this doctype under the persona's matrix row."""
	may_read = may_write = False
	for module, level in PERSONA_MATRIX[persona].items():
		if doctype not in MODULE_DOCTYPES.get(module, ()):
			continue
		if level in ("read", "write"):
			may_read = True
		if level == "write":
			may_write = True
	return may_read, may_write


class PersonaMatchesMatrix(FrappeTestCase):
	def test_no_persona_exceeds_its_matrix(self):
		for persona in PERSONA_ROLE_BUNDLES:
			for doctype, perms in effective_perms(persona).items():
				if doctype in ALLOWED_EXTRA.get(persona, set()):
					continue
				may_read, may_write = _matrix_allows(persona, doctype)
				self.assertTrue(
					may_read,
					f"{persona} reaches {doctype} ({sorted(perms)}) "
					f"with no matrix module granting it",
				)
				if perms & WRITE_PERMS:
					self.assertTrue(
						may_write,
						f"{persona} WRITES {doctype} but its matrix says read-only",
					)

	def test_every_matrix_grant_is_actually_reachable(self):
		"""A bundle that is too NARROW fails silently. Catch it here, not in the shop."""
		for persona, row in PERSONA_MATRIX.items():
			effective = effective_perms(persona)
			for module, level in row.items():
				if level == "none":
					continue
				for doctype in MODULE_DOCTYPES.get(module, ()):
					if not frappe.db.exists("DocType", doctype):
						continue
					granted = effective.get(doctype, set())
					self.assertTrue(
						granted & {"read", "select"},
						f"{persona} has {module}: {level} but no read on {doctype}",
					)
					if level == "write":
						self.assertTrue(
							granted & WRITE_PERMS,
							f"{persona} has {module}: write but no write on {doctype}",
						)

	def test_cashier_cannot_reach_the_salary_or_staff_modules(self):
		"""The doctypes behind `salary: none` / `staff: none` beyond the two that
		Barakat Self Service row-scopes."""
		effective = effective_perms("Cashier")
		for doctype in (
			"Salary Structure",
			"Salary Component",
			"Salary Structure Assignment",
			"Payroll Settings",
			"Designation",
			"Holiday List",
			"User",
		):
			self.assertNotIn(doctype, effective, f"Cashier still reaches {doctype}")

	def test_till_doctypes_readable_by_both_device_personas(self):
		for persona in ("Manager", "Branch Supervisor"):
			effective = effective_perms(persona)
			for doctype in TILL_REQUIRED_READS:
				if not frappe.db.exists("DocType", doctype):
					continue
				self.assertTrue(
					effective.get(doctype, set()) & {"read", "select"},
					f"{persona} till would silently fall back: no read on {doctype}",
				)
