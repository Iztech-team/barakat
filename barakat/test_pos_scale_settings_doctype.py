import json, pathlib, unittest

DOCTYPE = pathlib.Path(__file__).resolve().parent / "barakat" / "doctype" / \
    "pos_scale_settings" / "pos_scale_settings.json"

class PosScaleSettingsDoctype(unittest.TestCase):
    def setUp(self):
        self.dt = json.loads(DOCTYPE.read_text(encoding="utf-8"))
        self.fields = {f["fieldname"]: f for f in self.dt["fields"]}

    def test_autoname_by_branch(self):
        self.assertEqual(self.dt.get("autoname"), "field:branch")

    def test_branch_and_company_links(self):
        self.assertEqual(self.fields["branch"]["options"], "Branch")
        self.assertEqual(self.fields["custom_company"]["options"], "Company")

    def test_barcode_fields_present(self):
        for fn in ["has_balances", "scale_barcode_enabled", "scale_barcode_prefix",
                   "scale_barcode_code_length", "scale_barcode_value_type",
                   "scale_barcode_value_length", "scale_barcode_decimals"]:
            self.assertIn(fn, self.fields, fn)

    def test_value_type_options(self):
        self.assertEqual(self.fields["scale_barcode_value_type"]["options"], "price\nweight")
