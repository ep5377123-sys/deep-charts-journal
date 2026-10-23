import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from journal.sheets import SheetsError
from journal.state import State
from journal.sync import Syncer

from .helpers import FIXTURES, cfg

APEX = "Apex_PA-TEST-01_2026-10-06.fills"


REPLAY = "SIM_SIM 1_2026-10-23.fills"


class FakeClient:
    def __init__(self):
        self.posts, self.fail, self.knows_replay = [], False, True

    def post(self, body):
        if self.fail:
            raise SheetsError("network: down")
        self.posts.append(body)
        if not self.knows_replay:  # a receiver deployed before the Replay tab existed
            return {"ok": True}
        return {"ok": True, "replay_trades_upserted": len(body.get("replay_trades", []))}

    def fills(self):
        return [f for p in self.posts for f in p.get("fills", [])]

    def trades(self):
        return [t for p in self.posts for t in p.get("trades", [])]

    def replay_trades(self):
        return [t for p in self.posts for t in p.get("replay_trades", [])]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.watch = self.tmp / "Hist Fills"
        self.watch.mkdir()
        self.state = State(self.tmp / "state.db")
        self.client = FakeClient()
        self.syncer = Syncer(cfg(watch_dirs=[str(self.watch)]), self.state, self.client, "PC1")

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name, src=APEX):
        shutil.copy(FIXTURES / src, self.watch / name)

    def test_backfill_then_nothing_new(self):
        self.add(APEX)
        self.syncer.once()
        self.assertEqual(len(self.client.fills()), 4)
        self.assertEqual(len(self.client.trades()), 2)
        self.client.posts.clear()
        self.syncer.file_sigs.clear()  # force a re-read, as after a restart
        self.syncer.once()
        self.assertEqual(self.client.posts, [])

    def test_appended_fill_is_sent_and_trade_updated(self):
        lines = (FIXTURES / APEX).read_text().splitlines(keepends=True)
        path = self.watch / APEX
        path.write_text("".join(lines[:3]), newline="")
        self.syncer.once()
        open_trade = [t for t in self.client.trades() if t["status"] == "Open"]
        self.assertEqual(len(open_trade), 1)
        self.client.posts.clear()
        with open(path, "a", newline="") as f:
            f.write(lines[3])
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
        self.syncer.once()
        self.assertEqual(len(self.client.fills()), 1)
        (t,) = self.client.trades()
        self.assertEqual((t["trade_id"], t["status"], t["gross"]), (open_trade[0]["trade_id"], "Closed", 126.0))

    def test_conflict_copy_adds_nothing(self):
        self.add(APEX)
        self.syncer.once()
        self.client.posts.clear()
        self.add("Apex_PA-TEST-01_2026-10-06-DESKTOP-OTHER.fills")
        self.syncer.once()
        self.assertEqual(self.client.posts, [])

    def test_outage_keeps_payloads_until_delivered(self):
        self.add(APEX)
        self.client.fail = True
        self.syncer.once()
        self.assertEqual(self.state.counts()["outbox"], 2)
        self.client.fail = False
        self.state.db.execute("UPDATE outbox SET next_try = 0")
        self.syncer.flush()
        self.assertEqual(self.state.counts()["outbox"], 0)
        self.assertEqual(len(self.client.fills()), 4)

    def test_journal_since_skips_older_trades(self):
        # Apex fixture: trade 1 opens at 1791306875338, trade 2 at 1791307591370.
        self.syncer.cfg["journal_since_ms"] = 1791307591370
        self.add(APEX)
        self.syncer.once()
        self.assertEqual([t["gross"] for t in self.client.trades()], [126.0])
        self.assertEqual(len(self.client.fills()), 2)
        self.assertEqual(self.state.counts()["fills_unsent"], 0)
        # A fee change re-prices trades, but the skipped one must still stay out.
        self.client.posts.clear()
        self.syncer.cfg["fees_per_side"] = {"default": {"MNQ": 0.60}}
        self.syncer.stage()
        self.syncer.flush()
        self.assertEqual([t["gross"] for t in self.client.trades()], [126.0])

    def test_trade_open_at_cutoff_stays_out_when_it_closes(self):
        lines = (FIXTURES / APEX).read_text().splitlines(keepends=True)
        path = self.watch / APEX
        path.write_text(lines[0], newline="")  # trade 1 entry, before the cutoff
        self.syncer.cfg["journal_since_ms"] = 1791306875338 + 1
        self.syncer.once()
        with open(path, "a", newline="") as f:
            f.write(lines[1])  # its exit arrives after the cutoff
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
        self.syncer.once()
        self.assertEqual(self.client.posts, [])

    def test_replay_trade_is_cut_by_replay_day_and_sent_apart(self):
        self.syncer.cfg["journal_since_ms"] = 1792374430000  # 2026-10-18; the replayed bars are from January
        self.add(REPLAY, REPLAY)
        self.syncer.once()
        self.assertEqual(self.client.trades(), [])
        (t,) = self.client.replay_trades()
        self.assertEqual((t["account_type"], t["replayed_on"], t["gross"]), ("Replay", "2026-10-23", -400.0))
        self.assertEqual(len(self.client.fills()), 2)

    def test_replay_from_before_the_cutoff_day_stays_out(self):
        self.syncer.cfg["journal_since_ms"] = 1792900000000  # 2026-10-25, after the replay ran
        self.add(REPLAY, REPLAY)
        self.syncer.once()
        self.assertEqual(self.client.posts, [])

    def test_outdated_receiver_keeps_replay_trades_queued(self):
        self.client.knows_replay = False
        self.add(REPLAY, REPLAY)
        self.syncer.once()
        c = self.state.counts()
        self.assertEqual(c["outbox"], 1)
        self.assertIn("redeploy", c["outbox_error"])
        self.client.knows_replay = True
        self.state.db.execute("UPDATE outbox SET next_try = 0")
        self.syncer.flush()
        self.assertEqual(self.state.counts()["outbox"], 0)

    def test_sim_can_be_excluded(self):
        self.syncer.cfg["include_sim"] = False
        self.add("SIM_SIM 1_2026-10-06.fills")
        self.syncer.once()
        self.assertEqual(self.client.posts, [])


if __name__ == "__main__":
    unittest.main()
