"""Narrow a multi-company user to the persona of the shop they are acting in.

## The problem this solves

ERPNext roles live on the **User**, which is site-global. So a person who is a
Cashier in shop A and a Manager in shop B cannot be expressed: there is one roles
child table, and it says one thing for the whole site.

The chosen shape is therefore: the User carries the **union** of every persona they
hold (the widest thing they could do anywhere), and this module takes it back down
to the right persona for the shop the current request is acting in.

That direction is forced, not preferred. Frappe's controller-permission hooks can
only DENY — `has_permission` returning True leaves the DocPerm decision untouched
and can never grant. So the roles must be wide enough for the most privileged shop,
or the check never runs at all and the user is blocked even where they are the
manager.

## Which shop is "active"

The proxy stamps `X-Barakat-Company` on every ERPNext call from the admin panel; it
is the company already in the caller's JWT. A caller may of course forge it — and
gains nothing by doing so, because the same declaration does BOTH jobs: it picks the
persona *and* pins the rows. Claiming the shop where you are a manager also throws
away every row belonging to the shop where you are not. There is no combination that
holds shop B's powers over shop A's data.

When no company is declared at all (the ERPNext desk, a raw REST call, a background
job) we fall back to the INTERSECTION of every persona the user holds — the
narrowest thing that is true in all their shops. Failing to the narrowest, rather
than the widest, is what makes the undeclared path safe by construction.

## Standing down

This is a no-op unless the caller has ACTIVE Employee records in two or more
companies. Every single-company user — which today is every user on every site,
since ERPNext's own duplicate check has so far made anything else impossible — takes
the `return True` on the first branch and keeps the exact permission decision they
had before this module existed. Owners (System Manager / Administrator) are never
narrowed.

## Coverage

`GUARDED_DOCTYPES` is derived from `MODULE_DOCTYPES`, the same map the persona role
bundles are generated from, so a doctype a persona can reach is guarded by
construction rather than by anyone remembering to add it. Doctypes outside that map
are granted by no Barakat role, so the DocPerm check refuses them before this hook
is consulted — with the exception of Frappe's own desk baseline, which is why
`user_scope.py` exists for `User` and why any future baseline finding belongs here
too.

Registration is not scoping, though. A registered doctype with no Company Link field
has nothing to filter on, and until 2026-08-05 this module answered that case with an
empty string - silently unfiltering the query, the exact failure mode it exists to
prevent. Every guarded doctype is now classified: either it carries a marker, or it is
named in `COMPANY_NEUTRAL_DOCTYPES` as genuinely site-wide. Anything else fails closed,
so a doctype added to the matrix tomorrow is refused rather than leaked, and
`test_company_scope.py` fails until someone decides which of the two it is.

Note what this module can and cannot do. It only ever runs for a caller with ACTIVE
Employee records in two or more companies; the boundary for everyone else is the
`Company` User Permission, which needs the same marker field to bind to. Both layers
depend on it, which is why `company_marker.py` exists.
"""

import frappe

from barakat.permissions import ALL_ROLE_PERMS, PERSONAS, bundle_for
from barakat.persona_matrix import MODULE_DOCTYPES

# Accounts that are never narrowed. Mirrors `barakat.api.session_role.OWNER_ROLES`.
OWNER_ROLES = frozenset({"System Manager", "Administrator"})

# The header the proxy stamps with the caller's active company.
ACTIVE_COMPANY_HEADER = "X-Barakat-Company"

# Every doctype any persona can reach, straight from the matrix the roles are built
# from. Sorted so the generated hook registration in hooks.py is deterministic.
GUARDED_DOCTYPES = tuple(
	sorted({dt for doctypes in MODULE_DOCTYPES.values() for dt in doctypes})
)

# Guarded doctypes that genuinely belong to the SITE, not to one shop, and so can never
# carry a company marker. Everything else must have one or `get_permission_query_conditions`
# fails closed - see the module docstring.
#
# This list is deliberately hand-written and deliberately short. Each entry is a decision
# that a leak of these rows is acceptable, not an oversight, and `test_company_scope.py`
# refuses any entry that has since grown a marker.
#
#   Currency, Fiscal Year, Holiday List,     ERPNext masters shared by every company on
#   Holiday List Assignment, Designation,    the site. Scoping them would empty the
#   Salary Component                         pickers on forms every persona can open.
#   System Settings, Payroll Settings        Singles. One row, no owner. System Settings
#                                            is also in TILL_REQUIRED_READS - filtering
#                                            it would break every till's rounding.
#   User                                     Frappe's desk baseline, not a Barakat grant.
#                                            Scoped by tier in `user_scope.py` instead.
#   Device                                   A till's hardware identity (id + name only).
#                                            Owns no shop data; giving it a company is a
#                                            product change, not a security fix.
#   Supplier Group, Territory                Tree masters. ERPNext seeds a shared root and
#                                            both are picked on forms every persona opens,
#                                            so a marker here needs the same backfill work
#                                            Customer Group already had. Leak is names only.
COMPANY_NEUTRAL_DOCTYPES = frozenset(
	{
		"Currency",
		"Designation",
		"Device",
		"Fiscal Year",
		"Global Defaults",
		"Holiday List",
		"Holiday List Assignment",
		"Payroll Settings",
		"Salary Component",
		"Supplier Group",
		"System Settings",
		"Territory",
		"User",
	}
)

# Doctypes whose owning company is named by something the two standard field names
# miss. Both were found by `test_company_scope.py` rather than by reading, which is the
# point of having it.
#
#   Branch   ERPNext's Branch has no company at all; Barakat adds `custom_pos_company`.
#   Company  is pinned by its own name. Frappe binds a Company User Permission to the
#            `name` of the Company doctype itself, which is why a Company list is
#            already correctly scoped (1 of 8 on prod) with no `company` field in sight.
COMPANY_FIELD_OVERRIDES = {
	"Branch": "custom_pos_company",
	"Company": "name",
}


