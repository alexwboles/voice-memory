"""Unit tests for voice_memory.redact."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))

from voice_memory.redact import redact_text, redact  # noqa: E402


class TestRedact(unittest.TestCase):
    def test_email(self):
        self.assertIn("[email]", redact_text("mail me at jane@doe.com ok"))

    def test_phone_formats(self):
        self.assertIn("[phone]", redact_text("call (555) 123-4567"))
        self.assertIn("[phone]", redact_text("call 555-123-4567"))
        # short digit runs survive
        self.assertNotIn("[phone]", redact_text("order 12345"))

    def test_ssn(self):
        self.assertIn("[ssn]", redact_text("ssn 123-45-6789"))

    def test_card_luhn(self):
        self.assertIn("[card]", redact_text("card 4111111111111111"))
        # fails Luhn -> kept
        self.assertNotIn("[card]", redact_text("card 4111111111111112"))

    def test_account_keeps_label(self):
        out = redact_text("account #98765")
        self.assertIn("[account]", out)
        self.assertIn("account", out.replace("[account]", ""))

    def test_address_and_zip(self):
        out = redact_text("I live at 123 Main Street 90210")
        self.assertIn("[address]", out)
        self.assertNotIn("90210", out)
        # a zip separated from the address by other words is left alone
        out2 = redact_text("I live at 123 Main Street, Springfield 90210")
        self.assertIn("[address]", out2)

    def test_names_kept_by_default(self):
        self.assertIn("Maya", redact_text("my name is Maya"))

    def test_names_masked_opt_in(self):
        out = redact_text("my colleague John Smith will call", names=True)
        self.assertIn("[name]", out)
        self.assertNotIn("John Smith", out)

    def test_disabled_returns_untouched(self):
        self.assertEqual(redact_text("jane@doe.com", enabled=False), "jane@doe.com")

    def test_backcompat_alias(self):
        self.assertIn("[email]", redact("a@b.com"))


if __name__ == "__main__":
    unittest.main()
