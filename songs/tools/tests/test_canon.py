"""Unit tests for songs/tools: SMF parser/writer, tempo map, midi_end, end marker, trim."""
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import smf as S            # noqa: E402
import canon               # noqa: E402


def ch(tick, status, *data):
    return S.Event(tick, "channel", status, bytes(data))


def tempo(tick, us):
    return S.Event(tick, "meta", 0xFF, us.to_bytes(3, "big"), 0x51)


def build(division=480, tracks=None, fmt=1):
    return S.Smf(fmt, division, tracks or [])


class VlqTests(unittest.TestCase):
    def test_roundtrip(self):
        for v in (0, 1, 127, 128, 16383, 16384, 0x0FFFFFFF):
            enc = S.write_vlq(v)
            dec, pos = S.read_vlq(enc, 0)
            self.assertEqual((dec, pos), (v, len(enc)))

    def test_known_encodings(self):
        self.assertEqual(S.write_vlq(0x7F), b"\x7f")
        self.assertEqual(S.write_vlq(0x80), b"\x81\x00")
        self.assertEqual(S.write_vlq(0x3FFF), b"\xff\x7f")
        self.assertEqual(S.write_vlq(0x4000), b"\x81\x80\x00")


class ParserWriterTests(unittest.TestCase):
    def test_roundtrip_format1(self):
        t0 = [tempo(0, 500000), S.Event(0, "meta", 0xFF, b"name", 0x03),
              S.Event(0, "sysex", 0xF0, bytes([0x7E, 0x7F, 0x09, 0x01, 0xF7]))]
        t1 = [ch(0, 0xC0, 5), ch(0, 0x90, 60, 100), ch(480, 0x80, 60, 0),
              ch(480, 0xB0, 7, 100), ch(960, 0xE0, 0, 64), ch(1000, 0xD0, 10), ch(1001, 0xA0, 60, 3)]
        m = build(tracks=[t0, t1])
        blob = S.serialize(m)
        self.assertEqual(blob[:4], b"MThd")
        back = S.parse(blob)
        self.assertEqual(back.format, 1)
        self.assertEqual(back.division, 480)
        self.assertEqual(len(back.tracks), 2)
        # every original event survives, plus exactly one end-of-track per track
        for orig, got in zip(m.tracks, back.tracks):
            body = [e for e in got if not (e.kind == "meta" and e.meta_type == 0x2F)]
            eots = [e for e in got if e.kind == "meta" and e.meta_type == 0x2F]
            self.assertEqual(len(eots), 1)
            self.assertEqual([(e.tick, e.kind, e.status, e.data, e.meta_type) for e in orig],
                             [(e.tick, e.kind, e.status, e.data, e.meta_type) for e in body])
        # serialising again is byte-identical (determinism)
        self.assertEqual(S.serialize(back), blob)

    def test_running_status_and_format0(self):
        # hand-built format-0 track using running status and a note-on with velocity 0
        body = (b"\x00\xc0\x01"          # program 1
                b"\x00\x90\x3c\x64"      # note on 60
                b"\x10\x3e\x64"          # running status: note on 62
                b"\x20\x3c\x00"          # running status: note off (vel 0) 60
                b"\x00\xff\x51\x03\x07\xa1\x20"  # tempo 500000
                b"\x00\x3e\x00"          # running status resumes after meta: note off 62
                b"\x00\xff\x2f\x00")
        blob = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + b"MTrk" + struct.pack(">I", len(body)) + body
        m = S.parse(blob)
        self.assertEqual(m.format, 0)
        evs = [e for e in m.tracks[0] if e.kind == "channel"]
        self.assertEqual([(e.tick, e.status, tuple(e.data)) for e in evs],
                         [(0, 0xC0, (1,)), (0, 0x90, (60, 100)), (16, 0x90, (62, 100)),
                          (48, 0x90, (60, 0)), (48, 0x90, (62, 0))])
        self.assertTrue(evs[3].is_note_off())
        self.assertEqual(S.note_end_tick(m), 48)

    def test_rejects_format2_and_garbage(self):
        blob = b"MThd" + struct.pack(">IHHH", 6, 2, 0, 96)
        with self.assertRaises(S.SmfError):
            S.parse(blob)
        with self.assertRaises(S.SmfError):
            S.parse(b"not a midi file")

    def test_malformed_input_is_always_smferror(self):
        # truncated inputs used to escape as IndexError / struct.error, which the admin's
        # channels endpoint turned into a 500 instead of a 400 (#19)
        header = b"MThd" + struct.pack(">IHHH", 6, 1, 1, 96)
        cases = {
            "bare MThd": b"MThd",
            "short header": b"MThd" + b"\x00\x00\x00\x06\x00\x01",
            "track ends on 0xFF": header + b"MTrk" + struct.pack(">I", 2) + b"\x00\xff",
            "track ends after meta type": header + b"MTrk" + struct.pack(">I", 3) + b"\x00\xff\x51",
        }
        for name, blob in cases.items():
            with self.assertRaises(S.SmfError, msg=name):
                S.parse(blob)

    def test_real_corpus_files_roundtrip(self):
        # canonical MIDIs live under songs/rendered/, imported ones in sub-directories mirroring
        # their path under songs/import/FILES/
        rendered = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rendered")
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(rendered)
                 for f in fs if f.endswith(".mid")]
        self.assertTrue(files, "no canonical songs present under songs/rendered/")
        for f in files:
            with open(f, "rb") as fh:
                blob = fh.read()
            m = S.parse(blob)
            self.assertEqual(S.serialize(m), blob, "%s is not in canonical form" % f)


