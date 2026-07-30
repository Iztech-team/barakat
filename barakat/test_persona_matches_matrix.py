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

from barakat.overrides.staff_roles import persona_role_bundle
from barakat.permissions import (
	NATIVE_REPORT_MODULES,
	PERSONA_ROLE_BUNDLES,
	report_allowed_roles,
)
from barakat.persona_matrix import (
	MODULE_DOCTYPES,
	PERSONA_MATRIX,
	READ_ONLY_DOCTYPES,
	SETUP_WALKTHROUGH_DOCTYPES,
	SHARED_PICKER_READS,
	TILL_REQUIRED_READS,
)
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
	"""(may_read, may_write) for this doctype under the persona's matrix row.

	WRITE is bounded strictly by the doctypes a module OWNS. READ additionally allows
	the module's declared SHARED_PICKER_READS — a form legitimately reads the doctypes
	its pickers list, and the proxy widens its own route gate for exactly the same
	reason (`viewAny` in middleware/permission.ts). Declared, per module, and read-only:
	the widening cannot become a write.
	"""
	may_read = may_write = False
	for module, level in PERSONA_MATRIX[persona].items():
		if level == "none":
			continue
		if doctype in MODULE_DOCTYPES.get(module, ()):
			may_read = True
			if level == "write":
				may_write = True
		elif doctype in SHARED_PICKER_READS.get(module, ()):
			may_read = True
	return may_read, may_write


