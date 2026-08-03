"""Tests for the payment-mode cleanup decision.

Pure unit tests — `decide` takes two plain dicts and returns three lists, so the
whole safety argument is checked here without a site, a company, or a migration.
That is deliberate: the risk in this patch is not "does the SQL run", it is
"does it delete a row it should have kept", and that is a decision, not a query.

The shapes below mirror what `plan()` reads:
  owner_by_mode      {mode: the company it belongs to, "" when untagged}
  companies_by_mode  {mode: [company on each accounts row]}
"""

import unittest

from barakat.patches.scope_payment_mode_accounts import decide


class TestDecidePaymentModeCleanup(unittest.TestCase):
	def test_removes_another_shops_row(self):
		"""Ticket 0001-595: `نقدا - سبع بركات` had three neighbours in it."""
		to_delete, kept, shared = decide(
			{"cash-a": "A", "cash-b": "B"},
			{"cash-a": ["A", "B"], "cash-b": ["B"]},
		)
		self.assertEqual(to_delete, [("cash-a", "B")])
		self.assertEqual(kept, [])
		self.assertEqual(shared, [])

	def test_keeps_a_row_when_that_company_has_no_mode_of_its_own(self):
		"""The safety rule.

		B's only payment-mode row is the one sitting on A's mode. Deleting it would
		leave B with no cash mapping at all, so it is reported, not removed.
		"""
		to_delete, kept, shared = decide(
			{"cash-a": "A"},
			{"cash-a": ["A", "B"]},
		)
		self.assertEqual(to_delete, [])
		self.assertEqual(kept, [("cash-a", "B")])

	def test_never_removes_the_owning_companys_own_row(self):
		to_delete, kept, _ = decide({"cash-a": "A"}, {"cash-a": ["A"]})
		self.assertEqual((to_delete, kept), ([], []))

	def test_untagged_multi_company_mode_is_reported_not_touched(self):
		"""Stock ERPNext's global "Cash" is shared by design — not ours to strip."""
		to_delete, kept, shared = decide(
			{"Cash": "", "cash-a": "A"},
			{"Cash": ["A", "B"], "cash-a": ["A"]},
		)
		self.assertEqual(to_delete, [])
		self.assertEqual(kept, [])
		self.assertEqual(shared, [("Cash", ["A", "B"])])

	def test_untagged_single_company_mode_is_not_reported(self):
		_, _, shared = decide({"Cash": ""}, {"Cash": ["A", "A"]})
		self.assertEqual(shared, [])

	def test_the_backfill_trap(self):
		"""The scenario that makes the safety rule load-bearing.

		The proxy's `backfillModeCompany` tags an untagged mode with the FIRST
		company in its accounts table. So a shared mode holding A, B and C can wake
		up "owned" by A, and B and C suddenly look foreign. B has a mode of its own
		and is safe to unpick; C does not, and must survive.
		"""
		to_delete, kept, _ = decide(
			{"was-shared": "A", "cash-b": "B"},
			{"was-shared": ["A", "B", "C"], "cash-b": ["B"]},
		)
		self.assertEqual(to_delete, [("was-shared", "B")])
		self.assertEqual(kept, [("was-shared", "C")])

	def test_is_idempotent(self):
		"""Re-running over an already-cleaned site plans nothing."""
		owner = {"cash-a": "A", "cash-b": "B"}
		rows = {"cash-a": ["A"], "cash-b": ["B"]}
		self.assertEqual(decide(owner, rows), ([], [], []))

	def test_a_mode_with_no_rows_is_harmless(self):
		self.assertEqual(decide({"cash-a": "A"}, {}), ([], [], []))

	def test_blank_company_rows_are_ignored(self):
		to_delete, kept, shared = decide({"cash-a": "A"}, {"cash-a": ["A", "", None]})
		self.assertEqual((to_delete, kept, shared), ([], [], []))


if __name__ == "__main__":
	unittest.main()
