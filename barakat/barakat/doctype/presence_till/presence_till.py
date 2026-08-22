import frappe
from frappe import _
from frappe.model.document import Document


def resolve_scope(pos_profile):
	"""The branch and company a till reports for, read from the Branch record.

	Never from the caller. A till labelled with the wrong branch puts one person in two
	places at once and makes their attendance nonsense, so neither value may come from
	whatever the enrolling POS happens to send.

	The lookup goes through `Branch POS Profile`, the child table on `Branch` that
	assigns profiles to branches - a POS Profile carries no branch of its own. The
	company comes from the same Branch record's `custom_pos_company`, which is the
	marker the tenant boundary already binds to.

	A function rather than only a method, because two callers need the same answer: the
	till's own save, and the Branch hook that moves a till when its profile is
	reassigned. That hook cannot re-save the whole till - an unrelated validation
	failure there would block a manager's branch edit - so it writes these two fields
	directly, and this is what keeps "what a till's branch is" decided in one place.
	"""

	branch = frappe.db.get_value(
		"Branch POS Profile",
		{"pos_profile": pos_profile, "parenttype": "Branch"},
		"parent",
	)
	if not branch:
		frappe.throw(
			_("POS Profile {0} is not assigned to any branch.").format(pos_profile)
		)

	company = frappe.db.get_value("Branch", branch, "custom_pos_company")
	if not company:
		frappe.throw(
			_("Branch {0} has no company set, so presence cannot be scoped.").format(
				branch
			)
		)

	return branch, company


class PresenceTill(Document):
	def validate(self):
		"""Branch and company are read, never accepted. Overwritten on every save."""
		self.branch, self.custom_company = resolve_scope(self.pos_profile)
