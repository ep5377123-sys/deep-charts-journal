import unittest

from journal.parser import FileInfo, parse_filename, parse_text

INFO = FileInfo("Lucid", "TEST-1", "2026-10-16", "Lucid_TEST-1_2026-10-16.fills")
LINE = "MNQ-202612-CME;1792159006211;1;29802.5;1;7334220275982592;7334220276256768;0;0"


class FilenameTests(unittest.TestCase):
    def test_firm_with_space(self):
        i = parse_filename("Lucid Account_LFE050-XXXXXXXX-TEST001_2026-08-19.fills")
        self.assertEqual((i.firm, i.account, i.session_date), ("Lucid Account", "LFE050-XXXXXXXX-TEST001", "2026-08-19"))

    def test_account_with_space(self):
        i = parse_filename("SIM_SIM 1_2026-09-29.fills")
        self.assertEqual((i.firm, i.account, i.session_date), ("SIM", "SIM 1", "2026-09-29"))

    def test_onedrive_conflict_copy(self):
        i = parse_filename("Apex_PA-APEX-1_2026-10-06-DESKTOP-ABC.fills")
        self.assertEqual((i.firm, i.account, i.session_date), ("Apex", "PA-APEX-1", "2026-10-06"))

    def test_not_a_fills_file(self):
        self.assertIsNone(parse_filename("notes.txt"))
        self.assertIsNone(parse_filename("garbage.fills"))


class LineTests(unittest.TestCase):
    def test_fields(self):
        (f,), bad = parse_text(LINE + "\r\n", INFO)
        self.assertEqual(bad, [])
        self.assertEqual((f.symbol, f.ts_ms, f.price, f.qty, f.root), ("MNQ-202612-CME", 1792159006211, 29802.5, 1, "MNQ"))
        self.assertEqual((f.position_id, f.close_id, f.flag), ("7334220275982592", "7334220276256768", "1"))

    def test_identical_lines_are_separate_fills(self):
        fills, _ = parse_text(f"{LINE}\r\n{LINE}\r\n", INFO)
        self.assertEqual(len(fills), 2)
        self.assertNotEqual(fills[0].key, fills[1].key)

    def test_keys_are_stable_across_reads(self):
        a, _ = parse_text(f"{LINE}\r\n{LINE}\r\n", INFO)
        b, _ = parse_text(f"{LINE}\r\n{LINE}\r\n", FileInfo("Lucid", "TEST-1", "2026-10-16", "copy-from-other-pc.fills"))
        self.assertEqual([f.key for f in a], [f.key for f in b])

    def test_key_includes_account(self):
        a, _ = parse_text(LINE + "\r\n", INFO)
        b, _ = parse_text(LINE + "\r\n", FileInfo("Lucid", "TEST-2", "2026-10-16", "x.fills"))
        self.assertNotEqual(a[0].key, b[0].key)

    def test_unterminated_last_line_is_skipped(self):
        fills, bad = parse_text(f"{LINE}\r\n{LINE[:30]}", INFO)
        self.assertEqual((len(fills), bad), (1, []))

    def test_wrong_field_count_is_reported(self):
        fills, bad = parse_text("a;b;c\r\n" + LINE + "\r\n", INFO)
        self.assertEqual((len(fills), bad), (1, ["a;b;c"]))

    def test_blank_lines_ignored(self):
        fills, bad = parse_text("\r\n" + LINE + "\r\n\r\n", INFO)
        self.assertEqual((len(fills), bad), (1, []))


if __name__ == "__main__":
    unittest.main()
