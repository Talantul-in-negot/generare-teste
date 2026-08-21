import unittest

from src.biblical_tests.selection import SelectionError, parse_selection


class SelectionTests(unittest.TestCase):
    def test_parse_selection(self):
        cases = [
            ("1 Samuel 1,2", {"1 Samuel": [1, 2]}),
            ("1 Samuel 1-3", {"1 Samuel": [1, 2, 3]}),
            ("1Sam 1,3-5", {"1 Samuel": [1, 3, 4, 5]}),
            ("1 Samuel 1,2\nRut 1\nIoan 3-4", {"1 Samuel": [1, 2], "Rut": [1], "Ioan": [3, 4]}),
        ]
        for value, expected in cases:
            self.assertEqual(parse_selection(value), expected)

    def test_rejects_ambiguous_book(self):
        with self.assertRaises(SelectionError):
            parse_selection("Samuel 1")