class TempoMapTests(unittest.TestCase):
    def test_two_tempo_changes(self):
        # 480 ppqn: 120 bpm for 2 beats, 60 bpm for 2 beats, then 240 bpm
        t0 = [tempo(0, 500000), tempo(960, 1000000), tempo(1920, 250000)]
        m = build(tracks=[t0, []])
        tm = S.TempoMap(m)
        self.assertAlmostEqual(tm.seconds(0), 0.0)
        self.assertAlmostEqual(tm.seconds(480), 0.5)
        self.assertAlmostEqual(tm.seconds(960), 1.0)
        self.assertAlmostEqual(tm.seconds(1440), 2.0)
        self.assertAlmostEqual(tm.seconds(1920), 3.0)
        self.assertAlmostEqual(tm.seconds(1920 + 480), 3.25)
        # inverse
        for tick in (0, 100, 480, 960, 1500, 1920, 5000):
            self.assertEqual(tm.tick_at(tm.seconds(tick)), tick)

    def test_default_tempo_and_tempo_on_other_track(self):
        m = build(tracks=[[], [ch(0, 0x90, 60, 1), tempo(480, 250000), ch(960, 0x80, 60, 0)]])
        tm = S.TempoMap(m)
        self.assertAlmostEqual(tm.seconds(480), 0.5)    # 120 bpm default until the change
        self.assertAlmostEqual(tm.seconds(960), 0.75)   # then 240 bpm

    def test_same_tick_last_wins(self):
        m = build(tracks=[[tempo(0, 500000), tempo(0, 1000000)]])
        self.assertAlmostEqual(S.TempoMap(m).seconds(480), 1.0)


class MidiEndTests(unittest.TestCase):
    def test_last_note_off_not_last_event(self):
        t0 = [tempo(0, 500000)]
        t1 = [ch(0, 0x90, 60, 100), ch(480, 0x80, 60, 0), ch(4800, 0xB0, 7, 0)]  # CC after the notes
        t2 = [ch(0, 0x90, 64, 100), ch(1440, 0x90, 64, 0)]                      # note-off via vel 0
        m = build(tracks=[t0, t1, t2])
        self.assertEqual(S.note_end_tick(m), 1440)
        self.assertAlmostEqual(S.midi_end_seconds(m), 1.5)
        self.assertEqual(m.last_tick(), 4800)

    def test_hanging_note_ends_at_track_end(self):
        t1 = [ch(0, 0x90, 60, 100), S.Event(2400, "meta", 0xFF, b"", 0x2F)]
        m = build(tracks=[[tempo(0, 500000)], t1])
        self.assertEqual(S.note_end_tick(m), 2400)

    def test_honours_tempo_changes(self):
        t0 = [tempo(0, 500000), tempo(960, 1000000)]
        t1 = [ch(0, 0x90, 60, 100), ch(1920, 0x80, 60, 0)]
        m = build(tracks=[t0, t1])
        self.assertAlmostEqual(S.midi_end_seconds(m), 1.0 + 2.0)


