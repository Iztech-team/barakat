"""Normalise Customer company/branch markers after the Data -> Link conversion.

Two reasons this must run:

1. A row whose marker matches no Company/Branch is now an invalid Link. It stays
   readable, but the next save of that Customer throws LinkValidationError. We do
   NOT blank such values — guessing a tenant is worse than a visible gap — we count
   and report them so the rollout has evidence.
2. A BLANK marker is visible to EVERYONE. Frappe emits
   `ifnull(field,'')='' or field in (...)` unless `apply_strict_user_permissions` is
   on, so an empty company defeats the whole boundary. We fill blanks only when the
   site has exactly one company, where the answer is unambiguous.

Idempotent: a second run matches everything and changes nothing.

See docs/superpowers/specs/2026-07-23-customer-company-scoping-design.md
"""

import frappe


def _normalise(fieldname, valid_values, fill_value):
	matched = filled = unmatched = 0
	unmatched_examples = []

	for row in frappe.get_all("Customer", fields=["name", fieldname]):
		value = (row.get(fieldname) or "").strip()

		if value and value in valid_values:
			matched += 1
		elif not value and fill_value:
			frappe.db.set_value(
				"Customer", row.name, fieldname, fill_value, update_modified=False
			)
			filled += 1
		elif value:
			unmatched += 1
			if len(unmatched_examples) < 5:
				unmatched_examples.append(f"{row.name}={value!r}")

	print(
		f"[barakat] Customer.{fieldname}: matched={matched} blanks_filled={filled} "
		f"unmatched={unmatched}"
	)
	if unmatched_examples:
		print(f"[barakat] Customer.{fieldname} unmatched examples: {unmatched_examples}")


def execute():
	companies = set(frappe.get_all("Company", pluck="name"))
	branches = set(frappe.get_all("Branch", pluck="name"))

	# Only safe to infer when there is exactly one company on the site.
	sole_company = next(iter(companies)) if len(companies) == 1 else None
	if not sole_company and companies:
		print(
			f"[barakat] {len(companies)} companies on this site - blank Customer "
			f"companies are left alone and reported, not guessed."
		)

	_normalise("custom_company", companies, sole_company)
	# Branch blanks are the norm and there is no Branch user permission in play,
	# so never fill them - only report values that match no Branch.
	_normalise("custom_branch", branches, None)
