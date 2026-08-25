"""The publish mutex, and the static guarantee that every writer of songs.json takes it.

The routes need FastAPI (absent here), so the second half reads routes_publish.py with ast:
each publishops write call must sit inside a `with publocks.exclusive(...)` block. That is
what the first attempt at #19 got wrong — the mutex was inside RunManager.finish(), so the
Published tab's remove/prune/rebuild rewrote out/public and songs.json beside a finisher.
"""
import ast
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin import publocks  # noqa: E402

SFADMIN = os.path.join(HERE, "..", "sfadmin")
# publishops calls that write out/public and/or s3://<site>/songs.json
WRITERS = ("sync_down", "rebuild_and_publish", "resync_and_publish", "remove_track", "prune")


class ExclusiveTests(unittest.TestCase):
    def test_second_writer_is_refused_while_the_first_holds_it(self):
        with publocks.exclusive("first"):
            self.assertEqual(publocks.held_by()[0], "first")
            with self.assertRaises(publocks.Busy) as cm:
                with publocks.exclusive("second"):
                    self.fail("two writers in the critical section")
            self.assertIn("first", str(cm.exception))
        self.assertIsNone(publocks.held_by())
        with publocks.exclusive("third"):  # released, so the next writer gets in
            pass

    def test_the_slot_is_released_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with publocks.exclusive("boom"):
                raise ValueError("x")
        self.assertIsNone(publocks.held_by())

    def test_a_waiting_writer_gets_in_when_the_holder_leaves(self):
        got = threading.Event()
        started = threading.Event()

        def waiter():
            started.set()
            with publocks.exclusive("waiter", timeout=5):
                got.set()

        t = threading.Thread(target=waiter)
        with publocks.exclusive("holder"):
            t.start()
            self.assertTrue(started.wait(5))
            self.assertFalse(got.wait(0.3))     # queued behind the holder, not running
        self.assertTrue(got.wait(5))
        t.join(5)

    def test_a_bounded_wait_gives_up_instead_of_hanging(self):
        with publocks.exclusive("holder"):
            t0 = time.monotonic()
            with self.assertRaises(publocks.Busy):
                with publocks.exclusive("waiter", timeout=0.2):
                    self.fail("two writers in the critical section")
            self.assertLess(time.monotonic() - t0, 3)


def _imported_writers(tree: ast.AST) -> dict[str, str]:
    """local name -> writer, for `from .publishops import prune [as p]`. Without this the
    scan below only sees `publishops.prune(...)`, and a module that imported the name bare
    passed the very guard that exists to catch it (#19)."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("publishops"):
            for alias in node.names:
                if alias.name in WRITERS:
                    found[alias.asname or alias.name] = alias.name
    return found


def _guarded_calls(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(publishops writers called inside a `with publocks.exclusive(...)`, and outside one).
    Both call shapes count: `publishops.prune(...)` and a directly imported `prune(...)`."""
    inside: set[str] = set()
    outside: set[str] = set()
    imported = _imported_writers(tree)

    def visit(node, held: bool) -> None:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            guard = any(isinstance(i.context_expr, ast.Call)
                        and isinstance(i.context_expr.func, ast.Attribute)
                        and i.context_expr.func.attr == "exclusive"
                        for i in node.items)
            for i in node.items:      # the context expression runs before the lock is held
                visit(i.context_expr, held)
            for stmt in node.body:
                visit(stmt, held or guard)
            return
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "publishops" and f.attr in WRITERS):
                (inside if held else outside).add(f.attr)
            elif isinstance(f, ast.Name) and f.id in imported:
                (inside if held else outside).add(imported[f.id])
        for child in ast.iter_child_nodes(node):
            visit(child, held)

    visit(tree, False)
    return inside, outside


class RouteGuardTests(unittest.TestCase):
    """Every writer the Published tab calls runs inside the mutex (#19)."""

    def source(self, name: str) -> ast.AST:
        with open(os.path.join(SFADMIN, name)) as fh:
            return ast.parse(fh.read())

    def test_routes_publish_takes_the_mutex_for_every_writer(self):
        inside, outside = _guarded_calls(self.source("routes_publish.py"))
        self.assertEqual(outside, set(), "publishops writer called without publocks.exclusive")
        # rebuild goes through resync_and_publish, which holds OPS_LOCK across
        # sync_down + rebuild_and_publish rather than letting a caller interleave them (#15)
        self.assertEqual(inside, {"remove_track", "prune", "resync_and_publish"})

    def test_the_finisher_takes_the_mutex_too(self):
        inside, outside = _guarded_calls(self.source("renders.py"))
        self.assertEqual(outside, set())
        self.assertEqual(inside, {"sync_down", "rebuild_and_publish"})

    def test_no_module_writes_songs_json_outside_the_mutex(self):
        """The two above are today's writers; this one keeps a third from appearing without
        one. A writer that takes no lock is exactly the shape of the bug (#19)."""
        checked = 0
        for name in sorted(f for f in os.listdir(SFADMIN) if f.endswith(".py")):
            checked += 1
            _, outside = _guarded_calls(self.source(name))
            self.assertEqual(outside, set(), f"{name}: publishops writer outside the mutex")
        self.assertGreater(checked, 5)

    def test_no_module_imports_a_writer_name_directly(self):
        """`from .publishops import prune` then a bare `prune()` is the same bug in a shape
        the scan above had to grow a second case for. Keeping every call site spelled
        `publishops.<writer>(...)` keeps the guard simple and greppable (#19)."""
        for name in sorted(f for f in os.listdir(SFADMIN) if f.endswith(".py")):
            imported = _imported_writers(self.source(name))
            self.assertEqual(imported, {},
                             f"{name}: call it as publishops.{next(iter(imported.values()), '')}()"
                             if imported else "")

    def test_the_scan_catches_a_bare_imported_writer(self):
        """The guard's own regression test: this module shape used to pass it clean."""
        sneaky = ast.parse("from .publishops import prune\n"
                           "def go():\n"
                           "    return prune(dry_run=False)\n")
        self.assertEqual(_guarded_calls(sneaky), (set(), {"prune"}))
        locked = ast.parse("from .publishops import prune as p\n"
                           "def go():\n"
                           "    with publocks.exclusive('x'):\n"
                           "        return p()\n")
        self.assertEqual(_guarded_calls(locked), ({"prune"}, set()))


if __name__ == "__main__":
    unittest.main()
