"""Remove the now-unused User Permission DocPerm from `Barakat Staff Manager`.

The role was granted read/write/create/delete on User Permission for a staff-create
flow that never actually writes User Permission rows (verified across barakat, proxy
and AP). `add_permission` on migrate only ever ADDS, so dropping the grant from the
source dict (barakat/setup/install.py) does not remove it from sites already
migrated — this patch does.

See docs/superpowers/specs/2026-07-22-close-internal-escalation-design.md.
"""

import frappe

from barakat.permissions import STAFF_MANAGER_ROLE


def execute():
    frappe.db.delete(
        "Custom DocPerm",
        {"role": STAFF_MANAGER_ROLE, "parent": "User Permission"},
    )
    frappe.clear_cache(doctype="User Permission")
