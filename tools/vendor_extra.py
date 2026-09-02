#!/usr/bin/env python3
"""Add extra crates (serde_json, ryu) to the offline vendor directory."""
import hashlib
import io
import json
import os
import tarfile
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(ROOT, "vendor")
UA = {"User-Agent": "dsh-offline-vendor/0.1 (offline cargo vendoring)"}

# name -> version to vendor (latest satisfying the others' requirements)
EXTRA = {
    "serde_json": "1.0.145",
    "ryu": "1.0.20",
}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=120).read()


def main():
    for name, ver in EXTRA.items():
        dest = os.path.join(VENDOR_DIR, f"{name}-{ver}")
        if os.path.exists(os.path.join(dest, ".cargo-checksum.json")):
            print("skip existing", name, ver)
            continue
        url = f"https://static.crates.io/crates/{name}/{name}-{ver}.crate"
        print("fetching", url)
        data = fetch(url)
        pkg = hashlib.sha256(data).hexdigest()
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
            json.dump({"files": files, "package": pkg}, f, indent=1, sort_keys=True)
        print("vendored", name, ver, "package", pkg)


if __name__ == "__main__":
    main()
