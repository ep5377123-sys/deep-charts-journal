import unittest

from journal.trades import build_trades

from .helpers import cfg, fixture_fills, text_fills


class RealFixtureTests(unittest.TestCase):
    def test_apex_day_matches_deep_charts_info_file(self):
        # Apex .info for this session: "DailyPL": 16.5, "DailyNetPL": 10.38
        trades, _ = build_trades(fixture_fills("Apex_PA-TEST-01_2026-10-06.fills"), cfg())
        self.assertEqual([t.gross for t in trades], [-109.5, 126.0])
        self.assertEqual([t.side for t in trades], ["Long", "Long"])
        self.assertEqual([t.max_qty for t in trades], [3, 3])
        self.assertAlmostEqual(sum(t.gross for t in trades), 16.5)
        self.assertAlmostEqual(sum(t.net for t in trades), 10.38, places=2)

    def test_restart_gives_exit_a_new_position_id_but_one_trade(self):
        trades, _ = build_trades(fixture_fills("Lucid_TEST-RESTART_2026-10-13.fills"), cfg())
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t.status, t.side, t.gross), ("Closed", "Short", 4.5))
        self.assertEqual(len(t.position_ids), 2)

    def test_partial_fills_day(self):
        # Hand-computed: +116 + 0 + 194 - 107 + 105.75 = 308.75 gross; 20 contract-sides x 0.51 fees.
        trades, _ = build_trades(fixture_fills("Lucid_TEST-PARTIAL_2026-10-16.fills"), cfg())
        self.assertEqual(len(trades), 5)
        self.assertAlmostEqual(sum(t.gross for t in trades), 308.75)
        self.assertAlmostEqual(sum(t.fees for t in trades), 10.20)
        self.assertAlmostEqual(sum(t.net for t in trades), 298.55)

    def test_scale_in_out_day(self):
        fills = fixture_fills("Lucid Account_TEST-SCALE_2026-08-19.fills")
        trades, owner = build_trades(fills, cfg())
        self.assertTrue(all(t.status == "Closed" for t in trades))
        # With every position flat, total gross is simply -sum(qty * price) * $2.
        expected = round(-sum(f.qty * f.price for f in fills) * 2, 2)
        self.assertAlmostEqual(sum(t.gross for t in trades), expected, places=2)
        self.assertEqual(set(owner), {f.key for f in fills})
        self.assertEqual(max(t.max_qty for t in trades), 11)


class SyntheticTests(unittest.TestCase):
    def test_reversal_in_one_fill_splits_into_two_trades(self):
        trades, _ = build_trades(text_fills([(1000, 100.0, 2), (2000, 105.0, -3), (3000, 103.0, 1)]), cfg())
        self.assertEqual([(t.side, t.max_qty, t.status) for t in trades], [("Long", 2, "Closed"), ("Short", 1, "Closed")])
        self.assertEqual(trades[0].gross, 20.0)   # 5 pts x 2 x $2
        self.assertEqual(trades[1].gross, 4.0)    # 2 pts x 1 x $2

    def test_open_position(self):
        trades, _ = build_trades(text_fills([(1000, 100.0, -2)]), cfg())
        t = trades[0]
        self.assertEqual((t.status, t.side, t.gross, t.net, t.exit_price), ("Open", "Short", None, None, None))

    def test_unknown_symbol_is_flagged_not_guessed(self):
        fills = text_fills(["ZZZ-202612-CME;1000;0;10;1;1;;0;0", "ZZZ-202612-CME;2000;0;11;-1;1;2;0;0"])
        t = build_trades(fills, cfg())[0][0]
        self.assertIsNone(t.gross)
        self.assertIn("multiplier missing: ZZZ", t.warnings)

    def test_firm_fee_override(self):
        c = cfg(fees_per_side={"default": {"MNQ": 0.51}, "SIM": {"MNQ": 0}})
        t = build_trades(text_fills([(1000, 100.0, 1), (2000, 101.0, -1)]), c)[0][0]
        self.assertEqual((t.fees, t.net), (0, 2.0))

    def test_trade_id_is_deterministic(self):
        a = build_trades(text_fills([(1000, 100.0, 1), (2000, 101.0, -1)]), cfg())[0][0]
        b = build_trades(text_fills([(1000, 100.0, 1), (2000, 101.0, -1)], name="other-pc.fills"), cfg())[0][0]
        self.assertEqual(a.trade_id, b.trade_id)


if __name__ == "__main__":
    unittest.main()
