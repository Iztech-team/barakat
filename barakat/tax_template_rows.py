"""Pure duplicate-account rules for a tax template's rows — no Frappe imports.

ERPNext lets one Sales Taxes and Charges Template post twice to the SAME account
and then miscomputes the result. Observed on POS Invoice ACC-PSINV-2026-00134:
rows of 16% and 14%, both on `vat - BAM`, came back as 14 and 14, so the invoice's
tax total was 28 where the till had collected 30. It still posted — paid_amount
130 against grand_total 128 — and every return against it was then rejected for
the gap.

Nothing downstream can repair that, because by the time the numbers disagree the
money has already changed hands. So the rule is enforced at save time, on the
template, where the mistake is still free to undo.

Separate accounts per row stay allowed: a template that books VAT to one account
and a municipal levy to another is legitimate, and common.
"""


def normalize_account_head(account):
    """Comparison key for one row's account.

    Trimmed and case-folded, matching how MariaDB's default collation already
    treats an Account docname — `VAT - BAM` and `vat - BAM` cannot both exist as
    accounts, so treating them as one row is the truth, not a convenience.
    """
    return (account or "").strip().casefold()


def first_duplicate_account(accounts):
    """First account whose normalized key already appeared, else None.

    Returns the ORIGINAL string so the caller can quote the account the way the
    user sees it rather than the folded key.

    Blank accounts are skipped. `account_head` is `reqd` on the child doctype, so
    Frappe already refuses those, and flagging them here would stack a second
    error onto one row.
    """
    seen = set()
    for account in accounts:
        key = normalize_account_head(account)
        if not key:
            continue
        if key in seen:
            return account
        seen.add(key)
    return None