class EndMarkerTests(unittest.TestCase):
    def test_marker_position_and_duration(self):
        t0 = [tempo(0, 500000), tempo(960, 1000000)]
        t1 = [ch(0, 0x90, 60, 100), ch(1920, 0x80, 60, 0)]
        m = build(tracks=[t0, t1])
        end = S.midi_end_seconds(m)   # 3.0 s
        tick, secs = canon.append_end_marker(m, end, 3.0)
        marker = m.tracks[0][-1]
        self.assertEqual(marker.kind, "meta")
        self.assertEqual(marker.meta_type, 0x01)
        self.assertEqual(marker.data, canon.END_MARKER)
        self.assertEqual(marker.tick, tick)
        self.assertGreaterEqual(secs, 6.0 - 1e-9)
        self.assertLess(secs - 6.0, 1.0 / 480 * 1.0)       # within one tick at 60 bpm
        self.assertAlmostEqual(S.TempoMap(m).seconds(tick), 6.0)
        self.assertEqual(canon.duration_D(end, 3.0), 6)
        # the marker is the last event of the file -> players render through it
        back = S.parse(S.serialize(m))
        self.assertEqual(back.last_tick(), tick)

    def test_duration_rounding(self):
        self.assertEqual(canon.duration_D(186.178, 3), 190)
        self.assertEqual(canon.duration_D(1.0, 3), 4)
        self.assertEqual(canon.duration_D(1.0001, 3), 6)
        self.assertEqual(canon.duration_D(0.0, 3), 4)

    def test_marker_not_before_existing_track0_events(self):
        m = build(tracks=[[tempo(0, 500000), S.Event(99999, "meta", 0xFF, b"x", 0x01)],
                          [ch(0, 0x90, 60, 100), ch(480, 0x80, 60, 0)]])
        tick, _ = canon.append_end_marker(m, 0.5, 3.0)
        self.assertEqual(tick, 99999)


class TrimTests(unittest.TestCase):
    def make(self):
        t0 = [tempo(0, 500000), S.Event(4000, "meta", 0xFF, b"", 0x2F)]
        t1 = [ch(0, 0xC0, 19),
              ch(0, 0x90, 60, 100), ch(960, 0x80, 60, 0),      # ends exactly at cut
              ch(480, 0x90, 62, 100), ch(1440, 0x80, 62, 0),   # crosses the cut
              ch(960, 0x90, 64, 100), ch(1200, 0x80, 64, 0),   # starts at cut -> dropped
              ch(2000, 0xB0, 7, 1)]                            # after cut -> dropped
        return build(tracks=[t0, t1])

    def test_cut_closes_and_drops(self):
        m = self.make()
        r = canon.trim(m, 960)
        self.assertEqual(m.last_tick(), 960)
        notes = [e for e in m.tracks[1] if e.kind == "channel" and e.type in (0x80, 0x90)]
        self.assertEqual(len(notes), 4)
        self.assertEqual(S.note_end_tick(m), 960)
        self.assertEqual(r["closed_notes"], 1)
        # dropped: 64 on, 64 off, CC7, the original note-off of 62 at 1440, t0 end-of-track
        self.assertEqual(r["dropped_events"], 5)

    def test_hold_extends_final_chord(self):
        m = self.make()
        r = canon.trim(m, 960, hold_to_tick=1920)
        offs = [e for e in m.tracks[1] if e.kind == "channel" and e.is_note_off()]
        self.assertEqual(sorted(e.tick for e in offs), [1920, 1920])
        self.assertEqual(r["held_notes"], 2)
        self.assertEqual(S.note_end_tick(m), 1920)
        self.assertAlmostEqual(S.midi_end_seconds(m), 2.0)

    def test_hold_before_cut_rejected(self):
        with self.assertRaises(ValueError):
            canon.trim(self.make(), 960, hold_to_tick=100)


if __name__ == "__main__":
    unittest.main()
