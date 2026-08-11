"""The cashier-limit decisions, with no Frappe dependency.

Three per-till settings live on the POS Profile — see
docs/superpowers/specs/2026-08-11-pos-cashier-limits-design.md. The rules they
imply are decided HERE so they can be tested without a bench, and so the two
call sites (the POS Invoice validate hook and the Customer before_insert hook)
cannot drift apart.
"""

import decimal

AD_HOC_ITEM_CODE = "MISC"

# The value that means "no limit", and the value every profile that predates
# this feature inherits: Frappe writes the fixture's `default` into the column
# DDL, so `custom_max_discount_percent` is NOT NULL DEFAULT 100. A 0 there would
# reject every discounted sale at every live shop, which is why
# test_custom_fields pins the fixture default rather than trusting a comment.
MAX_DISCOUNT_UNLIMITED = 100.0


def has_ad_hoc_line(item_codes):
	"""Does this cart contain a cashier-invented item?

	The POS pushes an ad-hoc line — one the cashier typed a name and price for —
	as item_code "MISC" (see push-orders.ts). Matching is case- and
	padding-insensitive so a stray space cannot defeat the guard.
	"""
	target = AD_HOC_ITEM_CODE.casefold()
	for code in item_codes or []:
		if code is None:
			continue
		if str(code).strip().casefold() == target:
			return True
	return False


def _round_half_up(value, precision):
	"""Round half-up, the way the till's `roundMoney` does.

	Python's built-in `round` is banker's rounding, so `round(3.335, 2)` can land
	on 3.33 while the till sends 3.34. Comparing the two directly would reject
	the till's own maximum on exactly the subtotals where the percentage does
	not divide cleanly.

	This is NOT bit-identical to the till. `roundMoney` is
	`Math.round(major * 100)`, which multiplies first and so sees a different
	float; on a 19.99 subtotal at 50% it yields 9.99 where this yields 10.00.
	The property that matters is the DIRECTION of every such disagreement: this
	must never be smaller than the till's value, or the server would reject a
	discount the keypad itself offered. Measured across the half-way cases, it
	is always equal or one unit larger.
	"""
	quantum = decimal.Decimal(1).scaleb(-precision)
	return float(
		decimal.Decimal(str(value)).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
	)


def discount_over_cap(discount_amount, total, max_percent, precision=2):
	"""Does this order-level discount exceed the profile's cap?

	Compared in MONEY space rather than percentage space: the POS computes the
	discount as a rounded currency amount, so dividing it back into a percentage
	reintroduces the rounding error we would then have to forgive.

	The ceiling is rounded to the same precision, by the same half-up rule, that
	the till applies in `capDiscountAmount` — so the largest discount the keypad
	will accept is exactly the largest this function allows. Without that, a 10%
	cap on a 33.35 subtotal lets the till offer 3.34 (its rounding of 3.335) and
	the server rejects it.

	`max_percent` of None reads as unlimited, never as zero — an un-backfilled
	profile must not silently block every discount.
	"""
	total = float(total or 0.0)
	# Nothing to take a percentage of. Belt and braces: the money-space
	# comparison below would also handle it, but a zero-total invoice is an
	# exemption in the spec and stating it here keeps the two aligned.
	if total <= 0:
		return False

	cap = MAX_DISCOUNT_UNLIMITED if max_percent is None else float(max_percent)
	allowed = _round_half_up(total * cap / 100.0, precision)

	# One tenth of the smallest unit — float noise only, now that both sides of
	# the comparison are rounded to the same precision.
	tolerance = 1.0 / (10 ** (precision + 1))
	return float(discount_amount or 0.0) - allowed > tolerance
