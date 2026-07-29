"""Is a company abbreviation still free, across the WHOLE site?

## Why this cannot be a proxy-side query

ERPNext enforces `Company.abbr` uniqueness in `validate_abbr` with raw SQL:

	select abbr from tabCompany where name!=%s and abbr=%s

That query has no permission filter — it sees every company on the site, across
every tenant. A proxy-side check cannot reproduce it, because Frappe scopes
`Company` READS by the caller's `Company` User Permission (that permission is the
tenant boundary; see `barakat/api/session_role.py`). A shop owner asking "is BSM
free?" would get an empty result for another tenant's BSM and be told yes, then
watch the create fail anyway. A check that is confidently wrong is worse than no
check, so the lookup runs here, where `frappe.db.exists` bypasses permissions
exactly as ERPNext's own validation does.

## What this deliberately does not return

Only a boolean. Not the company name, not a count, not a suggested alternative.
Any signed-in user who may create a company can learn that *some* company holds a
given abbreviation; they learn nothing about whose it is. That narrow leak is the
price of an honest check, and was accepted in the 2026-07-29 design.
"""

import frappe


@frappe.whitelist()
def is_abbreviation_available(abbr=None):
	"""True when no Company on this site holds `abbr`.

	Mirrors ERPNext's own normalisation (`self.abbr = self.abbr.strip()`) so this
	answer matches what `validate_abbr` will decide at create time. Case is not
	folded: MariaDB's default collation is case-insensitive, so `bsm` and `BSM`
	collide in this lookup exactly as they will in ERPNext's.
	"""
	cleaned = (abbr or "").strip()
	if not cleaned:
		return {"available": False}
	return {"available": not frappe.db.exists("Company", {"abbr": cleaned})}
