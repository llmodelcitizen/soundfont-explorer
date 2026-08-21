"""Unit tests for inject_programs.py and make_diagnostic.py."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import smf as S                  # noqa: E402
import inject_programs as IP     # noqa: E402
import make_diagnostic           # noqa: E402


def ch(tick, status, *data):
    return S.Event(tick, "channel", status, bytes(data))


def lilypond_like():
    """Two tracks, no program changes, CC7 on both (what LilyPond exports)."""
    t0 = [S.Event(0, "meta", 0xFF, (500000).to_bytes(3, "big"), 0x51)]
    up = [S.Event(0, "meta", 0xFF, b"upper:", 0x03), ch(0, 0xB0, 7, 100),
          ch(0, 0x90, 72, 90), ch(480, 0x80, 72, 0),
          ch(480, 0x90, 86, 90), ch(960, 0x80, 86, 0)]
    lo = [S.Event(0, "meta", 0xFF, b"lower:", 0x03), ch(0, 0xB1, 7, 100),
          ch(0, 0x91, 40, 90), ch(480, 0x81, 40, 0),
          ch(480, 0x91, 55, 90), ch(960, 0x81, 55, 0), ch(700, 0xE1, 0, 70)]
    return S.Smf(1, 480, [t0, up, lo])


class InjectTests(unittest.TestCase):
    def test_missing_programs_detected(self):
        m = lilypond_like()
        self.assertEqual(IP.check_programs(m), [1, 2])

    def test_simple_injection(self):
        m = lilypond_like()
        log = IP.apply_rules(m, [{"track": 1, "channel": 1, "program": 0},
                                 {"track": 2, "channel": 2, "program": 0}])
        self.assertEqual(IP.check_programs(m), [])
        self.assertEqual(len(log), 2)
        self.assertTrue(all(l.endswith("added") for l in log))
        # the program change is the first channel event of the track, at tick 0
        first = next(e for e in m.tracks[1] if e.kind == "channel")
        self.assertEqual((first.tick, first.status, first.data), (0, 0xC0, b"\x00"))
        # idempotent
        log2 = IP.apply_rules(m, [{"track": 1, "channel": 1, "program": 0}])
        self.assertTrue(log2[0].endswith("kept"))
        self.assertEqual(sum(1 for e in m.tracks[1] if e.kind == "channel" and e.type == 0xC0), 1)

    def test_replace_differing_tick0_program(self):
        m = lilypond_like()
        m.tracks[1].insert(1, ch(0, 0xC0, 5))
        log = IP.apply_rules(m, [{"track": 1, "channel": 1, "program": 56}])
        self.assertTrue(log[0].endswith("replaced"))
        progs = [e for e in m.tracks[1] if e.kind == "channel" and e.type == 0xC0]
        self.assertEqual([(e.tick, e.data[0]) for e in progs], [(0, 56)])

    def test_split_moves_notes_and_duplicates_controllers(self):
        m = lilypond_like()
        IP.apply_rules(m, [
            {"track": 1, "channel": 1, "program": 56,
             "split": [{"from_note": 84, "channel": 4, "program": 72}]},
            {"track": 2, "channel": 2, "program": 60,
             "split": [{"below_note": 50, "channel": 3, "program": 58}]},
        ])
        self.assertEqual(IP.check_programs(m), [])
        notes = IP.channels_with_notes(m)
        self.assertEqual(notes, {0: 1, 3: 1, 1: 1, 2: 1})
        up = m.tracks[1]
        # note 86 (and its note-off) moved to channel 4
        self.assertEqual([(e.status, e.data[0]) for e in up if e.kind == "channel" and e.data[0] == 86],
                         [(0x93, 86), (0x83, 86)])
        # CC7 duplicated to channel 4; bend on lower duplicated to channel 3
        self.assertIn((0xB3, b"\x07\x64"), [(e.status, e.data) for e in up if e.kind == "channel"])
        lo = m.tracks[2]
        self.assertIn((0xE2, b"\x00\x46"), [(e.status, e.data) for e in lo if e.kind == "channel"])
        # programs: ch1=56 ch4=72 ch2=60 ch3=58, all at tick 0
        self.assertEqual(IP.programs_at_zero(m), {0: 56, 3: 72, 1: 60, 2: 58})
        # ordering is still monotonic so the writer accepts it
        S.serialize(m)

    def test_drum_channel_needs_program_too(self):
        m = S.Smf(0, 96, [[ch(0, 0x99, 36, 100), ch(48, 0x89, 36, 0)]])
        self.assertEqual(IP.check_programs(m), [10])
        IP.apply_rules(m, [{"track": 0, "channel": 10, "program": 0}])
        self.assertEqual(IP.check_programs(m), [])

    def test_program_later_than_first_note_is_flagged(self):
        m = S.Smf(0, 96, [[ch(0, 0x90, 60, 100), ch(0, 0xC0, 3), ch(48, 0x80, 60, 0)]])
        self.assertEqual(IP.check_programs(m), [1])
        m2 = S.Smf(0, 96, [[ch(0, 0xC0, 3), ch(0, 0x90, 60, 100), ch(48, 0x80, 60, 0)]])
        self.assertEqual(IP.check_programs(m2), [])


class DiagnosticTests(unittest.TestCase):
    def test_deterministic_and_complete(self):
        a = S.serialize(make_diagnostic.build())
        b = S.serialize(make_diagnostic.build())
        self.assertEqual(a, b)
        m = S.parse(a)
        progs = sorted({e.data[0] for e in m.tracks[1] if e.kind == "channel" and e.type == 0xC0})
        self.assertEqual(progs, list(range(128)))
        drums = sorted({e.data[0] for e in m.tracks[2] if e.kind == "channel" and e.is_note_on()})
        self.assertEqual(drums, list(range(35, 82)))
        self.assertTrue(all(e.channel == 9 for e in m.tracks[2] if e.kind == "channel"))
        ccs = {e.data[0] for e in m.tracks[3] if e.kind == "channel" and e.type == 0xB0}
        self.assertTrue({1, 7, 10, 11, 64, 91, 93} <= ccs)
        bends = [e for e in m.tracks[3] if e.kind == "channel" and e.type == 0xE0]
        values = {e.data[0] | (e.data[1] << 7) for e in bends}
        self.assertIn(16383, values)
        self.assertIn(0, values)
        self.assertIn(8192, values)
        sysex = [e for e in m.tracks[0] if e.kind == "sysex"]
        self.assertEqual(sysex[0].tick, 0)
        self.assertEqual(sysex[0].data, bytes([0x7E, 0x7F, 0x09, 0x01, 0xF7]))
        self.assertEqual(IP.check_programs(m), [])
        self.assertTrue(150 < S.midi_end_seconds(m) < 185)


if __name__ == "__main__":
    unittest.main()
