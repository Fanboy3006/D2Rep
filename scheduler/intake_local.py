#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intake_local.py — 私人 / 本地录像录入（Q7B）。

和 `intake_private.py`（老的第 1 层实验）的区别：
  · 目录按 **scope 分组**：`dems/local/<scope>/<match>.dem`（scope = 你自己的分组名，
    如 scrim / team_ts / 2026q1 / steam_76561198…）；
  · 解析产物写进 **Q7 用的主库**：`dems/db_full/local/<scope>/<match>.db`
    （Q7 的 find_db 是递归 glob，所以放这里就能直接被 `q7_replay.py` 找到）；
  · 登记进 catalog 时带 `source='local'` + `scope`，复合主键 (source, scope, match_id)，
    与联赛场次互不覆盖；
  · .dem 默认**保留**（owner 定：留着），可选 `--move` 挪到 `<scope>/registered/`；
  · 支持直接放入压缩包：`.bz2` / `.zst` / `.zstd` 会自动解压成 `.dem` 再处理
    （Valve 的 replay_url 现在实际吐的是 zstd，后缀仍写 .bz2）。

用法（在项目根目录跑）：
    python scheduler/intake_local.py                     # 扫 dems/local/*/ 下的 .dem
    python scheduler/intake_local.py --scope scrim       # 只扫某个 scope
    python scheduler/intake_local.py --dir <某文件夹> --scope <名>
    python scheduler/intake_local.py --no-parse          # 只登记不解析
    python scheduler/intake_local.py --move              # 处理完把 .dem 挪到 registered/
    python scheduler/intake_local.py --list              # 看 catalog 里的本地场次
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_ROOT = os.path.join(ROOT, "dems", "local")
DBFULL_ROOT = os.path.join(ROOT, "dems", "db_full", "local")
COMPRESSED_EXT = (".bz2", ".zst", ".zstd", ".gzip")


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            blk = f.read(chunk)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def run_captured(cmd, env=None):
    """跑外部 exe，stdout/stderr 落临时文件再读（避免管道捕获在某些沙箱里被拦）。"""
    tmp = os.path.join(ROOT, ".tmp")
    os.makedirs(tmp, exist_ok=True)
    o = os.path.join(tmp, "intake_local_out_%d.txt" % os.getpid())
    e = os.path.join(tmp, "intake_local_err_%d.txt" % os.getpid())
    with open(o, "wb") as fo, open(e, "wb") as fe:
        proc = subprocess.run(cmd, stdout=fo, stderr=fe, env=env)
    out = open(o, "rb").read().decode("utf-8", "replace")
    err = open(e, "rb").read().decode("utf-8", "replace")
    for p in (o, e):
        try:
            os.remove(p)
        except OSError:
            pass
    return proc.returncode, out, err


def read_header(parse_bin, dem, dll):
    env = dict(os.environ)
    if dll:
        env["DOTA_PARSE_SQLITE_DLL"] = dll
    rc, out, err = run_captured([parse_bin, "--info", dem], env=env)
    if rc != 0:
        raise RuntimeError("--info 失败 rc=%d：%s" % (rc, err.strip()[-400:]))
    return json.loads(out)


def parse_full(parse_bin, dem, db, dll, interval=1):
    env = dict(os.environ)
    if dll:
        env["DOTA_PARSE_SQLITE_DLL"] = dll
    return run_captured([parse_bin, dem, db, str(interval)], env=env)


