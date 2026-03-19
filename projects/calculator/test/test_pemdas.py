"""
test/test_pemdas.py
===================
Tests for the PEMDAS evaluator.

Covers every operator and precedence rule, edge cases, and error handling.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pemdas import evaluate, tokenise, format_result, ParseError


class TestTokenise(unittest.TestCase):

    def test_integers(self):
        self.assertEqual(tokenise("123"), ["123"])

    def test_floats(self):
        self.assertEqual(tokenise("3.14"), ["3.14"])

    def test_operators(self):
        self.assertEqual(tokenise("1+2"), ["1", "+", "2"])

    def test_power(self):
        self.assertEqual(tokenise("2**3"), ["2", "**", "3"])

    def test_caret_normalised(self):
        self.assertEqual(tokenise("2^3"), ["2", "**", "3"])

    def test_parens(self):
        self.assertEqual(tokenise("(1+2)"), ["(", "1", "+", "2", ")"])

    def test_whitespace_ignored(self):
        self.assertEqual(tokenise("  3  +  4  "), ["3", "+", "4"])

    def test_floor_div_token(self):
        self.assertIn("//", tokenise("10//3"))

    def test_unexpected_char_raises(self):
        with self.assertRaises(ParseError):
            tokenise("3 @ 4")


class TestArithmetic(unittest.TestCase):

    def test_addition(self):
        self.assertAlmostEqual(evaluate("1 + 2"), 3)

    def test_subtraction(self):
        self.assertAlmostEqual(evaluate("10 - 4"), 6)

    def test_multiplication(self):
        self.assertAlmostEqual(evaluate("3 * 4"), 12)

    def test_division(self):
        self.assertAlmostEqual(evaluate("10 / 4"), 2.5)

    def test_floor_division(self):
        self.assertAlmostEqual(evaluate("10 // 3"), 3)

    def test_modulo(self):
        self.assertAlmostEqual(evaluate("10 % 3"), 1)

    def test_exponentiation(self):
        self.assertAlmostEqual(evaluate("2 ** 10"), 1024)

    def test_unary_minus(self):
        self.assertAlmostEqual(evaluate("-5"), -5)

    def test_unary_minus_in_expr(self):
        self.assertAlmostEqual(evaluate("3 + -2"), 1)

    def test_unary_plus(self):
        self.assertAlmostEqual(evaluate("+5"), 5)

    def test_float_literal(self):
        self.assertAlmostEqual(evaluate("1.5 + 1.5"), 3.0)


class TestPrecedence(unittest.TestCase):
    """The core PEMDAS correctness tests."""

    def test_mul_before_add(self):
        # 3 + 4 * 2 = 3 + 8 = 11, NOT (3+4)*2=14
        self.assertAlmostEqual(evaluate("3 + 4 * 2"), 11)

    def test_div_before_add(self):
        # 10 + 6 / 2 = 10 + 3 = 13
        self.assertAlmostEqual(evaluate("10 + 6 / 2"), 13)

    def test_parens_override_precedence(self):
        # (3 + 4) * 2 = 7 * 2 = 14
        self.assertAlmostEqual(evaluate("(3 + 4) * 2"), 14)

    def test_exp_before_mul(self):
        # 2 * 3 ** 2 = 2 * 9 = 18, NOT (2*3)**2=36
        self.assertAlmostEqual(evaluate("2 * 3 ** 2"), 18)

    def test_exp_right_associative(self):
        # 2 ** 3 ** 2 = 2 ** (3**2) = 2**9 = 512, NOT (2**3)**2=64
        self.assertAlmostEqual(evaluate("2 ** 3 ** 2"), 512)

    def test_unary_minus_with_exp(self):
        # -2 ** 2 = -(2**2) = -4, NOT (-2)**2=4
        self.assertAlmostEqual(evaluate("0 - 2 ** 2"), -4)

    def test_left_assoc_subtraction(self):
        # 10 - 3 - 2 = (10-3)-2 = 5, NOT 10-(3-2)=9
        self.assertAlmostEqual(evaluate("10 - 3 - 2"), 5)

    def test_left_assoc_division(self):
        # 12 / 4 / 3 = (12/4)/3 = 1, NOT 12/(4/3)=9
        self.assertAlmostEqual(evaluate("12 / 4 / 3"), 1)

    def test_nested_parens(self):
        self.assertAlmostEqual(evaluate("((2 + 3) * (4 - 1)) ** 2"), 225)

    def test_complex_pemdas(self):
        # Classic PEMDAS example: 8 / 2 * (2 + 2) = 4 * 4 = 16
        self.assertAlmostEqual(evaluate("8 / 2 * (2 + 2)"), 16)

    def test_mixed_all_ops(self):
        # 2 + 3 * 4 ** 2 - 10 / 2 = 2 + 3*16 - 5 = 2 + 48 - 5 = 45
        self.assertAlmostEqual(evaluate("2 + 3 * 4 ** 2 - 10 / 2"), 45)


class TestEdgeCases(unittest.TestCase):

    def test_zero(self):
        self.assertAlmostEqual(evaluate("0"), 0)

    def test_negative_result(self):
        self.assertAlmostEqual(evaluate("3 - 10"), -7)

    def test_large_exponent(self):
        self.assertAlmostEqual(evaluate("2 ** 20"), 1048576)

    def test_chained_parens(self):
        self.assertAlmostEqual(evaluate("((((1))))"), 1)

    def test_division_result_float(self):
        result = evaluate("1 / 3")
        self.assertAlmostEqual(result, 1 / 3)

    def test_caret_operator(self):
        self.assertAlmostEqual(evaluate("3^2"), 9)

    def test_percent_operator(self):
        self.assertAlmostEqual(evaluate("17 % 5"), 2)

    def test_floor_div(self):
        self.assertAlmostEqual(evaluate("7 // 2"), 3)


class TestErrors(unittest.TestCase):

    def test_empty_expr(self):
        with self.assertRaises(ParseError):
            evaluate("")

    def test_whitespace_only(self):
        with self.assertRaises(ParseError):
            evaluate("   ")

    def test_division_by_zero(self):
        with self.assertRaises(ParseError):
            evaluate("1 / 0")

    def test_floor_div_by_zero(self):
        with self.assertRaises(ParseError):
            evaluate("1 // 0")

    def test_modulo_by_zero(self):
        with self.assertRaises(ParseError):
            evaluate("5 % 0")

    def test_mismatched_parens_open(self):
        with self.assertRaises(ParseError):
            evaluate("(1 + 2")

    def test_mismatched_parens_close(self):
        with self.assertRaises(ParseError):
            evaluate("1 + 2)")

    def test_double_operator(self):
        # 1 ++ 2 is valid: the second + is unary plus → 3
        self.assertAlmostEqual(evaluate("1 ++ 2"), 3)

    def test_truly_invalid_double_op(self):
        with self.assertRaises(ParseError):
            evaluate("1 */ 2")

    def test_trailing_operator(self):
        with self.assertRaises(ParseError):
            evaluate("1 + 2 +")

    def test_unexpected_char(self):
        with self.assertRaises(ParseError):
            evaluate("1 $ 2")


class TestFormatResult(unittest.TestCase):

    def test_whole_number(self):
        self.assertEqual(format_result(6.0), "6")

    def test_float(self):
        r = format_result(1 / 3)
        self.assertIn(".", r)

    def test_negative_whole(self):
        self.assertEqual(format_result(-4.0), "-4")

    def test_zero(self):
        self.assertEqual(format_result(0.0), "0")

    def test_large_whole(self):
        self.assertEqual(format_result(1048576.0), "1048576")


if __name__ == "__main__":
    unittest.main(verbosity=2)
