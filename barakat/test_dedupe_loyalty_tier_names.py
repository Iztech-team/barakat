"""On-bench test for the duplicate tier-name repair patch.

Run on a site:
    bench --site <site> run-tests --module barakat.test_dedupe_loyalty_tier_names
Not runnable on the Windows dev box (imports `frappe`).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.patches.dedupe_loyalty_tier_names import execute

PARENT = "ZZ Patch Test Program"


class DedupeLoyaltyTierNames(FrappeTestCase):
    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def _clear(self):
        frappe.db.sql(
            "delete from `tabLoyalty Program Collection` where parent = %s", PARENT
        )
        frappe.db.commit()

    def _insert(self, idx, tier_name, min_spent, collection_factor):
        # Inserted as a document, not raw SQL: the child table still has Frappe's
        # non-null bookkeeping columns (owner, creation, modified, modified_by,
        # docstatus), and get_doc fills them. A hand-written INSERT that omits
        # them fails on MariaDB for reasons unrelated to what this test asserts.
        frappe.get_doc(
            {
                "doctype": "Loyalty Program Collection",
                "parent": PARENT,
                "parenttype": "Loyalty Program",
                "parentfield": "collection_rules",
                "idx": idx,
                "tier_name": tier_name,
                "min_spent": min_spent,
                "collection_factor": collection_factor,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()

    def _rows(self):
        return frappe.db.sql(
            """select tier_name, min_spent, collection_factor
               from `tabLoyalty Program Collection`
               where parent = %s order by idx""",
            PARENT,
            as_dict=True,
        )

    def test_duplicate_is_renamed_and_keeps_its_numbers(self):
        self._insert(1, "vip vip", 0, 656567)
        self._insert(2, "vip vip", 2000, 50)

        execute()

        rows = self._rows()
        self.assertEqual([r.tier_name for r in rows], ["vip vip", "vip vip (2)"])
        # The renamed row keeps its own threshold and factor — a rename must not
        # change what customers earn.
        self.assertEqual(rows[1].min_spent, 2000)
        self.assertEqual(rows[1].collection_factor, 50)

    def test_second_run_changes_nothing(self):
        self._insert(1, "vip vip", 0, 10)
        self._insert(2, "vip vip", 2000, 5)

        execute()
        after_first = self._rows()
        execute()

        self.assertEqual(self._rows(), after_first)

    def test_distinct_names_are_left_alone(self):
        self._insert(1, "Bronze", 0, 10)
        self._insert(2, "Gold", 2000, 5)

        execute()

        self.assertEqual([r.tier_name for r in self._rows()], ["Bronze", "Gold"])
