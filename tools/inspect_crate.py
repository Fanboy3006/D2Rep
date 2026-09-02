#!/usr/bin/env python3
"""Inspect source2-demo 0.5.8: Cargo.lock packages + sub-crate dota feature deps."""
import io
import re
import tarfile
import urllib.request

UA = {"User-Agent": "dsh-diag/0.1"}
URL = "https://static.crates.io/crates/source2-demo/source2-demo-0.5.8.crate"


def main():
    data = urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=60).read()
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")

    lock = tf.extractfile("source2-demo-0.5.8/Cargo.lock").read().decode()
    pkgs = re.findall(r'name = "([^"]+)"\nversion = "([^"]+)"', lock)
    print("total lock packages:", len(pkgs))
    for n, v in pkgs:
        print(f"  {n} {v}")

    print("\n== source2-demo-protobufs Cargo.toml (normalized) ==")
    try:
        t = tf.extractfile("source2-demo-0.5.8/protobufs/Cargo.toml").read().decode()
    except KeyError:
        # find any Cargo.toml under protobufs path
        cands = [m.name for m in tf.getmembers() if m.name.endswith("protobufs/Cargo.toml")]
        print("candidates:", cands)
        t = tf.extractfile(cands[0]).read().decode() if cands else ""
    print(t[:3000])


if __name__ == "__main__":
    main()
