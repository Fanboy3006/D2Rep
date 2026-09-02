#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""decode_replay.py - decode a raw CDN replay download into a parseable .dem.

ARCHITECTURE.md §4.2 note (2026-09): Valve's replay CDN objects
(replay*.valve.net) are now zstandard-compressed instead of bz2. This tool
sniffs the real container by magic number and writes the decompressed .dem:

    raw[:4] == 28 B5 2F FD   -> zstd (current valve.net format)
    raw[:3] == BZh           -> bz2 (legacy / some CN hosts)
    raw[:8] == PBDEMS2\0     -> already an uncompressed Source2 demo

The output must start with b"PBDEMS2\\x00" (Source2 demo magic) - that is the
byte stream the source2-demo based parser (dota_parse) expects.

Usage:
    python decode_replay.py <input.raw> [output.dem]
    cat <input.raw> | python decode_replay.py - > output.dem

Dependency: zstandard for zstd streams (`pip install zstandard`; inside the dsh
sandbox point pip's temp dir and --target into the workspace, or extract the
cp3xx win_amd64 wheel manually and add it to PYTHONPATH). bz2/stdlib only
otherwise. Prints are ASCII-only on purpose (console code-page safety).
"""
import bz2
import sys

MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"
MAGIC_BZ2 = b"BZh"
MAGIC_DEM = b"PBDEMS2\x00"

try:
    import zstandard
except ImportError:
    zstandard = None


def decode(raw):
    """Return (ok, dem_bytes, note)."""
    if raw[:4] == MAGIC_ZSTD:
        if zstandard is None:
            return False, None, ("zstd stream but zstandard module missing - "
                                 "pip install zstandard (see module docstring)")
        try:
            out = zstandard.ZstdDecompressor().decompress(
                raw, max_output_size=2 * 1024 * 1024 * 1024)
        except Exception as e:
            return False, None, "zstd decompress failed: %r" % e
        note = "zstd"
    elif raw[:3] == MAGIC_BZ2:
        try:
            out = bz2.decompress(raw)
        except Exception as e:
            return False, None, "bz2 decompress failed: %r" % e
        note = "bz2"
    else:
        out = raw
        note = "uncompressed"
    if out[:8] == MAGIC_DEM:
        return True, out, "%s container, Source2 demo magic OK" % note
    return False, None, ("%s container but output magic %r != PBDEMS2\\0 - "
                         "not a Dota 2 replay?" % (note, out[:8]))


def main():
    if len(sys.argv) not in (2, 3):
        print("usage: python decode_replay.py <input.raw> [output.dem]", file=sys.stderr)
        return 2
    src = sys.argv[1]
    if src == "-":
        raw = sys.stdin.buffer.read()
    else:
        with open(src, "rb") as f:
            raw = f.read()
    print("input: %d bytes, head=%s" % (len(raw), raw[:8].hex()), flush=True)
    ok, dem, note = decode(raw)
    if not ok:
        print("decode FAILED: %s" % note, flush=True)
        return 1
    print("decode OK (%s): %d bytes" % (note, len(dem)), flush=True)
    if len(sys.argv) == 3 and sys.argv[2] != "-":
        with open(sys.argv[2], "wb") as f:
            f.write(dem)
        print("wrote %s" % sys.argv[2], flush=True)
    else:
        sys.stdout.buffer.write(dem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
