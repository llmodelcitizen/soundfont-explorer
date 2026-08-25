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
WRITERS = ("sync_down", "rebuild_and_publish", "remove_track", "prune")


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


def _guarded_calls(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(publishops writers called inside a `with publocks.exclusive(...)`, and outside one)."""
    inside: set[str] = set()
    outside: set[str] = set()

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
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "publishops"
                and node.func.attr in WRITERS):
            (inside if held else outside).add(node.func.attr)
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
        self.assertEqual(inside, set(WRITERS))

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


if __name__ == "__main__":
    unittest.main()
