# -*- coding: utf-8 -*-
"""对 apktool 重打包后的 APK 做"可安装性"体检。

检查项（不需要安卓设备）：
  1. zip 完整性 + 条目数 / 顶层目录结构 与原包一致
  2. 每个 dex 的 class_defs_size（直接读 dex 头，不用反编译）
  3. 签名：是否含 v1 签名（META-INF/*.SF + *.RSA），并提示 API<24 只能用 v1
  4. 若提供 --aapt：badging 逐行 diff（应只剩 versionCode/versionName 与图标路径差异）
  5. 若提供 --aapt：资源 ID 全表比对（防止资源重排导致 App 内 R 引用错位）

用法：
    python verify_repack.py --original old.apk --patched new.apk \
        --aapt /path/to/aapt --expect-classes 18583

退出码：0 表示没有硬伤；1 表示有失败项（会逐条打印 FAIL）。
"""

import argparse
import collections
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile

RES_ID_RE = re.compile(r"\bresource (0x7f[0-9a-f]{6}) [\w.]+:([A-Za-z0-9_./-]+?):")


class Report(object):
    def __init__(self):
        self.lines = []
        self.failed = 0

    def ok(self, label, detail=""):
        self.lines.append("  PASS  %-42s %s" % (label, detail))

    def bad(self, label, detail=""):
        self.failed += 1
        self.lines.append("  FAIL  %-42s %s" % (label, detail))

    def info(self, label, detail=""):
        self.lines.append("  info  %-42s %s" % (label, detail))


def dex_class_count(blob):
    """读 dex header 里 class_defs_size（offset 0x60）。"""
    if len(blob) < 0x70 or blob[:4] != b"dex\n":
        return None
    return struct.unpack("<I", blob[0x60:0x64])[0]


def dex_entries(z):
    return sorted(n for n in z.namelist() if re.match(r"classes\d*\.dex$", n))


def run_aapt(aapt, *args):
    try:
        out = subprocess.run([aapt] + list(args), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=300)
    except Exception as e:
        return None, str(e)
    return out.stdout.decode("utf-8", "replace"), out.stderr.decode("utf-8", "replace")


def parse_res_ids(dump):
    d = {}
    for line in dump.splitlines():
        m = RES_ID_RE.search(line)
        if m:
            d.setdefault(m.group(1), m.group(2))
    return d


def main():
    ap = argparse.ArgumentParser(description="apktool 重打包产物体检")
    ap.add_argument("--original", required=True)
    ap.add_argument("--patched", required=True)
    ap.add_argument("--aapt", help="aapt 可执行文件路径（从 apktool.jar 的 prebuilt 目录提取）")
    ap.add_argument("--expect-classes", type=int, help="期望的 dex 类总数（一般等于原包）")
    args = ap.parse_args()

    r = Report()
    zo = zipfile.ZipFile(args.original)
    zp = zipfile.ZipFile(args.patched)

    # 1. zip
    for tag, z in (("original", zo), ("patched", zp)):
        bad = z.testzip()
        r.ok("zip integrity (%s)" % tag) if bad is None else r.bad("zip integrity (%s)" % tag, str(bad))

    no, np_ = zo.namelist(), zp.namelist()
    r.ok("entry count") if len(no) == len(np_) else r.bad("entry count", "%d vs %d" % (len(no), len(np_)))
    top = lambda names: collections.Counter(n.split("/")[0] if "/" in n else "(root)" for n in names)
    to, tp = top(no), top(np_)
    r.ok("top-level structure") if to == tp else r.bad("top-level structure", "%s vs %s" % (dict(to), dict(tp)))

    # 2. dex class count
    total_o = 0
    total_p = 0
    for name in dex_entries(zo):
        total_o += dex_class_count(zo.read(name)) or 0
    for name in dex_entries(zp):
        n = dex_class_count(zp.read(name))
        total_p += n or 0
        (r.bad if n is None else r.info)("%s classes" % name, str(n))
    if total_o and total_p == total_o:
        r.ok("dex total classes", "%d (= original)" % total_p)
    elif total_o:
        r.bad("dex total classes", "%d vs original %d" % (total_p, total_o))
    if args.expect_classes is not None:
        r.ok("dex classes == expected", str(total_p)) if total_p == args.expect_classes \
            else r.bad("dex classes == expected", "%d vs %d" % (total_p, args.expect_classes))

    # 3. v1 signature
    sigs = [n for n in np_ if n.upper().startswith("META-INF/") and n.upper().endswith((".SF", ".RSA", ".DSA"))]
    if sigs:
        r.ok("v1 signature present", ", ".join(sigs))
        r.info("note", "若目标设备是 Android < 7.0 (API<24)，只认 v1 签名；v2/v3 缺失属正常")
    else:
        r.bad("v1 signature present", "没有 META-INF/*.SF|*.RSA —— 设备会拒绝安装")

    # 4/5. aapt 相关
    if args.aapt:
        if not os.path.exists(args.aapt):
            r.bad("aapt exists", args.aapt)
        else:
            bo, bad_o = run_aapt(args.aapt, "dump", "badging", args.original)
            bp, bad_p = run_aapt(args.aapt, "dump", "badging", args.patched)
            if bo is None or bp is None:
                r.bad("aapt badging", (bad_o or bad_p or "")[:120])
            else:
                lo, lp = bo.strip().splitlines(), bp.strip().splitlines()
                diff = [l for l in set(lo) ^ set(lp)]
                if diff:
                    r.info("badging diff", "%d 行不同（正常情况下应只有 versionCode/Name 与图标路径）" % len(diff))
                    for l in sorted(diff)[:12]:
                        r.lines.append("        " + l[:140])
                else:
                    r.ok("badging identical")
                a = re.search(r"versionCode='(\d+)' versionName='([^']*)'", bp)
                if a:
                    r.info("patched version", "versionCode=%s versionName=%s" % (a.group(1), a.group(2)))

            ro, re_o = run_aapt(args.aapt, "dump", "resources", args.original)
            rp, re_p = run_aapt(args.aapt, "dump", "resources", args.patched)
            if ro is None or rp is None:
                r.bad("aapt resources", (re_o or re_p or "")[:120])
            else:
                do, dp = parse_res_ids(ro), parse_res_ids(rp)
                only_o = set(do) - set(dp)
                only_p = set(dp) - set(do)
                mism = [k for k in do if k in dp and do[k] != dp[k]]
                if not only_o and not only_p and not mism:
                    r.ok("resource ids identical", "%d ids, 0 mismatch" % len(do))
                else:
                    r.bad("resource ids identical",
                          "only_orig=%d only_patched=%d renamed=%d" % (len(only_o), len(only_p), len(mism)))
                    for k in sorted(list(only_o))[:5]:
                        r.lines.append("        only-orig %s %s" % (k, do[k]))
                    for k in sorted(list(only_p))[:5]:
                        r.lines.append("        only-patched %s %s" % (k, dp[k]))
                    for k in mism[:5]:
                        r.lines.append("        renamed %s %s -> %s" % (k, do[k], dp[k]))
    else:
        r.info("skip", "未提供 --aapt：badging 与资源 ID 比对未执行（建议补上）")

    print("\n".join(r.lines))
    print("\n%s (%d failed)" % ("ALL CHECKS PASSED" if r.failed == 0 else "SOME CHECKS FAILED", r.failed))
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
