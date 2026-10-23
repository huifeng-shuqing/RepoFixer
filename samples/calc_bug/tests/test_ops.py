"""Verification tests: run with `python tests/test_ops.py` from the repo root."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calc.ops import add, div, mul, sub  # noqa: E402


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_sub(self):
        self.assertEqual(sub(5, 3), 2)

    def test_mul(self):
        self.assertEqual(mul(3, 4), 12)

    def test_div(self):
        self.assertEqual(div(6, 3), 2.0)

    def test_div_zero(self):
        with self.assertRaises(ValueError):
            div(1, 0)


if __name__ == "__main__":
    unittest.main()
