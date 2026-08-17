"""The receipt logo a POS Profile prints, and the rules it must obey.

Pure Python on purpose — no `frappe` import — so every rule below is testable
without a bench, and so the same reasoning can be read in one place instead of
being spread through a validate hook.

## Why the mode is a tri-state and not just "is the image empty"

The requirement carries two different meanings for a blank image: "I did not
choose one, print the Barakat logo" and "I chose to print nothing". One nullable
column cannot hold both, so the choice is its own field and the image is only
consulted when that choice says `Custom`.

## Why only PNG, and why a data URL at all

The Admin Panel bakes the manager's upload into a black-and-white PNG in the
browser and stores the bytes on the profile, because a till prints offline: a
logo it has to fetch at print time is a blank space the first time the shop's
internet drops. The bytes therefore travel with the rest of the profile.

`data:image/svg+xml` is refused rather than merely unused. An SVG data URL that
reaches an <img> in the Admin Panel is a script-execution vector, and the field
is written by anything holding POS Profile write — including the desk and the
raw REST API, neither of which goes through the panel's own checks.
"""

import base64
import binascii

MODE_DEFAULT = "Default"
MODE_CUSTOM = "Custom"
MODE_NONE = "None"
MODES = (MODE_DEFAULT, MODE_CUSTOM, MODE_NONE)

DATA_URL_PREFIX = "data:image/png;base64,"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# The decoded PNG ceiling. A realistic black-and-white logo lands at 2-20 KB, so
# this is ~5x headroom rather than a limit anyone will meet. It exists because
# the bytes ride inside the POS Profile document: they cross the proxy in a JSON
# body (nginx defaults to a 1 MB request) and land in the till's device-config
# file, which is rewritten in full on every unrelated settings change.
MAX_DECODED_BYTES = 100 * 1024

# The AP bakes masters at 512 dots on the longest side. 640 leaves room for a
# hand-made or older image without admitting something that would blow up the
# till's canvas.
MAX_DIMENSION = 640

DEFAULT_WIDTH_PERCENT = 32
MIN_WIDTH_PERCENT = 10
MAX_WIDTH_PERCENT = 100


class ReceiptLogoError(ValueError):
	"""A receipt logo field that cannot be stored, with a message for the user."""


def png_dimensions(raw: bytes) -> tuple[int, int]:
	"""Width and height from a PNG's IHDR chunk.

	Parsed from the raw bytes rather than through Pillow: this runs inside a
	validate hook on every POS Profile save, and reading 24 bytes cannot fail in
	the ways an image library can.
	"""
	# 8 magic + 4 length + 4 "IHDR" + 4 width + 4 height
	if len(raw) < 24 or not raw.startswith(PNG_MAGIC):
		raise ReceiptLogoError("The receipt logo is not a PNG image.")
	if raw[12:16] != b"IHDR":
		raise ReceiptLogoError("The receipt logo PNG is malformed (no header chunk).")
	width = int.from_bytes(raw[16:20], "big")
	height = int.from_bytes(raw[20:24], "big")
	if width <= 0 or height <= 0:
		raise ReceiptLogoError("The receipt logo PNG reports no size.")
	return width, height


def check_image(image: str) -> bytes:
	"""Validate a stored logo data URL and return its decoded PNG bytes.

	Raises ReceiptLogoError with a message naming what to do about it.
	"""
	value = (image or "").strip()
	if not value:
		raise ReceiptLogoError("No receipt logo image was provided.")
	if not value.startswith(DATA_URL_PREFIX):
		raise ReceiptLogoError(
			"The receipt logo must be a PNG prepared by the Admin Panel "
			"(a 'data:image/png;base64,' value). Please re-upload it there."
		)
	payload = value[len(DATA_URL_PREFIX) :]
	try:
		# validate=True so stray characters are an error rather than being
		# silently skipped into bytes that are not the image anyone approved.
		raw = base64.b64decode(payload, validate=True)
	except (binascii.Error, ValueError):
		raise ReceiptLogoError(
			"The receipt logo image is corrupted. Please re-upload it in the Admin Panel."
		) from None
	if len(raw) > MAX_DECODED_BYTES:
		raise ReceiptLogoError(
			f"The receipt logo image is too large ({len(raw) // 1024} KB). "
			f"The limit is {MAX_DECODED_BYTES // 1024} KB."
		)
	width, height = png_dimensions(raw)
	if width > MAX_DIMENSION or height > MAX_DIMENSION:
		raise ReceiptLogoError(
			f"The receipt logo image is {width}x{height} dots. "
			f"Neither side may exceed {MAX_DIMENSION}."
		)
	return raw


def clean_width(value) -> int:
	"""The printed width percentage, clamped into range.

	Clamped rather than refused: the width is a cosmetic dimension, and rejecting
	a whole profile save because a slider reported 101 would be a worse trade
	than printing the logo one percent narrower. A value that is not a number at
	all falls back to the default, which is what an untouched profile carries.
	"""
	if value is None or value == "":
		return DEFAULT_WIDTH_PERCENT
	try:
		number = int(float(value))
	except (TypeError, ValueError):
		return DEFAULT_WIDTH_PERCENT
	return max(MIN_WIDTH_PERCENT, min(MAX_WIDTH_PERCENT, number))


def resolve(mode, image, width) -> tuple[str, str, int]:
	"""Normalise the three stored fields, or raise ReceiptLogoError.

	Returns `(mode, image, width)` exactly as they should be persisted.

	`Default` and `None` come back with an EMPTY image on purpose. Keeping the
	bytes of a logo nobody prints means the next reader cannot tell whether the
	shop has a logo configured, and it carries a hundred kilobytes through every
	profile read for nothing.
	"""
	chosen = (mode or "").strip() or MODE_DEFAULT
	if chosen not in MODES:
		raise ReceiptLogoError(
			f"'{chosen}' is not a receipt logo choice. Use one of: {', '.join(MODES)}."
		)

	cleaned_width = clean_width(width)

	if chosen != MODE_CUSTOM:
		return chosen, "", cleaned_width

	if not (image or "").strip():
		raise ReceiptLogoError(
			"This till is set to print a custom receipt logo but no image has been "
			"uploaded. Upload one, or set the receipt logo to Default or None."
		)
	check_image(image)
	return chosen, image.strip(), cleaned_width
