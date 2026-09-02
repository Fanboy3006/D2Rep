#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cargo_net_proxy.py - local plain-HTTP mirror of crates.io.

Why this exists
---------------
On Windows, cargo (and curl / .NET) do TLS through schannel. In some sandboxed
process contexts (e.g. shell jobs spawned by the dsh harness) schannel fails
with SEC_E_NO_CREDENTIALS for every https host, while python's OpenSSL stack
still connects fine. This script is the transport shim for that case: cargo
talks plain http to 127.0.0.1 and this proxy relays to crates.io over https
using python. No vendor/ directory, no `--offline`: cargo still resolves and
downloads real crates from the live index, exactly like an online build.

How to use
----------
1. Start the proxy (leave it running while cargo builds):
       python tools/cargo_net_proxy.py [--port 45817]
2. Point cargo's crates-io source at the mirror (CLI only; nothing to edit):
       cargo build --release \
         --config 'source.crates-io.replace-with="mirror"' \
         --config 'source.mirror.registry="sparse+http://127.0.0.1:45817/index/"'

Path mapping (sparse index protocol)
------------------------------------
    /index/config.json                       -> https://index.crates.io/config.json,
                                                with "dl" rewritten to this server
    /index/<anything else>                   -> https://index.crates.io/<anything>
    /dl/<name>/<ver>/<name>-<ver>.crate       -> https://static.crates.io/crates/<...>

Index files are fetched fresh on every request. Downloaded .crate files are
cached under <project>/.cargo-home/mirror_cache (immutable content, safe to
reuse across builds). Only the python standard library is used.

NOTE: output is deliberately ASCII-only so the log survives any console
code page on Windows.
"""

import argparse
import http.server
import os
import re
import sys
import urllib.error
import urllib.request

HOST = "127.0.0.1"
INDEX_BASE = "https://index.crates.io"
STATIC_BASE = "https://static.crates.io/crates"
UA = "cargo-net-proxy/0.1 (python stdlib)"

CACHE_DIR = None
PORT = 0

# cargo requests crates from the configured "dl" as
#   {dl}/{crate}/{version}/download            (crates.io CDN redirect form)
#   {dl}/{crate}/{version}/{crate}-{version}.crate   (direct file form)
CRATE_PATH = re.compile(
    r"^/dl/([A-Za-z0-9_-]+)/([0-9A-Za-z.+-]+)/(?:download|[A-Za-z0-9_.+-]+\.crate)$"
)


def fetch(url, timeout=180):
    """GET url, return (status, headers, body). Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:  # DNS/TLS/conn refused/...
        return 502, {"Content-Type": "text/plain; charset=utf-8"}, str(e).encode("utf-8", "replace")


def serve(handler, status, body, content_type):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except Exception:
        pass  # client went away; nothing we can do


def cached_crate_file(rel):
    """rel like 'serde/1.0.219/serde-1.0.219.crate'; returns cache path."""
    safe = rel.replace("/", os.sep)
    return os.path.join(CACHE_DIR, safe)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "cargo-net-proxy/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def do_GET(self):
        try:
            self._handle()
        except Exception as e:
            print("error handling %s: %r" % (self.path, e), flush=True)
            try:
                serve(self, 500, repr(e).encode("utf-8", "replace"),
                      "text/plain; charset=utf-8")
            except Exception:
                pass

    def _handle(self):
        path = self.path.split("?", 1)[0]
        print("GET %s" % path, flush=True)

        if path == "/index/config.json":
            status, _h, body = fetch(INDEX_BASE + "/config.json")
            if status == 200:
                dl = "http://%s:%d/dl" % (HOST, PORT)
                body = body.replace(b"https://static.crates.io/crates", dl.encode("ascii"))
            serve(self, status, body, "application/json; charset=utf-8")
            return

        if path.startswith("/dl/"):
            m = CRATE_PATH.match(path)
            if not m:
                serve(self, 400, b"bad crate path", "text/plain")
                return
            rel = path[len("/dl/"):]
            cache_path = cached_crate_file(rel)
            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    body = f.read()
                print("  dl cache hit: %s (%d bytes)" % (rel, len(body)), flush=True)
                serve(self, 200, body, "application/octet-stream")
                return
            name, ver = m.group(1), m.group(2)
            # /download form hits the crates.io CDN endpoint which redirects to
            # the real .crate file; urllib follows the redirect automatically.
            url = "%s/%s/%s/download" % (STATIC_BASE, name, ver)
            status, _h, body = fetch(url)
            if status == 200 and body:
                try:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    tmp = cache_path + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(body)
                    os.replace(tmp, cache_path)
                    print("  dl cached: %s (%d bytes)" % (rel, len(body)), flush=True)
                except OSError as e:
                    print("  dl cache write failed: %r (serving anyway)" % e, flush=True)
            else:
                print("  dl upstream status %d for %s" % (status, url), flush=True)
            serve(self, status if status != 502 else 502, body, "application/octet-stream")
            return

        if path.startswith("/index/"):
            rest = path[len("/index/"):]
            status, _h, body = fetch("%s/%s" % (INDEX_BASE, rest))
            serve(self, status, body, "text/plain; charset=utf-8")
            return

        serve(self, 404, b"not found", "text/plain")


def main():
    global CACHE_DIR, PORT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=45817)
    ap.add_argument("--cache-dir", default=None,
                    help="default: <project root>/.cargo-home/mirror_cache")
    args = ap.parse_args()
    PORT = args.port

    if args.cache_dir:
        CACHE_DIR = os.path.abspath(args.cache_dir)
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cargo_home = os.environ.get("CARGO_HOME")
        base = cargo_home if cargo_home else os.path.join(root, ".cargo-home")
        CACHE_DIR = os.path.join(base, "mirror_cache")
    os.makedirs(CACHE_DIR, exist_ok=True)
    print("cache dir: %s" % CACHE_DIR, flush=True)

    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print("cargo mirror listening on http://%s:%d" % (HOST, PORT), flush=True)
    print("point cargo at it with:", flush=True)
    print("  --config 'source.crates-io.replace-with=\"mirror\"'", flush=True)
    print("  --config 'source.mirror.registry=\"sparse+http://%s:%d/index/\"'"
          % (HOST, PORT), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
