"""Rename exact-duplicate tier names inside a Loyalty Program.

Two collection rules with the same `tier_name` are legal in ERPNext and fatal to
the POS: it keys its local tier table on (site_url, program, tier_name), writes
every program in one transaction, and treats the resulting UNIQUE failure as
non-retryable — so one duplicate takes a till's whole loyalty sync down for good.

Renames rather than deletes. The duplicate row carries its own `min_spent` and
`collection_factor`; dropping it would quietly change what customers earn.

Idempotent: after a run there are no exact repeats left, so a second run renames
nothing. Expected to be a no-op on every current site — it exists for duplicates
created before the save-time guard shipped, and for sites restored from an older
backup.
"""

import frappe

from barakat.loyalty_tier_names import resolve_exact_duplicates


def execute():
    if not frappe.db.table_exists("Loyalty Program Collection"):
        return  # fresh site: the child table syncs after patches run

    parents = frappe.db.sql_list(
        """select distinct parent from `tabLoyalty Program Collection`
           where parenttype = 'Loyalty Program'"""
    )
    for parent in parents:
        rows = frappe.db.sql(
            """select name, tier_name from `tabLoyalty Program Collection`
               where parent = %s and parenttype = 'Loyalty Program'
               order by idx, name""",
            parent,
            as_dict=True,
        )
        for index, new_name in resolve_exact_duplicates(
            [r.tier_name for r in rows]
        ).items():
            row = rows[index]
            # Written straight to the child row: re-saving the parent would run
            # the Loyalty Program validation mid-migration, and `modified` is left
            # alone because the POS re-fetches every program on each pull anyway.
            frappe.db.set_value(
                "Loyalty Program Collection",
                row.name,
                "tier_name",
                new_name,
                update_modified=False,
            )
            print(f"{parent}: {row.tier_name!r} -> {new_name!r}")
    frappe.db.commit()
