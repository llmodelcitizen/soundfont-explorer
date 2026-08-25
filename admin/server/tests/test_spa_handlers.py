"""Every SPA click handler that writes must show the server's refusal (no JS runner here —
a source check, in the spirit of test_systemd_units).

The admin routes 409 on purpose now: the publish mutex refuses a second writer immediately
and names the holder, and POST /api/runs/{rid}/finish refuses a run already being finished
(#19). An unhandled rejection left the status line on "finisher running…" for ever, with
the reason only in the devtools console.
"""
import os
import re
import unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "src")
WRITES = re.compile(r"\bawait (post|del|patch)\(")
HANDLER = re.compile(r"\.on(?:click|change|submit) = async \([^)]*\) => \{")


def block(text: str, start: int) -> str:
    """The `{ ... }` body whose opening brace `start` points just past."""
    depth, i = 1, start
    while depth and i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start:i]


def handlers(name: str) -> list[tuple[str, str]]:
    with open(os.path.join(SRC, name)) as fh:
        text = fh.read()
    out = []
    for m in HANDLER.finditer(text):
        # name the handler by the line it starts on, so a failure says which button
        line = text.count("\n", 0, m.start()) + 1
        out.append((f"line {line}", block(text, m.end())))
    return out


class WriteHandlerTests(unittest.TestCase):
    def test_every_writing_handler_reports_a_refusal(self):
        seen = 0
        for name in sorted(f for f in os.listdir(SRC) if f.endswith(".ts")):
            for where, body in handlers(name):
                if not WRITES.search(body):
                    continue
                seen += 1
                self.assertIn("catch (", body, f"{name} {where}: a write with no catch")
        self.assertGreaterEqual(seen, 4, "the handler scan found nothing — the pattern moved")

    def test_the_scan_would_notice_an_unhandled_write(self):
        bare = "  x.onclick = async () => {\n    await post('/api/x');\n  };\n"
        body = block(bare, HANDLER.search(bare).end())
        self.assertTrue(WRITES.search(body))
        self.assertNotIn("catch (", body)


if __name__ == "__main__":
    unittest.main()
