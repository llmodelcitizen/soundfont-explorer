#!/usr/bin/env python3
"""Tiny stdlib static server with HTTP Range support for the M0b prototype pages.

python3 -m http.server ignores Range headers (it always answers 200 + full body), which would
make the SFPK "header + one member" measurement meaningless, so this server implements
single-range requests (RFC 9110 section 14) on top of SimpleHTTPRequestHandler.

Usage:   python3 web/test/proto/serve.py [--port 8765] [--bind 0.0.0.0] [--root <repo root>]
Then open  http://<host>:8765/web/test/proto/decode-probe.html  (etc.)

Extras used by the pages:
  GET /__ls/<dir>     -> JSON {"dirs": [...], "files": [...]} (sorted), so pages can discover
                         which variants / segments exist under work/ without hard-coding.
  Cache-Control: no-store on everything so "cold" measurements are really cold.
"""
import argparse
import json
import os
import re
import socket
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".opus": "audio/ogg; codecs=opus",
        ".ogg": "audio/ogg",
        ".pk": "application/octet-stream",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".wasm": "application/wasm",
        ".json": "application/json",
        ".md": "text/markdown; charset=utf-8",
    }
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        # headers and body are written separately; without TCP_NODELAY Nagle + delayed ACK adds ~40 ms
        # to small keep-alive responses (seen on the first Range member fetch)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, fmt, *args):  # quieter, but keep range info visible
        if os.environ.get("SERVE_QUIET"):
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    # --- directory listing as JSON -------------------------------------------------------
    def do_GET(self):
        if self.path.startswith("/__ls/"):
            return self._ls(self.path[len("/__ls/"):].split("?", 1)[0])
        return super().do_GET()

    def _ls(self, rel):
        path = os.path.normpath(os.path.join(self.directory, rel.strip("/")))
        if not path.startswith(os.path.abspath(self.directory)) or not os.path.isdir(path):
            self.send_error(HTTPStatus.NOT_FOUND, "no such dir")
            return
        dirs, files = [], []
        for name in sorted(os.listdir(path)):
            (dirs if os.path.isdir(os.path.join(path, name)) else files).append(name)
        body = json.dumps({"dirs": dirs, "files": files}).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- Range support ----------------------------------------------------------------------
    def send_head(self):
        self.range = None
        hdr = self.headers.get("Range")
        if hdr is None:
            return super().send_head()
        m = RANGE_RE.match(hdr.strip())
        if not m:
            return super().send_head()  # ignore unparseable ranges -> full 200
        path = self.translate_path(self.path)
        if os.path.isdir(path) or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        first, last = m.group(1), m.group(2)
        if first == "" and last == "":
            return super().send_head()
        if first == "":  # suffix range: last N bytes
            n = int(last)
            if n == 0:
                return self._416(size)
            start, end = max(0, size - n), size - 1
        else:
            start = int(first)
            end = size - 1 if last == "" else min(int(last), size - 1)
        if start >= size or start > end:
            return self._416(size)
        f = open(path, "rb")
        f.seek(start)
        self.range = (start, end)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return f

    def _416(self, size):
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", "bytes */%d" % size)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def copyfile(self, source, outputfile):
        if self.range is None:
            return super().copyfile(source, outputfile)
        start, end = self.range
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--root", default=default_root, help="directory to serve (default: repo root)")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)

    class H(RangeHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=root, **kw)

    httpd = ThreadingHTTPServer((args.bind, args.port), H)
    httpd.daemon_threads = True
    print("serving %s on http://%s:%d/  (pages: /web/test/proto/*.html)" % (root, args.bind, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
