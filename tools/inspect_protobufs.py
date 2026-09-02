#!/usr/bin/env python3
"""Inspect source2-demo-protobufs 0.5.8: build.rs, generated code presence."""
import io
import tarfile
import urllib.request

UA = {"User-Agent": "dsh-diag/0.1"}
URL = "https://static.crates.io/crates/source2-demo-protobufs/source2-demo-protobufs-0.5.8.crate"


def main():
    data = urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=60).read()
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    names = [m.name for m in tf.getmembers() if m.isfile()]
    print("files:", len(names))
    for n in names[:40]:
        print("  ", n)
    if len(names) > 40:
        print("   ... (+%d more)" % (len(names) - 40))

    for target in ["source2-demo-protobufs-0.5.8/build.rs",
                   "source2-demo-protobufs-0.5.8/Cargo.toml"]:
        if target in names:
            content = tf.extractfile(target).read().decode("utf-8", "replace")
            print(f"\n===== {target} =====")
            print(content[:5000])


if __name__ == "__main__":
    main()
