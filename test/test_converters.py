from decimal import Decimal
import unittest

from src.collector.raw.converters import to_decimal, to_int, to_text


class ConverterTest(unittest.TestCase):
    def test_decimal_empty_and_signed_values(self):
        cases = {
            "": None,
            " ": None,
            None: None,
            "0": Decimal("0"),
            "-123": Decimal("-123"),
            "+123": Decimal("123"),
            "123.45": Decimal("123.45"),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(to_decimal(source), expected)

    def test_integer_empty_and_signed_values(self):
        self.assertIsNone(to_int(""))
        self.assertIsNone(to_int(" "))
        self.assertIsNone(to_int(None))
        self.assertEqual(to_int("0"), 0)
        self.assertEqual(to_int("-123"), -123)
        self.assertEqual(to_int("+123"), 123)
        with self.assertRaises(ValueError):
            to_int("123.45")

    def test_sign_is_not_combined_with_value(self):
        self.assertEqual(to_decimal("123"), Decimal("123"))
        self.assertEqual(to_text("5"), "5")
