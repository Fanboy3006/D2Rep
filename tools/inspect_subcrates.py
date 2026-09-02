#!/usr/bin/env python3
"""Show index entries (deps/features) for source2-demo sub-crates at 0.5.8."""
import json
import urllib.request

UA = {"User-Agent": "dsh-diag/0.1"}


def index_path(name):
    n = len(name)
    if n == 1:
        return f"1/{name}"
    if n == 2:
        return f"2/{name}"
    if n == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[0:2]}/{name[2:4]}/{name}"


def show(name):
    url = f"https://index.crates.io/{index_path(name)}"
    req = urllib.request.Request(url, headers=UA)
    lines = urllib.request.urlopen(req, timeout=30).read().decode().splitlines()
    for line in lines:
        e = json.loads(line)
        if e["vers"] == "0.5.8":
            print(f"== {name} 0.5.8 ==")
            print("features:", json.dumps(e["features"], indent=1))
            print("deps:")
            for d in e["deps"]:
                print("   ", d["name"], d["req"],
                      "kind=", d.get("kind"), "optional=", d.get("optional"),
                      "features=", d.get("features"), "default=", d.get("default_features"),
                      "target=", d.get("target"))
            return
    print(f"== {name}: 0.5.8 not found ==")


show("source2-demo-protobufs")
show("source2-demo-macros")
