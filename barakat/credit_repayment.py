"""Settling what a customer owes, with no Frappe dependency.

The companion to `credit_limits`: that module decides how much debt a customer
may take on, this one decides what happens when they come back to pay it off.

Two ideas carry the whole module.

**A repayment may never exceed the debt.** Not a UI rule — the till's view of
what is owed is a snapshot, and between fetching it and taking the money the
customer may have bought again on another till, or a refund may have cancelled
part of it. The cap is therefore recomputed here against a freshly-read debt,
and the till's own number is never trusted.

**Only CONSOLIDATED debt can be allocated to an invoice.** A credit sale is a
POS Invoice, and a POS Invoice writes no GL entry until it is merged at shift
close — ERPNext will not let a Payment Entry reference one (its valid reference
doctypes for a customer are Sales Order, Sales Invoice, Journal Entry, Dunning
and Payment Entry). So money covering today's still-open shift cannot name the
invoice it pays. It goes on account, and ERPNext's own reconciliation matches
it up once the shift closes. That is not a workaround; it is the same thing
ERPNext does for any payment received before its invoice exists.
"""

from barakat.rounding import money_tolerance, round_half_up


def repayment_over_debt(amount, owed, precision=2):
    """Would this repayment hand over more than the customer actually owes?

    Compared with the same tolerance the credit ceiling uses, so float
    representation is forgiven and one agora over is still over.
    """
    paid = float(amount or 0.0)
    debt = float(owed or 0.0)
    if debt <= 0:
        # Nothing owed. Any payment at all is over, including a tiny one — we
        # do not accept money on account from a customer with a clean sheet.
        return paid > 0
    return round_half_up(paid, precision) - round_half_up(debt, precision) > money_tolerance(
        precision
    )


def valid_repayment(amount, owed, precision=2):
    """`(ok, reason)` for a proposed repayment. Reason is None when ok."""
    paid = float(amount or 0.0)
    if paid <= 0:
        return False, "amount_not_positive"
    if float(owed or 0.0) <= 0:
        return False, "nothing_owed"
    if repayment_over_debt(paid, owed, precision):
        return False, "over_debt"
    return True, None


def allocate_repayment(amount, invoices, precision=2):
    """Spread a repayment across outstanding invoices, oldest first.

    `invoices` is an iterable of `(name, outstanding)` ALREADY in the order they
    should be settled — oldest first, which is both what an accountant expects
    and what keeps the ageing report honest.

    Returns `(allocations, unallocated)`:

      allocations — `[(name, allocated), ...]`, skipping anything that would get
                    nothing. A Payment Entry reference row allocating zero is
                    noise in the customer's ledger, and ERPNext skips zero rows
                    when validating anyway.
      unallocated — what is left over. This is NOT an error and NOT a change: it
                    is the part of the payment covering debt that has no invoice
                    yet, which ERPNext holds as a credit on the customer's
                    account until the shift consolidates.

    Never allocates more than an invoice's own outstanding, and never more than
    the payment. Both caps matter: over-allocating makes ERPNext throw at submit
    and takes the cashier's money with no record.
    """
    remaining = round_half_up(float(amount or 0.0), precision)
    allocations = []
    for name, outstanding in invoices:
        if remaining <= 0:
            break
        due = round_half_up(float(outstanding or 0.0), precision)
        # A credit note, or a row that has been settled since it was read.
        # Skipped rather than treated as a negative allocation, which would
        # silently increase what the rest of the payment has to cover.
        if due <= 0:
            continue
        take = round_half_up(min(due, remaining), precision)
        allocations.append((name, take))
        remaining = round_half_up(remaining - take, precision)

    # Below the smallest unit is float dust, not money.
    if abs(remaining) <= money_tolerance(precision):
        remaining = 0.0
    return allocations, remaining
