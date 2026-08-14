"""The login a till reports under, and the two ways deriving it can go wrong.

A watcher authenticates as a user whose address is built from the till's name. That is
fine until the name is not spellable as an email — which for shops named in Arabic is not
an exception, it is the norm. Frappe refuses the address, approval throws, and the till is
answered 417 by every request it will ever make while the Admin Panel shows it Active.

The second failure is subtler and only appears when the first is fixed: change how an
address is derived and every till already in the field is authenticating with an address
the new rule no longer produces. Deriving is a guess about the past; the till recorded
what it was really given.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import validate_email_address

from barakat.presence import keys

ARABIC = "نقطة بيع الفرع الرئيسي - سبع بركات"
HEBREW = "קופה ראשית - חנות"
ASCII_NAME = "E2E Till 1 - E2E Shop"


def till(name, api_user=None):
	"""Enough of a till for naming: what it is called, and what it already holds."""
	return frappe._dict({"name": name, "api_user": api_user})


class TestPresenceKeys(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		for name in (ARABIC, HEBREW, ASCII_NAME):
			email = keys.user_name_for(till(name))
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		frappe.db.commit()

	# ------------------------------------------------------ the address is spellable

	def test_an_arabic_profile_name_yields_a_valid_login(self):
		"""The bug, exactly. Before the fix this address was Arabic and Frappe refused it."""
		email = keys.user_name_for(till(ARABIC))
		self.assertTrue(
			validate_email_address(email),
			f"a till named in Arabic still cannot be given a login: {email}",
		)

	def test_a_hebrew_profile_name_yields_a_valid_login(self):
		# Same class of failure, and these sites carry a Hebrew chart of accounts too.
		self.assertTrue(validate_email_address(keys.user_name_for(till(HEBREW))))

	def test_an_arabic_named_till_can_actually_be_issued_a_key(self):
		"""The end of the story, not just the address: approval has to complete."""
		credentials = keys.issue_key(till(ARABIC))

		self.assertTrue(credentials["api_key"])
		self.assertTrue(credentials["api_secret"])
		self.assertTrue(frappe.db.exists("User", credentials["user"]))
		# The name is not lost by being unspellable — it stays where a human reads it.
		self.assertIn(
			ARABIC, frappe.db.get_value("User", credentials["user"], "first_name")
		)

	# ------------------------------------------- and every existing till keeps its own

	def test_an_ascii_name_keeps_the_address_it_has_always_had(self):
		"""Pinned to the literal old formula, deliberately.

		Every till in the field authenticates with an address this function produced. A
		tidier scheme applied to all of them would orphan every one of those accounts, so
		the historical output is the specification for names that were always fine.
		"""
		historical = f"presence-till-{ASCII_NAME}@barakat.local".replace(" ", "-").lower()
		self.assertEqual(keys.user_name_for(till(ASCII_NAME)), historical)

	def test_a_name_with_a_dot_is_left_alone_too(self):
		# A dot is legal in an address, so this name never needed rescuing.
		name = "Till 1.0 - Shop"
		self.assertEqual(
			keys.user_name_for(till(name)),
			f"presence-till-{name}@barakat.local".replace(" ", "-").lower(),
		)

	def test_the_same_name_always_gives_the_same_login(self):
		"""Re-approving must find the existing account, never make a second one."""
		self.assertEqual(
			keys.user_name_for(till(ARABIC)), keys.user_name_for(till(ARABIC))
		)

	def test_two_different_unspellable_names_do_not_collide(self):
		"""Two tills sharing one login would each rotate the other's key away."""
		self.assertNotEqual(
			keys.user_name_for(till(ARABIC)), keys.user_name_for(till(HEBREW))
		)

	def test_names_differing_only_late_in_the_string_still_differ(self):
		# A fingerprint of the whole name, not a prefix of it.
		a = keys.user_name_for(till("الفرع الرئيسي - فرع ١"))
		b = keys.user_name_for(till("الفرع الرئيسي - فرع ٢"))
		self.assertNotEqual(a, b)

	# ------------------------------------------------ what the till holds outranks all

	def test_revoke_disables_the_account_the_till_actually_holds(self):
		"""The trap in changing this function at all.

		A till enrolled under the old scheme holds an address the new rule would not
		produce. Revoking by derivation would disable nothing, report success, and leave a
		key working that a manager believes they have just killed.
		"""
		legacy = "presence-till-legacy-address@barakat.local"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": legacy,
				"first_name": "Legacy Till",
				"user_type": "System User",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "User", legacy, force=True, ignore_permissions=True
		)

		keys.revoke(till(ARABIC, api_user=legacy))

		self.assertEqual(
			frappe.db.get_value("User", legacy, "enabled"),
			0,
			"revoke disabled a derived address instead of the one the till holds",
		)

	def test_issuing_again_reuses_the_recorded_account(self):
		"""A reissue rotates the key the watcher is using, not some other account's."""
		first = keys.issue_key(till(ARABIC))
		again = keys.issue_key(till(ARABIC, api_user=first["user"]))

		self.assertEqual(again["user"], first["user"])
		self.assertNotEqual(
			again["api_secret"], first["api_secret"], "the secret was not rotated"
		)

	def test_a_till_with_no_recorded_account_falls_back_to_the_rule(self):
		# The first approval, when there is nothing recorded yet.
		self.assertEqual(
			keys.account_for(till(ARABIC)), keys.user_name_for(till(ARABIC))
		)
