#!/usr/bin/env python3
"""Extract a downloaded rust toolchain tarball into a target-specific dir,
merging all component subdirectories (rustc, cargo, rust-std-*, ...) into one
toolchain root (rustup-style layout). Skips rust-docs (huge, not needed).

Env: TARGET=x86_64-pc-windows-msvc|gnu  (default msvc)
"""
import os
import shutil
import sys
import tarfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TARGET = os.environ.get("TARGET", "x86_64-pc-windows-msvc")
VERSION = "1.98.0"
SRC = os.path.join(ROOT, "downloads", f"rust-{VERSION}-{TARGET}.tar.gz")
DST = os.path.join(ROOT, f"rust_toolchain_{TARGET}")
SKIP = {"rust-docs", "rust-docs-json-preview", "LICENSE-APACHE",
        "COPYRIGHT", "LICENSE-MIT", "README.md", "install.sh", "builder-config",
        "git-commit-hash", "git-commit-info", "rust-installer-version",
        "components", "version"}

os.makedirs(DST, exist_ok=True)
print(f"extracting {SRC} -> {DST}")
with tarfile.open(SRC, mode="r:gz") as tf:
    members = tf.getmembers()
    print(f"{len(members)} members")
    n = 0
    for m in members:
        parts = m.name.split("/")
        if len(parts) < 3:
            continue  # top-level meta files
        comp = parts[1]
        if comp in SKIP:
            continue
        rel = "/".join(parts[2:])
        if not rel:
            continue
        dst_path = os.path.join(DST, rel)
        if m.isdir():
            os.makedirs(dst_path, exist_ok=True)
        elif m.isfile():
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with tf.extractfile(m) as srcf, open(dst_path, "wb") as outf:
                shutil.copyfileobj(srcf, outf)
            n += 1
        if n % 5000 == 0:
            print(f"  ... {n} files", flush=True)
print(f"EXTRACT DONE ({n} files)")

