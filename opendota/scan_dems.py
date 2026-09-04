import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEM = os.path.join(ROOT, "dems", "public")
MAGIC = b"PBDEMS2\x00"

bad, total, size = [], 0, 0
per_league = {}
for league in sorted(os.listdir(DEM)):
    d = os.path.join(DEM, league)
    if not os.path.isdir(d):
        continue
    cnt = 0
    for name in sorted(os.listdir(d)):
        if not name.endswith(".dem"):
            continue
        p = os.path.join(d, name)
        try:
            with open(p, "rb") as f:
                head = f.read(8)
        except OSError as e:
            bad.append((p, "read error %r" % e))
            cnt += 1
            continue
        total += 1
        sz = os.path.getsize(p)
        size += sz
        cnt += 1
        if head != MAGIC:
            bad.append((p, "magic %r" % head))
    per_league[league] = cnt

for k in sorted(per_league):
    print("league", k, "dems", per_league[k])
print("TOTAL dems=%d size=%.1fGB" % (total, size / 1e9))
print("BAD:", len(bad))
for p, why in bad[:20]:
    print("  ", p, why)
