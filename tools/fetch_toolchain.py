#!/usr/bin/env python3
"""Download the Rust stable toolchain tarball for x86_64-pc-windows-msvc.

Usage:
    python fetch_toolchain.py --test      # manifest + partial (1 KiB) download probe
    python fetch_toolchain.py             # full download + sha256 verify
"""
import hashlib
import os
import re
import sys
import urllib.request

BASE = "https://static.rust-lang.org"
MANIFEST = BASE + "/dist/channel-rust-stable.toml"
TARGET = os.environ.get("TARGET", "x86_64-pc-windows-msvc")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "downloads")
OUT_DIR = os.path.abspath(OUT_DIR)


def fetch(url, timeout=120, headers=None, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  retry {i + 1}/{retries} after {type(e).__name__}: {e}")
    raise last


def parse_manifest(text):
    """Extract url/hash for the rust pkg of TARGET from channel-rust-stable.toml."""
    section = re.search(
        r"\[pkg\.rust\.target\." + re.escape(TARGET) + r"\](.*?)(?=\n\[|\Z)",
        text, re.S,
    )
    if not section:
        raise SystemExit(f"target {TARGET} not found in manifest")
    body = section.group(1)
    url = re.search(r"^\s*url\s*=\s*\"([^\"]+)\"", body, re.M).group(1)
    mhash = re.search(r"^\s*hash\s*=\s*\"([^\"]+)\"", body, re.M)
    return url, (mhash.group(1) if mhash else None)


def main():
    test_only = "--test" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)

    print("== fetching manifest ==")
    text = fetch(MANIFEST).decode("utf-8")
    rel_url, sha256 = parse_manifest(text)
    full_url = rel_url if rel_url.startswith("http") else BASE + "/" + rel_url
    print("pkg url :", full_url)
    print("sha256  :", sha256)

    if test_only:
        print("== partial (1 KiB) probe ==")
        req = urllib.request.Request(full_url, headers={"Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            print("status        :", r.status)
            print("content-range :", r.headers.get("Content-Range"))
            print("bytes received:", len(data))
            print("first 4 bytes :", data[:4].hex())
        print("PROBE OK")
        return

    fname = os.path.basename(rel_url)
    out = os.path.join(OUT_DIR, fname)
    print(f"== downloading {full_url} -> {out} ==")
    req = urllib.request.Request(full_url)
    with urllib.request.urlopen(req, timeout=300) as r:
        total = int(r.headers.get("Content-Length", 0))
        print("total bytes   :", total)
        h = hashlib.sha256()
        done = 0
        with open(out, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if done % (50 * 1024 * 1024) < 1024 * 1024:
                    print(f"  ... {done / 1e6:.1f} MB", flush=True)
        print("downloaded    :", done, "bytes")
        print("sha256 actual :", h.hexdigest())
        print("sha256 expect :", sha256)
        if sha256 and h.hexdigest() != sha256:
            raise SystemExit("HASH MISMATCH")
        print("DOWNLOAD OK")


if __name__ == "__main__":
    main()
