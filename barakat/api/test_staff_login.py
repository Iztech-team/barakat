"""On-bench tests for the staff login-email rename. Run on a site:

	bench --site <site> run-tests --module barakat.api.test_staff_login

Not runnable on the Windows dev box (imports `frappe`).

The case that matters is the FIRST one: before this method existed the panel called
`frappe.client.rename_doc`, and every persona below the shop owner failed on the nested
`Notification Settings` rename that `User.after_rename` performs. So the test acts as a
real Manager persona — not as Administrator, who never had the problem.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.api.staff_login import rename_login

# Addresses no real staff member holds. `.invalid` is reserved by RFC 2606, so these
# can never collide with a live login even if a test leaks a record.
MANAGER = "test-staff-login-manager@barakat.invalid"
STAFF_OLD = "test-staff-login-old@barakat.invalid"
STAFF_NEW = "test-staff-login-new@barakat.invalid"
OUTSIDER = "test-staff-login-outsider@barakat.invalid"
CASHIER = "test-staff-login-cashier@barakat.invalid"
# A login with no Employee record at all — the shape an owner or a service
# account has. Not staff, therefore not the manager's to rename.
ORPHAN = "test-staff-login-orphan@barakat.invalid"

# The persona bundles as `staff_roles.py` grants them. The manager set is what makes
# `User` writable at all; the cashier set deliberately does not include it.
MANAGER_ROLES = ("Barakat Staff Manager", "Barakat Staff Writer")
CASHIER_ROLES = ("Barakat POS Operator",)


class TestRenameLogin(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		assert cls.company, "test site has no Company"
		# A second company, only if the site has one — the cross-shop test is skipped
		# rather than faked on a single-company site.
		cls.other_company = frappe.db.get_value(
			"Company", {"name": ("!=", cls.company)}, "name"
		)

	def setUp(self):
		self._wipe()
		self._make_user(MANAGER, "Login Test Manager", MANAGER_ROLES)
		self._make_employee("Login Test Manager", MANAGER, self.company)
		self._make_user(STAFF_OLD, "Login Test Staff", CASHIER_ROLES)
		self.staff_employee = self._make_employee(
			"Login Test Staff", STAFF_OLD, self.company
		)
		self._make_user(CASHIER, "Login Test Cashier", CASHIER_ROLES)
		self._make_employee("Login Test Cashier", CASHIER, self.company)
		self._make_user(ORPHAN, "Login Test Orphan", CASHIER_ROLES)  # no Employee
		if self.other_company:
			self._make_user(OUTSIDER, "Login Test Outsider", CASHIER_ROLES)
			self._make_employee("Login Test Outsider", OUTSIDER, self.other_company)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		self._wipe()

	# ── fixtures ──────────────────────────────────────────────────────────────
	def _wipe(self):
		frappe.set_user("Administrator")
		for name in (
			"Login Test Manager",
			"Login Test Staff",
			"Login Test Cashier",
			"Login Test Outsider",
		):
			for emp in frappe.get_all("Employee", filters={"employee_name": name}, pluck="name"):
				frappe.delete_doc("Employee", emp, force=1, ignore_permissions=True)
		for email in (MANAGER, STAFF_OLD, STAFF_NEW, OUTSIDER, CASHIER, ORPHAN):
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=1, ignore_permissions=True)
		frappe.db.commit()

	def _make_user(self, email, full_name, roles):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": full_name,
				"send_welcome_email": 0,
				"new_password": "Str0ng-Test-Pass!42",
			}
		).insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user

	def _make_employee(self, full_name, email, company):
		return frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": full_name,
				"employee_name": full_name,
				"company": company,
				"date_of_joining": "2026-01-01",
				"date_of_birth": "1990-01-01",
				"gender": "Male",
				"status": "Active",
				"user_id": email,
			}
		).insert(ignore_permissions=True)

	# ── the fix ───────────────────────────────────────────────────────────────
	def test_a_manager_can_correct_a_staff_login_email(self):
		frappe.set_user(MANAGER)
		result = rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		self.assertEqual(result, {"user": STAFF_NEW, "renamed": True})
		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("User", STAFF_NEW))
		self.assertFalse(
			frappe.db.exists("User", STAFF_OLD),
			"the old address must not survive — it stayed a working key into the shop",
		)

	def test_the_employee_link_follows_the_rename(self):
		frappe.set_user(MANAGER)
		rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Employee", self.staff_employee.name, "user_id"), STAFF_NEW
		)

	def test_the_login_keeps_its_roles_and_password(self):
		# The whole point of renaming rather than re-creating: nothing is reset, so
		# correcting a typo does not force a password reset on the staff member.
		frappe.set_user("Administrator")
		before = frappe.db.sql(
			"select password from `__Auth` where doctype='User' and name=%s and fieldname='password'",
			STAFF_OLD,
		)
		frappe.set_user(MANAGER)
		rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		after = frappe.db.sql(
			"select password from `__Auth` where doctype='User' and name=%s and fieldname='password'",
			STAFF_NEW,
		)
		self.assertTrue(before and before[0][0], "fixture had no password to carry")
		self.assertEqual(before, after)
		self.assertIn(
			CASHIER_ROLES[0], [r.role for r in frappe.get_doc("User", STAFF_NEW).roles]
		)

	def test_the_nested_notification_settings_rename_goes_through(self):
		# This is the exact write that used to throw "You need write permission on
		# Notification Settings ... to rename" and roll the whole thing back.
		frappe.set_user(MANAGER)
		rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Notification Settings", STAFF_NEW))
		self.assertFalse(frappe.db.exists("Notification Settings", STAFF_OLD))

	def test_the_caller_is_not_left_elevated(self):
		# The rename runs as Administrator. If that leaked, every later write in the
		# same request would bypass permissions.
		frappe.set_user(MANAGER)
		rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		self.assertEqual(frappe.session.user, MANAGER)

	def test_the_session_is_restored_even_when_the_rename_fails(self):
		frappe.set_user(MANAGER)
		with self.assertRaises(Exception):
			# CASHIER already exists, so the rename raises from inside the elevated block.
			rename_login(old_email=STAFF_OLD, new_email=CASHIER)

		self.assertEqual(frappe.session.user, MANAGER)

	# ── what it still refuses ─────────────────────────────────────────────────
	def test_a_caller_may_not_rename_their_own_login(self):
		# The rename force-clears that user's sessions, so this would kill the caller
		# mid-request and drop the edit.
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.PermissionError):
			rename_login(old_email=MANAGER, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("User", MANAGER))

	def test_a_persona_without_user_write_is_refused(self):
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("User", STAFF_OLD))

	def test_a_login_with_no_staff_record_is_out_of_reach(self):
		# Runs on every site, unlike the cross-company case below: a user with no
		# Employee is not staff of anyone's shop, so the scope check must refuse it
		# even though the caller holds `User` write.
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.PermissionError):
			rename_login(old_email=ORPHAN, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("User", ORPHAN))

	def test_a_password_may_still_be_set_on_the_renamed_login(self):
		# The panel allows changing the email AND the password in one save, and the
		# proxy applies the password to the NEW name straight after the rename,
		# under the caller's own session. So that write has to stay possible for a
		# manager once the rename has moved the account.
		frappe.set_user(MANAGER)
		rename_login(old_email=STAFF_OLD, new_email=STAFF_NEW)

		renamed = frappe.get_doc("User", STAFF_NEW)
		renamed.new_password = "An0ther-Test-Pass!42"
		renamed.save()

	def test_another_shops_staff_is_out_of_reach(self):
		if not self.other_company:
			self.skipTest("site has only one company")
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.PermissionError):
			rename_login(old_email=OUTSIDER, new_email=STAFF_NEW)

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("User", OUTSIDER))

	def test_an_address_already_in_use_is_refused_as_a_duplicate(self):
		# The proxy maps this onto the login-email field as "already in use", so it
		# must arrive as a duplicate and not as a generic failure.
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.DuplicateEntryError):
			rename_login(old_email=STAFF_OLD, new_email=CASHIER)

	def test_an_unknown_login_is_refused(self):
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.PermissionError):
			# Out of scope before it is missing: an unknown address is not this
			# caller's staff, and the scope check runs first.
			rename_login(old_email="no-such-login@barakat.invalid", new_email=STAFF_NEW)

	def test_a_malformed_address_is_refused(self):
		frappe.set_user(MANAGER)
		with self.assertRaises(Exception):
			rename_login(old_email=STAFF_OLD, new_email="not-an-email")

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("User", STAFF_OLD))

	def test_a_blank_address_is_refused(self):
		frappe.set_user(MANAGER)
		with self.assertRaises(Exception):
			rename_login(old_email=STAFF_OLD, new_email="   ")

	def test_resaving_the_same_address_is_a_no_op(self):
		# The panel re-sends the current email on every save; that must not be an error.
		frappe.set_user(MANAGER)
		self.assertEqual(
			rename_login(old_email=STAFF_OLD, new_email=STAFF_OLD),
			{"user": STAFF_OLD, "renamed": False},
		)
