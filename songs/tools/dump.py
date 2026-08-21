#!/usr/bin/env python3
"""Print a per-track / per-channel summary of an SMF (debug helper)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smf as S

def main(paths):
    for p in paths:
        m = S.load(p)
        tm = S.TempoMap(m)
        print("== %s  format=%d div=%d tracks=%d last_tick=%d end=%.3fs note_end=%.3fs"
              % (p, m.format, m.division, len(m.tracks), m.last_tick(), tm.seconds(m.last_tick()), S.midi_end_seconds(m)))
        tempos = [(ev.tick, round(6e7/ev.tempo_us(),2)) for ev in m.all_events() if ev.is_tempo()]
        print("   tempos:", tempos[:8], "..." if len(tempos) > 8 else "", "(%d)" % len(tempos))
        for i, t in enumerate(m.tracks):
            names = [ev.data.decode("latin-1") for ev in t if ev.kind == "meta" and ev.meta_type in (0x03, 0x04)]
            chans = {}
            for ev in t:
                if ev.kind != "channel":
                    continue
                c = chans.setdefault(ev.channel, {"notes": 0, "prog": [], "cc": set(), "bend": 0, "lo": 128, "hi": -1})
                if ev.is_note_on():
                    c["notes"] += 1; c["lo"] = min(c["lo"], ev.data[0]); c["hi"] = max(c["hi"], ev.data[0])
                elif ev.type == 0xC0:
                    c["prog"].append((ev.tick, ev.data[0]))
                elif ev.type == 0xB0:
                    c["cc"].add(ev.data[0])
                elif ev.type == 0xE0:
                    c["bend"] += 1
            print("   track %2d %-30s %s" % (i, names[:2], ""))
            for ch, c in sorted(chans.items()):
                print("      ch %2d notes=%5d range=%3d..%3d prog=%s cc=%s bend=%d" % (ch+1, c["notes"], c["lo"], c["hi"], c["prog"][:4], sorted(c["cc"]), c["bend"]))

if __name__ == "__main__":
    main(sys.argv[1:])
