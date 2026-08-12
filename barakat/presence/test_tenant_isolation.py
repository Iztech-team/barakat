"""One shop must never see another shop's presence data.

This is the most important test in the feature. On 2026-08-05 `Contact` and `Item Price`
leaked across every tenant on an 8-company production site, and the reason nobody caught
it was that no test ever asked "can this persona actually see rows it should not?" —
registration was there, scoping was not, and the two look identical from the code.

Two rules from that fix are what this exercises:

  - The boundary is one `Company` User Permission, and Frappe binds it by walking the
    queried doctype's Link fields. A doctype with no `Link -> Company` field comes back
    completely unscoped.
  - `frappe.get_list`, never `frappe.get_all`. `get_all` ignores permissions by design,
    so it would "prove" a leak on a healthy boundary and prove nothing on a broken one.
    The original bug report's own repro made exactly that mistake.

Every assertion is paired with a control that is known to be scoped, in the same call
shape and as the same user. Without a control, "saw 1 row" might mean the boundary
works or might mean the query was broken.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

OTHER_COMPANY = "Presence Isolation Test Co"
OTHER_ABBR = "ZPIC"
OTHER_BRANCH = "Presence Isolation Test Branch"
SCOPED_USER = "presence-isolation@example.com"

MINE_KEY = "isolation-mine"
THEIRS_KEY = "isolation-theirs"


class TestPresenceTenantIsolation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		cls.mine = frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.my_branch = cls._ensure_branch("Presence Isolation Mine", cls.mine)

		cls.theirs = cls._ensure_company()
		cls.their_branch = cls._ensure_branch(OTHER_BRANCH, cls.theirs)

		cls._ensure_user()
		cls._ensure_permission()

		cls._device(cls.mine, MINE_KEY)
		cls._device(cls.theirs, THEIRS_KEY)
		cls._sighting(cls.mine, cls.my_branch, MINE_KEY)
		cls._sighting(cls.theirs, cls.their_branch, THEIRS_KEY)

	@classmethod
	def tearDownClass(cls):
		"""Clean up after ourselves.

		`barakat/api/test_company.py` leaves its fixture Company behind, which makes the
		whole app suite fail on any second run. Not repeating that here.
		"""
		frappe.set_user("Administrator")
		frappe.db.delete("Presence Sighting", {"device_key": ("in", [MINE_KEY, THEIRS_KEY])})
		frappe.db.delete("Presence Device", {"device_key": ("in", [MINE_KEY, THEIRS_KEY])})
		frappe.db.delete("User Permission", {"user": SCOPED_USER})
		for branch in ("Presence Isolation Mine", OTHER_BRANCH):
			if frappe.db.exists("Branch", branch):
				frappe.delete_doc("Branch", branch, force=True, ignore_permissions=True)
		if frappe.db.exists("Company", OTHER_COMPANY):
			frappe.delete_doc("Company", OTHER_COMPANY, force=True, ignore_permissions=True)
		if frappe.db.exists("User", SCOPED_USER):
			frappe.delete_doc("User", SCOPED_USER, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	# ---------------------------------------------------------------- fixtures

	@classmethod
	def _ensure_company(cls):
		if frappe.db.exists("Company", OTHER_COMPANY):
			return OTHER_COMPANY
		reference = frappe.db.get_value(
			"Company", cls.mine, ["default_currency", "country"], as_dict=True
		)
		return frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": OTHER_COMPANY,
				"abbr": OTHER_ABBR,
				"default_currency": reference.default_currency or "ILS",
				"country": reference.country or "United States",
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _ensure_branch(cls, name, company):
		if not frappe.db.exists("Branch", name):
			frappe.get_doc(
				{"doctype": "Branch", "branch": name, "custom_pos_company": company}
			).insert(ignore_permissions=True)
		return name

	@classmethod
	def _ensure_user(cls):
		"""A staff-shaped account: allowed by role, restricted by User Permission.

		The roles matter more than they look. `_caller_is_tenant_scoped` treats anyone
		holding `System Manager` as an owner and stands the whole boundary down, so a
		test user with System Manager would sail past every assertion below and prove
		nothing. It has to be the generated reader roles - allowed to read presence by
		role, and narrowed to one company by the User Permission. That is the exact
		shape of a real staff login, which is the population the boundary protects.
		"""
		if not frappe.db.exists("User", SCOPED_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": SCOPED_USER,
					"first_name": "Presence Isolation",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		user = frappe.get_doc("User", SCOPED_USER)
		wanted = (
			"Barakat Attendance Reader",
			"Barakat Staff Reader",
			"Barakat Settings Reader",
		)
		held = {row.role for row in user.roles}
		for role in wanted:
			if frappe.db.exists("Role", role) and role not in held:
				user.append("roles", {"role": role})
		# `User.roles` is permlevel 1. A plain save silently drops the rows unless the
		# caller is System Manager - which Administrator is, but be explicit anyway so
		# this does not become a mystery if it is ever run as somebody else.
		user.save(ignore_permissions=True)

		assert "System Manager" not in {r.role for r in user.roles}, (
			"the fixture user must not hold System Manager, or the boundary stands down"
		)

	@classmethod
	def _ensure_permission(cls):
		"""Restrict the user to OUR company. Their company must become invisible."""
		if frappe.db.exists(
			"User Permission",
			{"user": SCOPED_USER, "allow": "Company", "for_value": cls.mine},
		):
			return
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": SCOPED_USER,
				"allow": "Company",
				"for_value": cls.mine,
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _device(cls, company, key):
		frappe.db.delete("Presence Device", {"device_key": key})
		frappe.get_doc(
			{
				"doctype": "Presence Device",
				"custom_company": company,
				"device_key": key,
				"display_suffix": key[-4:],
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _sighting(cls, company, branch, key):
		frappe.get_doc(
			{
				"doctype": "Presence Sighting",
				"custom_company": company,
				"branch": branch,
				"device_key": key,
				"event": "appeared",
				"server_time": "2026-08-11 08:00:00",
			}
		).insert(ignore_permissions=True)

	# ---------------------------------------------------------------- the control

	def test_the_control_a_scoped_user_sees_only_their_own_company(self):
		"""Proves the User Permission is actually in force for this session.

		Frappe binds a Company User Permission to the Company doctype's own `name`, so
		a scoped caller sees exactly one company. If this fails, every assertion below
		is meaningless and the fault is the fixture, not the presence code.
		"""
		frappe.set_user(SCOPED_USER)
		try:
			seen = frappe.get_list("Company", pluck="name", limit=0)
		finally:
			frappe.set_user("Administrator")

		total = frappe.get_all("Company", pluck="name")

		self.assertGreaterEqual(len(total), 2, "fixture did not create a second company")
		self.assertEqual(seen, [self.mine])

	# ---------------------------------------------------------------- the real tests

	def test_a_scoped_user_cannot_list_another_companys_devices(self):
		frappe.set_user(SCOPED_USER)
		try:
			seen = frappe.get_list(
				"Presence Device",
				filters={"device_key": ("in", [MINE_KEY, THEIRS_KEY])},
				pluck="device_key",
				limit=0,
			)
		finally:
			frappe.set_user("Administrator")

		self.assertIn(MINE_KEY, seen)
		self.assertNotIn(THEIRS_KEY, seen, "another company's device is visible")

	def test_a_scoped_user_cannot_list_another_companys_sightings(self):
		frappe.set_user(SCOPED_USER)
		try:
			seen = frappe.get_list(
				"Presence Sighting",
				filters={"device_key": ("in", [MINE_KEY, THEIRS_KEY])},
				pluck="device_key",
				limit=0,
			)
		finally:
			frappe.set_user("Administrator")

		self.assertIn(MINE_KEY, seen)
		self.assertNotIn(THEIRS_KEY, seen, "another company's sighting is visible")

	def test_a_scoped_user_cannot_read_another_companys_device_directly(self):
		"""Listing and reading are separate gates. A filtered list is not enough."""
		theirs = frappe.db.get_value("Presence Device", {"device_key": THEIRS_KEY}, "name")
		self.assertTrue(theirs, "fixture device missing")

		frappe.set_user(SCOPED_USER)
		try:
			allowed = frappe.has_permission("Presence Device", "read", doc=theirs)
		finally:
			frappe.set_user("Administrator")

		self.assertFalse(allowed, "another company's device is readable by name")

	def test_a_scoped_user_can_still_read_their_own(self):
		"""The other half. A boundary that hides everything is not a boundary."""
		mine = frappe.db.get_value("Presence Device", {"device_key": MINE_KEY}, "name")

		frappe.set_user(SCOPED_USER)
		try:
			allowed = frappe.has_permission("Presence Device", "read", doc=mine)
		finally:
			frappe.set_user("Administrator")

		self.assertTrue(allowed, "the boundary is hiding the caller's own data")

	def test_a_blank_company_row_is_visible_to_everyone(self):
		"""This is why `custom_company` is mandatory, and it is not a hypothetical.

		Frappe emits `ifnull(field,'')='' or field in (...)` for a User Permission,
		so a row with a BLANK marker is visible to every tenant. That was half of the
		2026-08-05 Contact leak, and no amount of scoping configuration fixes it.

		The row here has to be forced in with raw SQL precisely because the doctype
		refuses to create one - which is the protection. This test asserts the danger
		is real so that nobody ever relaxes `reqd` thinking the scope layer has it
		covered. It does not.
		"""
		blank_key = "isolation-blank"
		frappe.db.sql(
			"""INSERT INTO `tabPresence Device`
				(name, owner, creation, modified, modified_by, docstatus, idx,
				 custom_company, device_key)
				VALUES (%s, 'Administrator', NOW(), NOW(), 'Administrator', 0, 0, '', %s)""",
			(blank_key, blank_key),
		)
		frappe.db.commit()

		try:
			frappe.set_user(SCOPED_USER)
			try:
				seen = frappe.get_list(
					"Presence Device",
					filters={"device_key": ("in", [blank_key, THEIRS_KEY])},
					pluck="device_key",
					limit=0,
				)
			finally:
				frappe.set_user("Administrator")

			self.assertIn(
				blank_key,
				seen,
				"a blank marker is expected to be visible - if this ever stops being "
				"true, Frappe changed and this comment needs revisiting",
			)
			self.assertNotIn(THEIRS_KEY, seen, "a filled marker must still be scoped")
		finally:
			frappe.db.delete("Presence Device", {"device_key": blank_key})
			frappe.db.commit()

	def test_the_doctype_refuses_to_create_a_blank_company_row(self):
		"""The protection itself: the dangerous row above cannot be made normally."""
		with self.assertRaises(frappe.MandatoryError):
			frappe.get_doc(
				{
					"doctype": "Presence Device",
					"device_key": "isolation-should-not-exist",
				}
			).insert(ignore_permissions=True)


class TestPresenceScopeWiring(FrappeTestCase):
	"""The machinery underneath, asserted directly.

	The isolation tests above prove the behaviour on two companies. These prove the
	wiring for every presence doctype, including the ones too expensive to build a
	second tenant's fixtures for.
	"""

	PRESENCE_DOCTYPES = (
		"Presence Settings",
		"Presence Till",
		"Presence Device",
		"Employee Device",
		"Presence Sighting",
		"Presence Session",
		"Presence Live Device",
	)

	def test_every_presence_doctype_resolves_to_the_company_marker(self):
		from barakat.overrides.company_scope import company_field_for

		for doctype in self.PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertEqual(company_field_for(doctype), "custom_company")

	def test_no_presence_doctype_is_declared_company_neutral(self):
		"""Declaring one neutral would be a decision that leaking it is acceptable."""
		from barakat.overrides.company_scope import COMPANY_NEUTRAL_DOCTYPES

		for doctype in self.PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertNotIn(doctype, COMPANY_NEUTRAL_DOCTYPES)

	def test_no_presence_doctype_is_unscopable(self):
		"""`unscopable_block` returns "1=0" for a shop-owned doctype it cannot pin."""
		from barakat.overrides.company_scope import unscopable_block

		for doctype in self.PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertEqual(unscopable_block(SCOPED_USER, doctype), "")

	def test_every_presence_doctype_is_registered_as_guarded(self):
		from barakat.overrides.company_scope import GUARDED_DOCTYPES

		for doctype in self.PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertIn(doctype, GUARDED_DOCTYPES)
