"""The credit-sale decisions, with no Frappe dependency.

See docs/superpowers/specs/2026-08-17-pos-credit-sales-design.md.

Decided here, rather than at the call sites, for the same reason
`cashier_limits` is: the rules can then be tested without a bench, and the
server guard and anything else that answers "may this customer take credit?"
cannot drift apart.

The one idea worth carrying in your head while reading this module: a
customer's debt lives in THREE places, and ERPNext's own credit check sees only
one of them.

  consolidated    — in the GL. `check_credit_limit` reads this and nothing else.
  unconsolidated  — submitted POS Invoices with an outstanding balance that have
                    not yet been merged at shift close. They post no GL entries
                    until then, so they are invisible to ERPNext.
  in flight       — credit sales still sitting in a till's local queue.

Judging a new credit sale against the GL alone is what lets a ₪500 limit be
breached ten times in one shift, with every check passing, until consolidation
posts the lot and throws — jamming the shift close.
"""

from barakat.rounding import money_tolerance, round_half_up

# A customer with no `Customer Credit Limit` row for this company, or a row of
# zero, may not buy on credit at all.
#
# This is DELIBERATELY stricter than ERPNext, where `get_credit_limit` returning
# 0 disables the check entirely — i.e. zero means "unlimited". Inheriting that
# would mean a shop that switches credit sales on and forgets to set any limits
# accrues unbounded debt with no warning, which is the opposite of what a limit
# is for.
NO_CREDIT = 0.0


def credit_limit_of(raw):
	"""A configured credit limit as a float, or 0.0 for "none configured".

	Accepts whatever the database hands back — None, "", a string, a Decimal —
	because a missing row and a zero row mean the same thing here and neither
	should raise.
	"""
	if raw is None:
		return NO_CREDIT
	try:
		limit = float(raw)
	except (TypeError, ValueError):
		return NO_CREDIT
	# A negative limit is meaningless; read it as "none" rather than as a
	# ceiling below zero that no customer could ever satisfy.
	return limit if limit > 0 else NO_CREDIT


def may_take_credit(limit):
	"""Is this customer allowed to buy on credit at all?"""
	return credit_limit_of(limit) > NO_CREDIT


def total_owed(consolidated, unconsolidated, precision=2):
	"""What the customer owes once everything already committed is counted.

	Both terms are summed at the invoice's precision so the total cannot drift
	by a fraction of an agora across many invoices.
	"""
	return round_half_up(
		float(consolidated or 0.0) + float(unconsolidated or 0.0), precision
	)


def credit_headroom(limit, consolidated, unconsolidated, precision=2):
	"""How much more this customer may put on credit right now.

	Never negative: a customer already past their limit has no headroom, and
	reporting a negative number would invite a caller to treat it as arithmetic
	rather than as a refusal.
	"""
	ceiling = credit_limit_of(limit)
	if ceiling <= NO_CREDIT:
		return 0.0
	owed = total_owed(consolidated, unconsolidated, precision)
	headroom = round_half_up(ceiling - owed, precision)
	return headroom if headroom > 0 else 0.0


def credit_over_limit(new_debt, limit, consolidated, unconsolidated, precision=2):
	"""Would taking on `new_debt` put this customer past their limit?

	`new_debt` is THIS invoice's unpaid remainder, not its total: a bill
	part-paid in cash only borrows the difference.

	Compared in money space against a ceiling rounded the same way the till
	rounds, so the largest credit the till offers is exactly the largest this
	allows. The tolerance forgives float representation only — both sides are
	already rounded to the same precision — so being one agora over is still
	over.
	"""
	debt = float(new_debt or 0.0)
	# Not a credit sale at all. Stated here as well as at the call site so this
	# function is safe to ask about any invoice, paid or not.
	if debt <= 0:
		return False

	ceiling = credit_limit_of(limit)
	if ceiling <= NO_CREDIT:
		# No limit configured means no credit, so ANY debt is over the line.
		return True

	owed_after = round_half_up(
		total_owed(consolidated, unconsolidated, precision) + debt, precision
	)
	return owed_after - ceiling > money_tolerance(precision)
