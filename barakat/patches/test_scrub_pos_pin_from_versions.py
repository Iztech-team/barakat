"""Unit tests for the Version-history PIN scrub.

Only `_scrub_row` is exercised: it is the whole decision, and it is pure, so it can
be tested without a site.
"""

import json
import unittest

from barakat.patches.scrub_pos_pin_from_versions import REDACTED, _scrub_row


def _version(*changed):
    return json.dumps({"changed": list(changed)})


class ScrubPosPinFromVersions(unittest.TestCase):
    def test_replaces_both_the_old_and_the_new_pin(self):
        # The old value matters as much as the new one: a history of past PINs is
        # still a history of credentials, and people reuse them.
        out = _scrub_row(_version(["custom_pos_pin", "1234", "5678"]))
        self.assertIsNotNone(out)
        parsed = json.loads(out)
        self.assertEqual(parsed["changed"][0], ["custom_pos_pin", REDACTED, REDACTED])

    def test_leaves_the_row_and_its_other_fields_intact(self):
        # The version row is not deleted and unrelated changes keep their full
        # before-and-after — losing a designation's history to protect a PIN would
        # be a bad trade.
        out = _scrub_row(
            _version(
                ["designation", "Cashier", "Supervisor"],
                ["custom_pos_pin", "1111", "2222"],
            )
        )
        parsed = json.loads(out)
        self.assertEqual(
            parsed["changed"][0], ["designation", "Cashier", "Supervisor"]
        )
        self.assertEqual(parsed["changed"][1], ["custom_pos_pin", REDACTED, REDACTED])

    def test_a_row_without_a_pin_is_left_alone(self):
        self.assertIsNone(_scrub_row(_version(["designation", "A", "B"])))

    def test_is_idempotent(self):
        once = _scrub_row(_version(["custom_pos_pin", "1234", "5678"]))
        # Already scrubbed: nothing left to do, so no second write.
        self.assertIsNone(_scrub_row(once))

    def test_survives_junk(self):
        # A patch that throws on one malformed row stops the whole migration.
        self.assertIsNone(_scrub_row("not json"))
        self.assertIsNone(_scrub_row(""))
        self.assertIsNone(_scrub_row(None))
        self.assertIsNone(_scrub_row(json.dumps([1, 2, 3])))
        self.assertIsNone(_scrub_row(json.dumps({"changed": "nope"})))
        self.assertIsNone(_scrub_row(json.dumps({"changed": [["short"]]})))


if __name__ == "__main__":
    unittest.main()