class PersonaMatchesMatrix(FrappeTestCase):
	def test_no_persona_exceeds_its_matrix(self):
		# Collected, not asserted per-item: stopping at the first violation hides the
		# rest, and these are only worth reviewing as a complete list.
		violations = []
		for persona in PERSONA_ROLE_BUNDLES:
			for doctype, perms in effective_perms(persona).items():
				if doctype in ALLOWED_EXTRA.get(persona, set()):
					continue
				may_read, may_write = _matrix_allows(persona, doctype)
				if not may_read:
					violations.append(
						f"{persona} reaches {doctype} ({sorted(perms)}) "
						f"with no matrix module granting it"
					)
				elif perms & WRITE_PERMS and not may_write:
					violations.append(
						f"{persona} WRITES {doctype} ({sorted(perms & WRITE_PERMS)}) "
						f"but its matrix says read-only"
					)
		self.assertEqual(violations, [], "\n  " + "\n  ".join(violations))

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
					if level == "write" and doctype not in READ_ONLY_DOCTYPES:
						self.assertTrue(
							granted & WRITE_PERMS,
							f"{persona} has {module}: write but no write on {doctype}",
						)

	def test_system_generated_doctypes_are_never_writable(self):
		"""ERPNext writes these itself on submit. A direct write grant is a bug even
		when the persona's module says write — see READ_ONLY_DOCTYPES."""
		for persona in PERSONA_ROLE_BUNDLES:
			effective = effective_perms(persona)
			for doctype in READ_ONLY_DOCTYPES:
				granted = effective.get(doctype, set())
				self.assertEqual(
					granted & WRITE_PERMS,
					set(),
					f"{persona} can write {doctype}, which ERPNext generates",
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

	def test_setup_walkthrough_is_readable(self):
		"""The setup page must be completable by the persona that owns settings.

		This exists because the other guards iterate MODULE_DOCTYPES, so a doctype in
		NO module is invisible to them. `Cost Center` was exactly that: step 15 of the
		walkthrough, listed by no module, so the company-accounts page told a Manager
		"1 account still needs confirmation" with no way to clear it. Found by driving
		the real UI on osa, not by any test — hence this one.
		"""
		effective = effective_perms("Manager")
		for doctype in SETUP_WALKTHROUGH_DOCTYPES:
			if not frappe.db.exists("DocType", doctype):
				continue
			self.assertTrue(
				effective.get(doctype, set()) & {"read", "select"},
				f"Manager cannot read {doctype}; the setup walkthrough will stall on it",
			)

	def test_native_reports_pass_both_query_report_gates(self):
		"""Every persona whose matrix grants the module must be able to RUN the report.

		`frappe.desk.query_report.run` gates twice and independently, and a DocPerm only
		answers one of them:
		  gate 1  the report's own role allow-list (`Report.is_permitted`), which a
		          `Custom Role` REPLACES rather than extends;
		  gate 2  `has_permission(ref_doctype, "report")`.

		Measured on the osa test site 2026-07-30: the Manager failed BOTH for Trial
		Balance while its matrix row said `finance: write`, and the AP rendered the 403
		as "No account movements in the selected period" — a Manager reading that
		concludes the books are empty, not that they were denied.
		"""
		for report, modules in NATIVE_REPORT_MODULES.items():
			if not frappe.db.exists("Report", report):
				continue
			ref_doctype = frappe.db.get_value("Report", report, "ref_doctype")

			# Resolved exactly as Report.is_permitted does: a Custom Role wins outright.
			custom = frappe.db.get_value("Custom Role", {"report": report}, "name")
			allowed = set(
				frappe.get_all(
					"Has Role",
					filters={"parent": custom or report},
					pluck="role",
				)
			)

			for persona, row in PERSONA_MATRIX.items():
				if all(row[module] == "none" for module in modules):
					continue
				bundle = set(persona_role_bundle(persona))
				self.assertTrue(
					bundle & allowed,
					f"{persona} may see {report} per the matrix but holds none of its "
					f"allowed roles {sorted(allowed)} -- gate 1 will 403",
				)
				self.assertIn(
					"report",
					effective_perms(persona).get(ref_doctype, set()),
					f"{persona} has no `report` permission on {ref_doctype} -- gate 2 "
					f"will 403 on {report}",
				)

	def test_a_custom_role_never_drops_the_reports_native_roles(self):
		"""`is_permitted` REPLACES the allow-list when a Custom Role exists.

		So every role ERPNext shipped on the report has to be carried across, or the
		accounts staff (and any owner relying on those roles) silently lose the report
		the moment we grant it to a persona.
		"""
		for report in NATIVE_REPORT_MODULES:
			if not frappe.db.exists("Report", report):
				continue
			native = set(frappe.get_all("Has Role", filters={"parent": report}, pluck="role"))
			computed = set(report_allowed_roles(report, native))
			self.assertTrue(
				native <= computed,
				f"{report}: Custom Role would drop {sorted(native - computed)}",
			)

	def test_every_persona_can_actually_log_in(self):
		"""Every persona must resolve to a System User, or login is impossible.

		The gateway's verify_user_credentials logs into the tenant and accepts ONLY the
		response "Logged In". Frappe returns "No App" for a Website User, so the
		gateway reports "email or password is incorrect" — with a perfectly correct
		password. `User.user_type` is DERIVED: System User iff any held role has
		desk_access=1.

		Regression guard for 2026-07-29, when all 40 generated roles were minted with
		desk_access=0 and Branch Supervisor, Cashier and HR were locked out of the
		admin panel entirely. desk_access is not a security control (the DocPerms are);
		it is the switch that decides whether a persona can sign in.
		"""
		for persona in PERSONA_ROLE_BUNDLES:
			bundle = persona_role_bundle(persona)
			self.assertTrue(bundle, f"{persona} resolves to no roles on this site")
			desk = frappe.get_all(
				"Role",
				filters={"name": ("in", list(bundle)), "desk_access": 1},
				pluck="name",
			)
			self.assertTrue(
				desk,
				f"{persona} holds no role with desk_access -> its users become "
				f"Website Users and CANNOT LOG IN",
			)

	def test_persona_users_are_system_users(self):
		"""The same thing, checked against the users that actually exist here."""
		for emp in frappe.get_all(
			"Employee",
			filters={"custom_role_preset": ("in", sorted(PERSONA_ROLE_BUNDLES)), "user_id": ("is", "set")},
			fields=["user_id", "custom_role_preset"],
		):
			email = (emp.user_id or "").strip()
			if not email or email == "Administrator" or not frappe.db.exists("User", email):
				continue
			self.assertEqual(
				frappe.db.get_value("User", email, "user_type"),
				"System User",
				f"{email} ({emp.custom_role_preset}) is not a System User and cannot log in",
			)
