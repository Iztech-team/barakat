"""Let the tills a shop has been selling on for months onto the tills board.

Until this release a `Presence Till` row was only ever useful to wifi attendance, and a
shop that had not switched that on got no further than the row: `request_join` creates
it and then answers `off`, so every one of those shops is already carrying a set of
Pending tills that nobody has ever been asked to approve.

The tills board makes those rows matter. Without this patch an existing customer's first
sight of the feature is a page of machines waiting for permission they were never asked
for - a chore invented by an upgrade, which is the kind of thing that gets a release
blamed for breaking something.

So a till whose POS Profile has actually submitted a sale is approved here. That is not a
weakening of the gate: a machine that has been taking this shop's money is a machine the
shop already trusts, and there is no stronger evidence available. A profile that has
never sold gets nothing, because it is indistinguishable from a stale row left by a PC
that was reimaged or a profile somebody created and abandoned.

Two things are deliberately NOT done.

Suspended and Retired rows are left exactly as they are. Somebody put them there on
purpose, and a migration that quietly undoes a human decision is worse than a chore.

No key is issued. Approval only means a till may collect one, and collecting is still the
till asking under its own login - so this patch cannot hand credentials to a machine that
is not there to ask for them.
"""

import frappe


def execute():
	pending = frappe.get_all(
		"Presence Till",
		filters={"status": "Pending"},
		fields=["name", "pos_profile"],
	)
	if not pending:
		return

	# One query for every profile that has ever sold, rather than one per till. A site
	# with a hundred tills is not unusual and a patch runs inside the migrate lock.
	sold = {
		profile
		for profile in frappe.get_all(
			"POS Invoice",
			filters={
				"docstatus": 1,
				"pos_profile": ("in", [t.pos_profile for t in pending]),
			},
			pluck="pos_profile",
			distinct=True,
			limit_page_length=0,
		)
		if profile
	}
	if not sold:
		return

	for till in pending:
		if till.pos_profile not in sold:
			continue
		frappe.db.set_value(
			"Presence Till",
			till.name,
			{
				"status": "Active",
				# Named rather than blamed on whoever ran the migration. A manager
				# looking at this row later should be able to tell that nobody chose it.
				"approved_by": "Administrator",
				"approved_at": frappe.utils.now_datetime(),
			},
			update_modified=False,
		)

	frappe.db.commit()
