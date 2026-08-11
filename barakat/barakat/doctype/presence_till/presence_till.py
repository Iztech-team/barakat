import frappe
from frappe import _
from frappe.model.document import Document


class PresenceTill(Document):
	def validate(self):
		"""Branch and company are read from the Branch record, never from the caller.

		A till labelled with the wrong branch puts one person in two places at once and
		makes their attendance nonsense, so neither value may come from whatever the
		enrolling POS happens to send. Both are overwritten here on every save.

		The lookup goes through `Branch POS Profile`, the child table on `Branch` that
		assigns profiles to branches - a POS Profile carries no branch of its own. The
		company comes from the same Branch record's `custom_pos_company`, which is the
		marker the tenant boundary already binds to.
		"""

		branch = frappe.db.get_value(
			"Branch POS Profile",
			{"pos_profile": self.pos_profile, "parenttype": "Branch"},
			"parent",
		)
		if not branch:
			frappe.throw(
				_("POS Profile {0} is not assigned to any branch.").format(
					self.pos_profile
				)
			)

		company = frappe.db.get_value("Branch", branch, "custom_pos_company")
		if not company:
			frappe.throw(
				_("Branch {0} has no company set, so presence cannot be scoped.").format(
					branch
				)
			)

		self.branch = branch
		self.custom_company = company
