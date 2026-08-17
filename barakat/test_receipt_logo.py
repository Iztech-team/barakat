"""Tests for the receipt-logo rules.

Runnable ANYWHERE — `barakat.receipt_logo` imports no `frappe`, on purpose. The
validate hook that calls it is a five-line adapter; everything worth getting
wrong is here.

    python -m unittest barakat.test_receipt_logo
"""

import base64
import struct
import unittest
import zlib

from barakat.receipt_logo import (
	DEFAULT_WIDTH_PERCENT,
	MAX_DECODED_BYTES,
	MAX_DIMENSION,
	ReceiptLogoError,
	check_image,
	clean_width,
	png_dimensions,
	resolve,
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
	return (
		struct.pack(">I", len(payload))
		+ kind
		+ payload
		+ struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
	)


def make_png(width: int = 64, height: int = 64, padding: int = 0) -> bytes:
	"""A structurally real 1-bit greyscale PNG of the given size.

	Built by hand rather than with Pillow so the test suite needs nothing the
	Frappe bench does not already guarantee. `padding` inflates the file with a
	private chunk when a test needs to cross the size ceiling.
	"""
	ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
	rows = b"".join(b"\x00" + b"\xff" * ((width + 7) // 8) for _ in range(height))
	out = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
	if padding:
		out += _png_chunk(b"prIV", b"\x00" * padding)
	return out + _png_chunk(b"IDAT", zlib.compress(rows)) + _png_chunk(b"IEND", b"")


def data_url(raw: bytes) -> str:
	return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


SMALL_LOGO = data_url(make_png(128, 64))


class PngDimensions(unittest.TestCase):
	def test_reads_ihdr(self):
		self.assertEqual(png_dimensions(make_png(300, 120)), (300, 120))

	def test_rejects_a_jpeg(self):
		with self.assertRaises(ReceiptLogoError):
			png_dimensions(b"\xff\xd8\xff\xe0" + b"\x00" * 40)

	def test_rejects_a_truncated_png(self):
		with self.assertRaises(ReceiptLogoError):
			png_dimensions(make_png()[:16])

	def test_rejects_a_png_whose_first_chunk_is_not_ihdr(self):
		broken = bytearray(make_png())
		broken[12:16] = b"gAMA"
		with self.assertRaises(ReceiptLogoError):
			png_dimensions(bytes(broken))

	def test_rejects_a_zero_sized_png(self):
		with self.assertRaises(ReceiptLogoError):
			png_dimensions(make_png(0, 0))


class CheckImage(unittest.TestCase):
	def test_accepts_a_baked_logo(self):
		self.assertTrue(check_image(SMALL_LOGO).startswith(b"\x89PNG"))

	def test_tolerates_surrounding_whitespace(self):
		self.assertTrue(check_image(f"\n  {SMALL_LOGO}  \n"))

	def test_rejects_empty(self):
		for value in ("", "   ", None):
			with self.assertRaises(ReceiptLogoError):
				check_image(value)

	def test_rejects_an_svg_data_url(self):
		# Not merely unsupported: an SVG data URL rendered in the Admin Panel
		# executes script, and this field is writable straight over REST.
		svg = base64.b64encode(b"<svg onload='alert(1)'/>").decode()
		with self.assertRaises(ReceiptLogoError):
			check_image(f"data:image/svg+xml;base64,{svg}")

	def test_rejects_a_jpeg_data_url(self):
		with self.assertRaises(ReceiptLogoError):
			check_image("data:image/jpeg;base64," + base64.b64encode(b"x" * 40).decode())

	def test_rejects_a_bare_http_url(self):
		with self.assertRaises(ReceiptLogoError):
			check_image("https://example.com/logo.png")

	def test_rejects_a_png_mime_carrying_non_png_bytes(self):
		with self.assertRaises(ReceiptLogoError):
			check_image(data_url(b"\xff\xd8\xff\xe0" + b"\x00" * 40))

	def test_rejects_broken_base64(self):
		with self.assertRaises(ReceiptLogoError):
			check_image("data:image/png;base64,!!!!not base64!!!!")

	def test_rejects_base64_with_smuggled_whitespace(self):
		# validate=True: characters outside the alphabet are an error rather
		# than being skipped into bytes nobody approved.
		with self.assertRaises(ReceiptLogoError):
			check_image("data:image/png;base64," + base64.b64encode(make_png()).decode()[:20] + " \n junk")

	def test_rejects_an_image_over_the_size_ceiling(self):
		big = make_png(64, 64, padding=MAX_DECODED_BYTES + 1)
		self.assertGreater(len(big), MAX_DECODED_BYTES)
		with self.assertRaises(ReceiptLogoError) as caught:
			check_image(data_url(big))
		self.assertIn("too large", str(caught.exception))

	def test_accepts_an_image_exactly_at_the_ceiling(self):
		raw = make_png(64, 64)
		pad = MAX_DECODED_BYTES - len(raw) - 12  # 12 = the padding chunk's frame
		exact = make_png(64, 64, padding=pad)
		self.assertEqual(len(exact), MAX_DECODED_BYTES)
		self.assertTrue(check_image(data_url(exact)))

	def test_rejects_an_image_wider_than_the_dimension_cap(self):
		with self.assertRaises(ReceiptLogoError):
			check_image(data_url(make_png(MAX_DIMENSION + 1, 10)))

	def test_rejects_an_image_taller_than_the_dimension_cap(self):
		with self.assertRaises(ReceiptLogoError):
			check_image(data_url(make_png(10, MAX_DIMENSION + 1)))

	def test_accepts_an_image_exactly_at_the_dimension_cap(self):
		self.assertTrue(check_image(data_url(make_png(MAX_DIMENSION, MAX_DIMENSION))))


class CleanWidth(unittest.TestCase):
	def test_keeps_a_sane_value(self):
		self.assertEqual(clean_width(45), 45)

	def test_clamps_below_the_floor(self):
		self.assertEqual(clean_width(2), 10)
		self.assertEqual(clean_width(-30), 10)

	def test_clamps_above_the_ceiling(self):
		self.assertEqual(clean_width(101), 100)
		self.assertEqual(clean_width(100000), 100)

	def test_truncates_a_float(self):
		self.assertEqual(clean_width(42.9), 42)

	def test_accepts_a_numeric_string(self):
		self.assertEqual(clean_width("55"), 55)

	def test_falls_back_to_the_default_for_nonsense(self):
		# The fallback is 32, not 0. Zero would print a logo of no width, which
		# reads on paper as "the logo feature is broken".
		for value in (None, "", "abc", [], {}):
			self.assertEqual(clean_width(value), DEFAULT_WIDTH_PERCENT)


class Resolve(unittest.TestCase):
	def test_a_profile_from_before_the_feature_still_saves(self):
		# The shape that matters most: every field absent. It must normalise to
		# exactly what the till already prints, not raise and not blank the logo.
		self.assertEqual(resolve(None, None, None), ("Default", "", 32))

	def test_default_mode_drops_a_leftover_image(self):
		self.assertEqual(resolve("Default", SMALL_LOGO, 40), ("Default", "", 40))

	def test_none_mode_drops_a_leftover_image(self):
		mode, image, width = resolve("None", SMALL_LOGO, 40)
		self.assertEqual((mode, image), ("None", ""))

	def test_custom_keeps_the_image(self):
		mode, image, width = resolve("Custom", SMALL_LOGO, 50)
		self.assertEqual(mode, "Custom")
		self.assertEqual(image, SMALL_LOGO)
		self.assertEqual(width, 50)

	def test_custom_trims_the_stored_value(self):
		_, image, _ = resolve("Custom", f"  {SMALL_LOGO}  ", 32)
		self.assertEqual(image, SMALL_LOGO)

	def test_custom_without_an_image_is_refused(self):
		with self.assertRaises(ReceiptLogoError) as caught:
			resolve("Custom", "", 32)
		self.assertIn("Upload one", str(caught.exception))

	def test_custom_with_a_bad_image_is_refused(self):
		with self.assertRaises(ReceiptLogoError):
			resolve("Custom", "data:image/png;base64,zzzz", 32)

	def test_an_unknown_mode_is_refused(self):
		with self.assertRaises(ReceiptLogoError) as caught:
			resolve("Rainbow", "", 32)
		self.assertIn("Rainbow", str(caught.exception))

	def test_mode_is_case_sensitive(self):
		# Frappe stores Select options verbatim; accepting "custom" here would
		# mean the till and the server disagree about what is configured.
		with self.assertRaises(ReceiptLogoError):
			resolve("custom", SMALL_LOGO, 32)

	def test_blank_mode_reads_as_default(self):
		self.assertEqual(resolve("   ", "", 32)[0], "Default")

	def test_width_is_clamped_even_on_none_mode(self):
		self.assertEqual(resolve("None", "", 999)[2], 100)


if __name__ == "__main__":
	unittest.main()
