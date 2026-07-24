"""Pure, Frappe-free tests for the UOM-scoping decision logic.

Runs locally:  python -m unittest barakat.test_uom_scope_logic
"""

import unittest

from barakat.patches._uom_scope_logic import classify_unit, scoped_name


class ScopedName(unittest.TestCase):
    def test_appends_once(self):
        self.assertEqual(scoped_name("Kg", "Beit Al-Moneh"), "Kg - Beit Al-Moneh")

    def test_idempotent(self):
        self.assertEqual(
            scoped_name("Kg - Beit Al-Moneh", "Beit Al-Moneh"), "Kg - Beit Al-Moneh"
        )

    def test_arabic_unit(self):
        self.assertEqual(scoped_name("علبة", "Beit Al-Moneh"), "علبة - Beit Al-Moneh")


class ClassifyUnit(unittest.TestCase):
    # Already scoped to this company -> nothing to do.
    def test_already_scoped_skip(self):
        self.assertEqual(classify_unit("Kg - Beit Al-Moneh", "Beit Al-Moneh"), "skip")

    # A bare built-in unit -> copy a scoped version and repoint (never rename the
    # global, which is shared system-wide / with other companies).
    def test_bare_builtin_copy(self):
        self.assertEqual(classify_unit("Kg", "Beit Al-Moneh"), "copy")

    # A bare custom unit -> also copy (uniform; the orphaned global is invisible
    # because the picker filters on custom_company).
    def test_bare_custom_copy(self):
        self.assertEqual(classify_unit("Bag", "Beit Al-Moneh"), "copy")

    # A name that ends with a DIFFERENT company's suffix is still just "copy" for us
    # (we only ever classify units our own items reference, so this is defensive).
    def test_other_company_suffix_copy(self):
        self.assertEqual(classify_unit("Kg - Other Co", "Beit Al-Moneh"), "copy")


if __name__ == "__main__":
    unittest.main()
