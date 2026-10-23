import unittest

from journal.trades import build_trades, is_replay

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


# 2026-01-05 15:32 UTC: a replayed January bar, written to files dated in October.
JAN = 1767627131991


class ReplayTests(unittest.TestCase):
    def test_replay_fixture_is_one_replay_trade(self):
        # Replayed 2026-10-23: NQ March long 25684.25 -> 25664.25 = -20 pts x 1 x $20.
        (t,), _ = build_trades(fixture_fills("SIM_SIM 1_2026-10-23.fills"), cfg())
        self.assertEqual((t.account_type, t.status, t.side, t.gross), ("Replay", "Closed", "Long", -400.0))
        self.assertEqual((t.session_date, t.replayed_on), ("2026-01-05", "2026-10-23"))
        self.assertEqual(t.trade_id, "SIM|SIM 1|NQ-202603-CME|1767627131991|replay 2026-10-23")
        self.assertEqual(t.to_payload()["replayed_on"], "2026-10-23")

    def test_live_fixtures_are_never_replay(self):
        for name in ["Apex_PA-TEST-01_2026-10-06.fills", "Lucid_TEST-RESTART_2026-10-13.fills",
                     "Lucid_TEST-PARTIAL_2026-10-16.fills", "Lucid Account_TEST-SCALE_2026-08-19.fills"]:
            fills = fixture_fills(name)
            self.assertFalse([f for f in fills if is_replay(f)], name)
            trades, _ = build_trades(fills, cfg())
            self.assertTrue(all(t.replayed_on == "" and "replay" not in t.trade_id for t in trades), name)
            self.assertTrue(all("replayed_on" not in t.to_payload() for t in trades), name)

    def test_same_bars_replayed_on_two_days_are_separate_trades(self):
        day1 = text_fills([(JAN, 100.0, 1), (JAN + 60_000, 101.0, -1)], date="2026-10-20", name="SIM_SIM 1_2026-10-20.fills")
        day2 = text_fills([(JAN + 1000, 100.0, 1), (JAN + 30_000, 99.0, -1)], date="2026-10-21", name="SIM_SIM 1_2026-10-21.fills")
        trades, _ = build_trades(day1 + day2, cfg())
        self.assertEqual([(t.replayed_on, t.gross, t.status) for t in trades],
                         [("2026-10-20", 2.0, "Closed"), ("2026-10-21", -2.0, "Closed")])

    def test_live_and_replay_fills_in_one_file_stay_apart(self):
        live = 1792712885000  # 2026-10-22 19:48 New York, the file's own day
        fills = text_fills([(live, 200.0, 1), (JAN, 100.0, -1), (JAN + 5000, 99.0, 1), (live + 5000, 201.0, -1)],
                           date="2026-10-22", name="SIM_SIM 1_2026-10-22.fills")
        trades, _ = build_trades(fills, cfg())
        self.assertEqual(sorted((t.account_type, t.side, t.gross) for t in trades),
                         [("Replay", "Short", 2.0), ("SIM", "Long", 2.0)])


if __name__ == "__main__":
    unittest.main()
