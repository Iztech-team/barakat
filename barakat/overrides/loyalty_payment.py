import frappe
from frappe import _

_PREFIX = "Loyalty Points - "


def _is_loyalty_mode(name: str) -> bool:
	return bool(name) and name.startswith(_PREFIX)


def guard_loyalty_mode_delete(doc, method):
	if _is_loyalty_mode(doc.name):
		frappe.throw(_("The '{0}' payment method is managed by Barakat and cannot be deleted.").format(doc.name))


def guard_loyalty_mode_rename(doc, method):
	# on 'before_rename' Frappe passes old/new via doc? Use a validate-time name check:
	# block renaming AWAY from the managed prefix by comparing to the stored name.
	if doc.get("__islocal"):
		return
	old = frappe.db.get_value("Mode of Payment", doc.name, "mode_of_payment")
	if old and _is_loyalty_mode(old) and doc.mode_of_payment != old:
		frappe.throw(_("The '{0}' payment method is managed by Barakat and cannot be renamed.").format(old))
