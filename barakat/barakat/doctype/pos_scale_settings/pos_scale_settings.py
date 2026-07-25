import frappe
from frappe import _
from frappe.model.document import Document


class POSScaleSettings(Document):
    def validate(self):
        if not (self.scale_barcode_enabled or self.has_balances):
            return
        uom = self.custom_company and frappe.db.get_value(
            "Company", self.custom_company, "custom_scale_uom"
        )
        if not uom:
            frappe.throw(
                _(
                    "Set the company's Scale/Balance UOM before enabling scale "
                    "barcodes for a branch."
                )
            )
