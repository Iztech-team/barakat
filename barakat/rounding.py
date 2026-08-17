"""Money rounding that agrees with the till, with no Frappe dependency.

Extracted from `cashier_limits` when the credit-sale rules needed the same
rule: both compare a server-side ceiling against a figure the POS computed, so
both have to round the ceiling exactly as the POS does or they reject the till's
own maximum.
"""

import decimal


def round_half_up(value, precision):
	"""Round half-up, the way the till's `roundMoney` does.

	Python's built-in `round` is banker's rounding, so `round(3.335, 2)` can land
	on 3.33 while the till sends 3.34. Comparing the two directly would reject
	the till's own maximum on exactly the amounts where the arithmetic does not
	divide cleanly.

	This is NOT bit-identical to the till. `roundMoney` is
	`Math.round(major * 100)`, which multiplies first and so sees a different
	float; on a 19.99 subtotal at 50% it yields 9.99 where this yields 10.00.
	The property that matters is the DIRECTION of every such disagreement: this
	must never be smaller than the till's value, or the server would reject a
	figure the keypad itself offered. Measured across the half-way cases, it is
	always equal or one unit larger.
	"""
	quantum = decimal.Decimal(1).scaleb(-precision)
	return float(
		decimal.Decimal(str(value)).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
	)


def money_tolerance(precision):
	"""One tenth of the smallest unit — float noise only.

	Used on both sides of a money comparison once each side has already been
	rounded to `precision`, so it forgives representation error without ever
	forgiving a real difference of one agora.
	"""
	return 1.0 / (10 ** (precision + 1))