def decompress_into_dem(src):
    """把 .bz2 / .zst 解压成同目录的 .dem，返回 .dem 路径（已经是 .dem 就原样返回）。"""
    low = src.lower()
    if not low.endswith(COMPRESSED_EXT):
        return src
    out = src
    for ext in COMPRESSED_EXT:
        if low.endswith(ext):
            out = src[: -len(ext)]
            break
    if not out.lower().endswith(".dem"):
        out += ".dem"
    if os.path.exists(out) and os.path.getsize(out) > 0:
        print("   已有解压结果，跳过：%s" % os.path.basename(out))
        return out
    print("   解压 %s → %s" % (os.path.basename(src), os.path.basename(out)))
    if low.endswith(".bz2"):
        import bz2
        fi = bz2.open(src, "rb")
    else:
        fi = None
        for name in ("compression.zstd", "zstandard", "pyzstd"):
            try:
                m = __import__(name, fromlist=["*"])
                fi = m.open(src, "rb") if hasattr(m, "open") else m.ZstdFile(src, "rb")
                break
            except Exception:
                continue
        if fi is None:
            raise RuntimeError("没有可用的 zstd 库，无法解压 %s" % src)
    with fi, open(out, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 22)
    return out


def looks_like_dem(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"PBDEMS2\x00"
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="要扫描的目录（默认 dems/local/<scope>/ 或全部 scope）")
    ap.add_argument("--scope", default=None, help="scope 名（默认取目录名；--dir 时默认 inbox）")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--parse-bin", default=os.path.join(ROOT, "dota_parse", "target", "release",
                                                        "dota_parse.exe"))
    ap.add_argument("--dll", default=os.path.join(ROOT, "dota_parse", "sqlite3.dll"))
    ap.add_argument("--interval", type=int, default=1, help="快照采样间隔秒（Q7 用 1）")
    ap.add_argument("--no-parse", action="store_true", help="只登记，不解析")
    ap.add_argument("--move", action="store_true", help="处理完把 .dem 挪到 <scope>/registered/")
    ap.add_argument("--note", default=None, help="备注 → metadata_json")
    ap.add_argument("--list", action="store_true", help="只列出 catalog 里的本地场次")
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    from scheduler import catalog as cat

    con = cat.connect(args.catalog)
    cat.ensure_schema(con)

    if args.list:
        rows = cat.list_rows(con, source="local")
        if not rows:
            print("catalog 里还没有本地场次。")
        for r in rows:
            print("  %-12s scope=%-12s state=%-8s 时长=%ss  db=%s" % (
                r["match_id"], r.get("scope") or "-", r["parse_state"], r["duration_sec"],
                os.path.relpath(r["db_path"], ROOT) if r["db_path"] else "-"))
        return 0

    # 决定扫哪些 (scope, dir)
    targets = []
    if args.dir:
        scope = args.scope or os.path.basename(os.path.abspath(args.dir)) or "inbox"
        targets.append((scope, os.path.abspath(args.dir)))
    else:
        if not os.path.isdir(LOCAL_ROOT):
            print("还没有本地录像目录：%s（把 .dem 放进去再跑一次）" % LOCAL_ROOT)
            return 0
        for name in sorted(os.listdir(LOCAL_ROOT)):
            d = os.path.join(LOCAL_ROOT, name)
            if os.path.isdir(d) and not name.startswith("."):
                if args.scope and name != args.scope:
                    continue
                targets.append((name, d))
        if not targets:
            print("dems/local/ 下没有 scope 目录。")
            return 0

    parse_bin = os.path.abspath(args.parse_bin)
    dll = os.path.abspath(args.dll) if args.dll else None
    if not args.no_parse and not os.path.exists(parse_bin):
        print("解析程序不存在：%s（先 cargo build --release）" % parse_bin)
        return 2

    n_new = n_parsed = n_skip = n_fail = 0
    for scope, sdir in targets:
        files = sorted(f for f in os.listdir(sdir)
                       if os.path.isfile(os.path.join(sdir, f))
                       and (f.lower().endswith(".dem") or f.lower().endswith(COMPRESSED_EXT)))
        print("\n== scope=%s（%s）：%d 个待处理文件" % (scope, os.path.relpath(sdir, ROOT), len(files)))
        for name in files:
            src = os.path.join(sdir, name)
            print("  · %s（%.1f MB）" % (name, os.path.getsize(src) / 1e6))
            try:
                dem = decompress_into_dem(src)
            except Exception as e:
                print("    ✗ 解压失败：%s" % e)
                n_fail += 1
                continue
            if not looks_like_dem(dem):
                print("    ✗ 头 8 字节不是 PBDEMS2 —— 不是可解析的录像，跳过")
                n_fail += 1
                continue
            sha = sha256_of(dem)
            try:
                hdr = read_header(parse_bin, dem, dll)
            except Exception as e:
                print("    ✗ 读头部失败：%s" % e)
                n_fail += 1
                continue
            mid = str(hdr.get("match_id") or "").strip()
            id_src = "header"
            if not mid.isdigit() or int(mid) <= 0:
                mid = "local_%s" % sha[:12]
                id_src = "content-hash"
            dur = int(round(hdr.get("duration_seconds") or 0)) or None
            print("    头部：match_id=%s（%s）时长=%ss 玩家=%d"
                  % (mid, id_src, dur, len(hdr.get("players") or [])))

            meta = {"id_source": id_src, "scope": scope, "sha256": sha,
                    "players": [{"name": p.get("player_name"), "hero": p.get("hero_npc"),
                                 "slot": p.get("slot")} for p in (hdr.get("players") or [])]}
            if args.note:
                meta["note"] = args.note

            row = cat.get(con, mid, "local", scope)
            if row is None:
                cat.register(con, mid, source="local", scope=scope, dem_path=dem,
                             dem_sha256=sha, duration_sec=dur, metadata=meta)
                print("    登记为 local/%s/%s" % (scope, mid))
                n_new += 1
                row = cat.get(con, mid, "local", scope)
            else:
                print("    已在 catalog（state=%s）" % row["parse_state"])
                if args.note:
                    cat.set_parse_result(con, mid, metadata_merge={"note": args.note},
                                         source="local", scope=scope)

            dbdir = os.path.join(DBFULL_ROOT, scope)
            os.makedirs(dbdir, exist_ok=True)
            db = os.path.join(dbdir, "%s.db" % mid)
            if args.no_parse:
                print("    --no-parse：保持 pending")
                n_skip += 1
            elif row["parse_state"] == "parsed" and os.path.exists(db):
                print("    已解析过 → 跳过（%s）" % os.path.relpath(db, ROOT))
                n_skip += 1
            else:
                print("    解析 → %s （单核大约 %.0fs）" % (os.path.relpath(db, ROOT),
                                                          os.path.getsize(dem) / 1e6 * 0.36))
                rc, out, err = parse_full(parse_bin, dem, db, dll, args.interval)
                if rc == 0 and os.path.exists(db):
                    tail = [l.strip() for l in out.splitlines() if "[db]" in l or "[done]" in l]
                    for l in tail[-2:]:
                        print("      " + l)
                    cat.set_parse_result(con, mid, db_path=db, state="parsed",
                                         source="local", scope=scope)
                    print("      ✓ %.1f MB" % (os.path.getsize(db) / 1e6))
                    n_parsed += 1
                else:
                    cat.set_parse_result(con, mid, state="failed", source="local", scope=scope,
                                         metadata_merge={"parse_error": err.strip()[-400:]})
                    print("      ✗ 解析失败 rc=%d：%s" % (rc, err.strip()[-300:]))
                    n_fail += 1
                    continue

            if args.move:
                reg = os.path.join(sdir, "registered")
                os.makedirs(reg, exist_ok=True)
                dst = os.path.join(reg, os.path.basename(dem))
                if not os.path.exists(dst):
                    os.replace(dem, dst)
                    cat.update_dem_path(con, mid, dst, source="local", scope=scope)
                    print("      已挪到 %s" % os.path.relpath(dst, ROOT))

    print("\n汇总：新登记 %d ｜ 解析 %d ｜ 跳过 %d ｜ 失败 %d" % (n_new, n_parsed, n_skip, n_fail))
    print("生成页面：python analysis/q7_replay.py <match_id> → python analysis/build_q7_html.py <match_id>")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
