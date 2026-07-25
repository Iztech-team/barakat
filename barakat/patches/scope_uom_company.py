"""Scope each company's Units of Measure: custom_company + ' - <Company>' name.

Data-driven off CURRENT references (Item.stock_uom, the Item UOM child table
`UOM Conversion Detail`, and Item Price.uom). Every unit a company uses gets a
scoped COPY and the company's references are repointed onto it; the global
original is left intact (we never rename — see _uom_scope_logic). Idempotent: a
second run finds every unit already scoped and does nothing. petromall is skipped
— it is not a barakat shop.

See docs/superpowers/specs/2026-07-24-uom-company-scoping-design.md
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from barakat.patches._uom_scope_logic import classify_unit, scoped_name

SKIP_SITES = {"petromall.iztech.net"}


def _ensure_uom_company_column():
    """bench migrate runs patches BEFORE fixtures sync, so on a site's first
    migrate the UOM.custom_company column this patch reads does not exist yet
    (caught live on qa-test: OperationalError 1054). Create the field here with
    the exact definition the fixture ships — sync_fixtures later matches it by
    name (UOM-custom_company) and leaves it in place."""
    if frappe.db.has_column("UOM", "custom_company"):
        return
    create_custom_fields(
        {
            "UOM": [
                dict(
                    fieldname="custom_company",
                    label="Company (Barakat)",
                    fieldtype="Link",
                    options="Company",
                    insert_after="enabled",
                    in_list_view=0,
                    in_standard_filter=1,
                )
            ]
        },
        update=True,
    )


def _used_units(company):
    rows = frappe.db.sql(
        """
        select distinct stock_uom as u from `tabItem`
          where custom_company=%(c)s and ifnull(stock_uom,'')!=''
        union
        select distinct d.uom as u from `tabUOM Conversion Detail` d
          join `tabItem` i on i.name=d.parent and d.parenttype='Item'
          where i.custom_company=%(c)s and ifnull(d.uom,'')!=''
        union
        select distinct ip.uom as u from `tabItem Price` ip
          join `tabItem` i on i.name=ip.item_code
          where i.custom_company=%(c)s and ifnull(ip.uom,'')!=''
        """,
        {"c": company},
        as_dict=True,
    )
    return {r.u for r in rows if r.u}


def _repoint(company, old, new):
    frappe.db.sql(
        "update `tabItem` set stock_uom=%(n)s where custom_company=%(c)s and stock_uom=%(o)s",
        {"n": new, "o": old, "c": company},
    )
    frappe.db.sql(
        """update `tabUOM Conversion Detail` d
             join `tabItem` i on i.name=d.parent and d.parenttype='Item'
           set d.uom=%(n)s where i.custom_company=%(c)s and d.uom=%(o)s""",
        {"n": new, "o": old, "c": company},
    )
    frappe.db.sql(
        """update `tabItem Price` ip join `tabItem` i on i.name=ip.item_code
           set ip.uom=%(n)s where i.custom_company=%(c)s and ip.uom=%(o)s""",
        {"n": new, "o": old, "c": company},
    )


def execute():
    if frappe.local.site in SKIP_SITES:
        print(f"[barakat] scope_uom_company: skipping non-barakat site {frappe.local.site}")
        return

    _ensure_uom_company_column()

    for company in frappe.get_all("Company", pluck="name"):
        created = repointed = skipped = 0
        for uom in _used_units(company):
            if classify_unit(uom, company) == "skip":
                if not frappe.db.get_value("UOM", uom, "custom_company"):
                    frappe.db.set_value(
                        "UOM", uom, "custom_company", company, update_modified=False
                    )
                skipped += 1
                continue
            new = scoped_name(uom, company)
            if not frappe.db.exists("UOM", new):
                src = frappe.get_doc("UOM", uom)
                frappe.get_doc(
                    {
                        "doctype": "UOM",
                        "uom_name": new,
                        "enabled": src.enabled,
                        "must_be_whole_number": src.must_be_whole_number,
                        "custom_company": company,
                    }
                ).insert(ignore_permissions=True)
                created += 1
            _repoint(company, uom, new)
            repointed += 1

        frappe.db.commit()

        leftover = frappe.db.sql(
            """select count(*) from `tabItem`
               where custom_company=%(c)s and ifnull(stock_uom,'')!='' and stock_uom not like %(p)s""",
            {"c": company, "p": f"% - {company}"},
        )[0][0]
        print(
            f"[barakat] UOM scope {company}: created={created} repointed={repointed} "
            f"skipped={skipped} leftover_items={leftover}"
        )
