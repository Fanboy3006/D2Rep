#!/usr/bin/env python3
"""Lock-driven fully-offline vendoring for source2-demo 0.5.8.

Authoritative source: the Cargo.lock shipped inside source2-demo-0.5.8.crate
(108 packages, exact versions + checksums). Every package is downloaded from
static.crates.io, sha256-verified against the lock, extracted into vendor/,
and given a .cargo-checksum.json so `cargo build --offline` never goes online.
"""
import hashlib
import io
import json
import os
import re
import tarfile
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(ROOT, "vendor")
DL_DIR = os.path.join(ROOT, "downloads", "crates")
UA = {"User-Agent": "dsh-offline-vendor/0.1 (offline cargo vendoring)"}

ROOT_CRATE_URL = "https://static.crates.io/crates/source2-demo/source2-demo-0.5.8.crate"


def fetch(url, timeout=180, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"    retry {i + 1}/{retries} {type(e).__name__}: {e}", flush=True)
    raise last


def parse_lock(text):
    """Parse Cargo.lock v4: returns {name: {version: checksum}}."""
    packages = {}
    blocks = re.split(r"\n\[\[package\]\]\n", "\n" + text)
    for b in blocks:
        m = re.search(r'name = "([^"]+)"', b)
        if not m:
            continue
        name = m.group(1)
        vm = re.search(r'version = "([^"]+)"', b)
        cm = re.search(r'checksum = "([^"]+)"', b)
        if vm:
            packages.setdefault(name, {})[vm.group(1)] = cm.group(1) if cm else None
    return packages


def extract_to(dest, data, pkg_sha):
    os.makedirs(dest, exist_ok=True)
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    files = {}
    for m in tf.getmembers():
        if not m.isfile():
            continue
        rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
        if not rel or rel.startswith(".."):
            continue
        content = tf.extractfile(m).read()
        full = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        files[rel] = hashlib.sha256(content).hexdigest()
    tf.close()
    with open(os.path.join(dest, ".cargo-checksum.json"), "w", encoding="utf-8") as f:
        json.dump({"files": files, "package": pkg_sha}, f, indent=1, sort_keys=True)


def main():
    os.makedirs(VENDOR_DIR, exist_ok=True)
    os.makedirs(DL_DIR, exist_ok=True)

    print("== fetching source2-demo-0.5.8.crate for its Cargo.lock ==")
    root_data = fetch(ROOT_CRATE_URL)
    with open(os.path.join(DL_DIR, "source2-demo-0.5.8.crate"), "wb") as f:
        f.write(root_data)
    tf = tarfile.open(fileobj=io.BytesIO(root_data), mode="r:gz")
    lock_text = tf.extractfile("source2-demo-0.5.8/Cargo.lock").read().decode()
    tf.close()

    packages = parse_lock(lock_text)
    total = sum(len(vs) for vs in packages.values())
    print(f"== lock has {total} package versions across {len(packages)} names ==")

    to_fetch = []
    for name, vers in packages.items():
        for ver, ck in vers.items():
            to_fetch.append((name, ver, ck))
    to_fetch.sort()

    done = 0
    for name, ver, ck in to_fetch:
        dest = os.path.join(VENDOR_DIR, f"{name}-{ver}")
        done += 1
        if os.path.exists(os.path.join(dest, ".cargo-checksum.json")):
            print(f"[{done}/{total}] skip existing {name}-{ver}")
            continue
        url = f"https://static.crates.io/crates/{name}/{name}-{ver}.crate"
        print(f"[{done}/{total}] {name}-{ver}", flush=True)
        data = fetch(url)
        actual = hashlib.sha256(data).hexdigest()
        if ck and actual != ck:
            raise SystemExit(f"CHECKSUM MISMATCH for {name}-{ver}: {actual} != {ck}")
        extract_to(dest, data, actual)
        with open(os.path.join(DL_DIR, f"{name}-{ver}.crate"), "wb") as f:
            f.write(data)

    cargo_dir = os.path.join(ROOT, ".cargo")
    os.makedirs(cargo_dir, exist_ok=True)
    with open(os.path.join(cargo_dir, "config.toml"), "w", encoding="utf-8") as f:
        f.write(
            "[source.crates-io]\n"
            'replace-with = "vendored-sources"\n\n'
            "[source.vendored-sources]\n"
            'directory = "vendor"\n'
        )
    print("== wrote .cargo/config.toml ==")
    print("== VENDORING COMPLETE ==")


if __name__ == "__main__":
    main()
