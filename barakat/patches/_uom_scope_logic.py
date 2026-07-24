"""Pure decision logic for UOM company-scoping — no Frappe imports, unit-tested.

We never rename a unit in place. A shared/built-in unit (Kg, Unit, Nos, ...) is
system-wide and may be used by other companies; renaming it would corrupt them,
and after repointing one company's items the reference counts shift so a later
company could mis-classify it. So every unit a company uses gets a scoped COPY
(`<unit> - <Company>`) and the company's items are repointed onto it; the global
original is left intact. Orphaned globals are harmless — the AP picker filters on
`custom_company`, so they never show.
"""

SEP = " - "


def scoped_name(uom, company):
    """`<uom> - <company>`, idempotent if already suffixed for this company."""
    suffix = f"{SEP}{company}"
    return uom if uom.endswith(suffix) else f"{uom}{suffix}"


def classify_unit(uom, company):
    """'skip' if already scoped to this company, else 'copy'."""
    return "skip" if uom.endswith(f"{SEP}{company}") else "copy"
