import json
import frappe
from frappe import _


@frappe.whitelist()
def commission_units(item_code: str, warehouse: str, epcs) -> dict:
	"""
	Bring tagged units into stock. `epcs` is a JSON list (or list) of tag EPC hex
	strings; each becomes a Serial No whose name == the EPC. Tag-first: this is the
	moment the unit enters ERPNext. Idempotent per EPC.
	"""
	if isinstance(epcs, str):
		epcs = json.loads(epcs)
	epcs = [str(e).strip().upper() for e in (epcs or []) if str(e).strip()]
	if not item_code or not warehouse:
		frappe.throw(_("item_code and warehouse are required."))
	if not epcs:
		frappe.throw(_("No EPCs supplied."))

	item = frappe.db.get_value(
		"Item", item_code, ["has_serial_no", "stock_uom"], as_dict=True
	)
	if not item:
		frappe.throw(_("Item {0} not found.").format(item_code))
	if not item.has_serial_no:
		frappe.throw(_("Item {0} is not a serialized item.").format(item_code))
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Warehouse {0} not found.").format(warehouse))

	existing = [e for e in epcs if frappe.db.exists("Serial No", e)]
	new = [e for e in epcs if e not in existing]

	stock_entry_name = None
	if new:
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"purpose": "Material Receipt",
			"items": [{
				"item_code": item_code,
				"qty": len(new),
				"uom": item.stock_uom,
				"t_warehouse": warehouse,
				# Mirror the POS's proven outward pattern, inbound: ERPNext builds the
				# Serial-and-Batch-Bundle + Serial Nos from these on submit.
				"use_serial_batch_fields": 1,
				"serial_no": "\n".join(new),
			}],
		})
		se.insert(ignore_permissions=True)
		se.submit()
		stock_entry_name = se.name
		frappe.db.commit()

	return {"created": new, "already": existing, "stock_entry": stock_entry_name}


@frappe.whitelist()
def decommission_units(epcs) -> dict:
	"""
	Retire tags: issue the given serials OUT of stock (Material Issue), so a tag
	that is being re-burned to a new item doesn't leave its OLD Serial No behind as
	orphaned Active stock. `epcs` is a JSON list (or list) of tag EPC hex strings.
	Idempotent: skips EPCs that aren't a Serial No or are already out of stock.
	"""
	if isinstance(epcs, str):
		epcs = json.loads(epcs)
	epcs = [str(e).strip().upper() for e in (epcs or []) if str(e).strip()]
	if not epcs:
		return {"issued": [], "skipped": [], "stock_entries": []}

	issued, skipped = [], []
	# Group the in-stock serials by (item_code, warehouse) — one Material Issue each.
	groups: dict = {}
	for e in epcs:
		if not frappe.db.exists("Serial No", e):
			skipped.append(e)
			continue
		sn = frappe.db.get_value("Serial No", e, ["item_code", "warehouse"], as_dict=True)
		if not sn or not sn.warehouse:
			skipped.append(e)  # already out of stock / delivered
			continue
		groups.setdefault((sn.item_code, sn.warehouse), []).append(e)

	stock_entries = []
	for (item_code, warehouse), serials in groups.items():
		uom = frappe.db.get_value("Item", item_code, "stock_uom")
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Issue",
			"purpose": "Material Issue",
			"items": [{
				"item_code": item_code,
				"qty": len(serials),
				"uom": uom,
				"s_warehouse": warehouse,
				"use_serial_batch_fields": 1,
				"serial_no": "\n".join(serials),
			}],
		})
		se.insert(ignore_permissions=True)
		se.submit()
		stock_entries.append(se.name)
		issued.extend(serials)

	if issued:
		frappe.db.commit()

	return {"issued": issued, "skipped": skipped, "stock_entries": stock_entries}
