"""Unit tests for the stale-PIN backfill.

Only `rows_to_clear` is exercised: it is the whole decision, and it is pure, so it
can be tested without a site. The three guards it sits behind (petromall, a missing
column, idempotence) are asserted against the module source and by re-running the
decision over its own result.
"""

import unittest
from pathlib import Path

from barakat.patches.clear_pos_pin_for_non_pos_roles import SKIP_SITES, rows_to_clear


def _row(name, preset, pin):
	return {"name": name, "custom_role_preset": preset, "custom_pos_pin": pin}


class RowsToClear(unittest.TestCase):
	def test_clears_a_pin_on_a_non_till_role(self):
		rows = [_row("EMP-1", "HR", "1234")]
		self.assertEqual(rows_to_clear(rows), ["EMP-1"])

	def test_clears_every_non_till_persona(self):
		rows = [
			_row("EMP-1", "HR", "1111"),
			_row("EMP-2", "Accountant", "2222"),
			_row("EMP-3", "Inventory Keeper", "3333"),
		]
		self.assertEqual(rows_to_clear(rows), ["EMP-1", "EMP-2", "EMP-3"])

	def test_leaves_till_personas_alone(self):
		rows = [
			_row("EMP-1", "Manager", "1111"),
			_row("EMP-2", "Branch Supervisor", "2222"),
			_row("EMP-3", "Cashier", "3333"),
		]
		self.assertEqual(rows_to_clear(rows), [])

	def test_clears_a_blank_or_unknown_preset(self):
		# The 129 imported employees on bom sit at a blank preset. None has a PIN
		# today; if one is ever given one from the desk, it is not a till credential.
		rows = [
			_row("EMP-1", "", "1111"),
			_row("EMP-2", None, "2222"),
			_row("EMP-3", "Kashier", "3333"),
		]
		self.assertEqual(rows_to_clear(rows), ["EMP-1", "EMP-2", "EMP-3"])

	def test_ignores_employees_with_no_pin(self):
		# The overwhelming majority. Nothing to clear means no write, which is what
		# keeps a second run free.
		rows = [
			_row("EMP-1", "HR", ""),
			_row("EMP-2", "HR", None),
			_row("EMP-3", "HR", "   "),
		]
		self.assertEqual(rows_to_clear(rows), [])

	def test_is_idempotent(self):
		# Re-running over the state the patch leaves behind finds nothing.
		rows = [_row("EMP-1", "HR", "1234"), _row("EMP-2", "Cashier", "5678")]
		cleared = set(rows_to_clear(rows))
		after = [
			_row(r["name"], r["custom_role_preset"], "" if r["name"] in cleared else r["custom_pos_pin"])
			for r in rows
		]
		self.assertEqual(rows_to_clear(after), [])

	def test_survives_rows_missing_the_fields_entirely(self):
		# A site part-way through a fixture sync can return rows without the keys.
		# A patch that throws on one row stops the whole migration.
		self.assertEqual(rows_to_clear([{"name": "EMP-1"}]), [])
		self.assertEqual(
			rows_to_clear([{"name": "EMP-1", "custom_pos_pin": "1234"}]), ["EMP-1"]
		)

	def test_handles_an_empty_site(self):
		self.assertEqual(rows_to_clear([]), [])


class Guards(unittest.TestCase):
	SRC = (
		Path(__file__).parent / "clear_pos_pin_for_non_pos_roles.py"
	).read_text(encoding="utf-8")

	def test_petromall_is_excluded(self):
		self.assertIn("petromall.iztech.net", SKIP_SITES)

	def test_execute_checks_the_site_before_anything_else(self):
		body = self.SRC.split("def execute():")[1]
		site_guard = body.index("SKIP_SITES")
		first_read = body.index("frappe.get_all")
		self.assertLess(site_guard, first_read, "petromall must be refused before any read")

	def test_execute_checks_both_columns_exist(self):
		body = self.SRC.split("def execute():")[1]
		# izdehar has no custom_pos_pin column; reading it would kill the migration.
		self.assertIn('has_column("Employee", "custom_pos_pin")', body)
		# Without the preset there is no way to tell a cashier from an HR clerk.
		self.assertIn('has_column("Employee", "custom_role_preset")', body)

	def test_it_does_not_save_documents(self):
		# doc.save() would fire the persona role re-assertion for every affected
		# employee — a large, unrelated side effect for emptying one field.
		#
		# The module docstring explains that choice and therefore contains the very
		# string being searched for, so only the code after it is examined.
		code = self.SRC.split('"""', 2)[2]
		self.assertNotIn(".save(", code)
		self.assertIn("frappe.db.set_value", code)

	def test_it_never_prints_a_pin(self):
		printed = self.SRC.split("print(")[1]
		self.assertNotIn("custom_pos_pin", printed)


if __name__ == "__main__":
	unittest.main()