def active_company():
	"""The company this request declared, or None.

	Reads the request directly rather than a cached flag: a background job has no
	request at all, and `frappe.request` raises rather than returning None there.
	"""
	try:
		request = getattr(frappe.local, "request", None)
		if request is None:
			return None
		return (request.headers.get(ACTIVE_COMPANY_HEADER) or "").strip() or None
	except Exception:
		return None


def employee_personas(user):
	"""`{company: persona}` for every ACTIVE Employee linked to this user.

	`ignore_permissions` is required: no persona holds read on Employee, and this
	must answer for the caller before their own permissions are known.
	"""
	rows = frappe.get_all(
		"Employee",
		filters={"user_id": user, "status": "Active"},
		fields=["company", "custom_role_preset"],
		ignore_permissions=True,
	)
	out = {}
	for row in rows:
		company = (row.get("company") or "").strip()
		persona = (row.get("custom_role_preset") or "").strip()
		if company and persona in PERSONAS:
			out[company] = persona
	return out


def persona_perms(persona, doctype):
	"""The ptypes `persona` may use on `doctype`, from the real role bundles.

	Computed from `ALL_ROLE_PERMS` rather than a second matrix, so this can never
	drift from the roles the user is actually granted.
	"""
	granted = set()
	for role in bundle_for(persona):
		granted |= set(ALL_ROLE_PERMS.get(role, {}).get(doctype, ()))
	return granted


def scope_for(user, doctype):
	"""`(allowed_ptypes, pinned_company)` for this caller, or None to stand down.

	`pinned_company` is None when nothing was declared — the caller then keeps every
	company they belong to, but only at the intersection of their personas.
	"""
	if user in ("Administrator", "Guest"):
		return None
	if OWNER_ROLES & set(frappe.get_roles(user)):
		return None

	personas = employee_personas(user)
	if len(personas) < 2:
		# Single-company (or no Employee at all): unchanged behaviour.
		return None

	declared = active_company()
	if declared and declared in personas:
		return persona_perms(personas[declared], doctype), declared

	# Undeclared, or a company this user has no Employee in: fall to the narrowest
	# truth across their shops rather than the widest.
	sets = [persona_perms(persona, doctype) for persona in personas.values()]
	return set.intersection(*sets), None


def company_field_for(doctype):
	"""The fieldname naming this doctype's owning company, or None.

	Accepts the native ERPNext `company` and the `custom_company` marker Barakat adds
	where ERPNext ships none, because the Company User Permission binds to either -
	all it looks for is a Link field whose `options` is Company. Checking the fieldtype
	rather than mere presence matters: a Data field named `company` holds a company
	NAME, not a link, and filtering on it would compare against the wrong value.

	Resolved by name rather than by "any Link to Company" on purpose. Several doctypes
	carry a Company link that is not ownership -- `Customer.represents_company`,
	`Company.parent_company` -- and pinning on one of those would filter the list by
	the wrong relationship. Anything the two standard names miss is declared in
	COMPANY_FIELD_OVERRIDES, and `test_company_scope.py` fails until it is.
	"""
	override = COMPANY_FIELD_OVERRIDES.get(doctype)
	if override:
		return override
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return None
	for fieldname in ("company", "custom_company"):
		field = meta.get_field(fieldname)
		if field and field.fieldtype == "Link" and field.options == "Company":
			return fieldname
	return None


def _doc_company(doc, doctype):
	field = company_field_for(doctype)
	if not field:
		return ""
	if isinstance(doc, dict):
		return (doc.get(field) or "").strip()
	return (getattr(doc, field, "") or "").strip()


def has_permission(doc, ptype="read", user=None, **kwargs):
	"""Controller permission for one document (frappe hook).

	Can only deny. Returning True leaves the DocPerm decision exactly as it was.
	"""
	user = user or frappe.session.user
	doctype = doc.get("doctype") if isinstance(doc, dict) else getattr(doc, "doctype", None)
	scope = scope_for(user, doctype)
	if scope is None:
		return True

	allowed, pinned = scope
	if ptype not in allowed:
		return False
	if pinned:
		if not company_field_for(doctype) and doctype not in COMPANY_NEUTRAL_DOCTYPES:
			# Shop-owned, but nothing on the row says which shop. Refuse, for the same
			# reason the list query returns `1=0`.
			return False
		company = _doc_company(doc, doctype)
		# A company-neutral doctype is not shop-owned; the persona check above is the
		# whole decision for it.
		if company and company != pinned:
			return False
	return True


def get_permission_query_conditions(user=None, doctype=None):
	"""SQL appended to every list query for a guarded doctype (frappe hook).

	Returns "" for callers we do not narrow, which Frappe drops entirely — an
	unaffected user keeps the exact query they had before.
	"""
	user = user or frappe.session.user
	scope = scope_for(user, doctype)
	if scope is None:
		return ""

	allowed, pinned = scope
	if "read" not in allowed and "select" not in allowed:
		# Matches nothing. `1=0` rather than an empty string, which would silently
		# unfilter the query — the failure mode this whole module exists to prevent.
		return "1=0"
	if pinned:
		field = company_field_for(doctype)
		if field:
			return f"`tab{doctype}`.`{field}` = {frappe.db.escape(pinned)}"
		if doctype not in COMPANY_NEUTRAL_DOCTYPES:
			# Shop-owned, but there is no column to pin it by. Returning "" here is
			# what leaked every shop's Contact and Item Price rows until 2026-08-05.
			return "1=0"
	return ""
