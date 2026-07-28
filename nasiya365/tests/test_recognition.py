import unittest

from nasiya365.api.recognition import (
    recognized_amount,
    recognized_delta,
    split_recognized,
)


class TestRecognizedAmount(unittest.TestCase):
    def test_below_cost_is_zero(self):
        # collected 350 < cogs 620 -> nothing recognized
        self.assertEqual(recognized_amount(350, 620, 120), 0.0)

    def test_exactly_cost_is_zero(self):
        self.assertEqual(recognized_amount(620, 620, 120), 0.0)

    def test_above_cost_under_total(self):
        # collected 700, cogs 620 -> 80 above cost, below total profit 120
        self.assertEqual(recognized_amount(700, 620, 120), 80.0)

    def test_full_collection_is_total_profit(self):
        # collected 740 (= principal 650 + interest 90), cogs 620 -> 120
        self.assertEqual(recognized_amount(740, 620, 120), 120.0)

    def test_overpayment_clamped_to_total(self):
        self.assertEqual(recognized_amount(800, 620, 120), 120.0)

    def test_zero_profit_deal(self):
        self.assertEqual(recognized_amount(700, 620, 0), 0.0)

    def test_loss_deal_recognizes_nothing(self):
        # total_profit negative (sold below cost) -> no positive profit recognized
        self.assertEqual(recognized_amount(700, 620, -10), 0.0)


class TestRecognizedDelta(unittest.TestCase):
    def test_first_payment_below_cost(self):
        # before 0 -> 0 ; after 350 -> 0 ; delta 0
        self.assertEqual(recognized_delta(0, 350, 620, 120), 0.0)

    def test_full_life_recognizes_everything(self):
        # before 350 -> 0 ; after 740 -> 120 ; delta 120
        self.assertEqual(recognized_delta(350, 740, 620, 120), 120.0)

    def test_window_crosses_cost_boundary(self):
        # before 650 -> 30 ; after 700 -> 80 ; delta 50
        self.assertEqual(recognized_delta(650, 700, 620, 120), 50.0)


class TestSplitRecognized(unittest.TestCase):
    def test_full_split(self):
        # total profit 120 = margin 30 + interest 90
        self.assertEqual(split_recognized(120, 30, 120), (30.0, 90.0))

    def test_partial_split_is_proportional(self):
        # recognized 80 of a 30/90 deal -> 20 margin / 60 interest
        self.assertEqual(split_recognized(80, 30, 120), (20.0, 60.0))

    def test_zero_total_profit(self):
        self.assertEqual(split_recognized(50, 0, 0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
