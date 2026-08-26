"""Is anybody looking at this company's tills right now.

The tills board wants to feel live, and a till reports every thirty seconds. Rather than
open a socket to every till in every shop for a page that is closed almost all the time,
the server simply answers the question the till already asks: `report` returns
`next_heartbeat_s`, and while somebody has the board open that answer gets small.

So the board makes the tills chatty by being looked at, and they go quiet again on their
own a couple of minutes after it is closed. Nothing has to be told to stop, which is the
property that matters - a browser tab that is closed, crashed or driven off a cliff sends
no goodbye, and a design that needed one would leave shops reporting every three seconds
for ever.

Redis rather than a column: this is a fact about the last two minutes, it is written on
every single board request, and it is worthless after a restart. A row would be a write
amplifier on the busiest read in the feature.
"""

import frappe

#: How long one look at the board keeps the shop's tills hurrying.
#: Comfortably longer than the board's own poll, so a manager reading the page holds the
#: window open without a gap, and short enough that a closed tab costs one quiet minute.
WATCH_TTL_S = 120

#: What a watched till is asked for. Not zero, and not tuned lower without measuring: a
#: shop with eight tills at three seconds is already ~2.7 requests a second, all of which
#: write a row.
WATCHED_HEARTBEAT_S = 3


def _key(company):
	return f"presence:watching:{company}"


def mark_watching(company):
	"""Somebody just looked at this company's board."""
	if not company:
		return
	frappe.cache().set_value(_key(company), 1, expires_in_sec=WATCH_TTL_S)


def is_watched(company):
	"""True while the last look is still recent."""
	if not company:
		return False
	return bool(frappe.cache().get_value(_key(company)))


def forget(company):
	"""Drop the window immediately. For tests, and for turning a shop quiet by hand."""
	if not company:
		return
	frappe.cache().delete_value(_key(company))


def heartbeat_for(company, configured):
	"""How soon this company's tills should report next.

	`min`, never a replacement. A shop that has deliberately set a heartbeat FASTER than
	the watched rate must not be slowed down by somebody opening a page.
	"""
	if is_watched(company):
		return min(int(configured), WATCHED_HEARTBEAT_S)
	return int(configured)
