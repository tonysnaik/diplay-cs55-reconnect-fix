# -*- coding: utf-8 -*-
"""DiPlay 车机日志统计器。

把 DiPlay 生成的运行日志（txt）扫一遍，输出：
  * 成功会话数 / 无线启动失败数，失败按原因分类
  * 每次失败后使用的退避间隔分布（2s/4s/8s/16s/30s ...）
  * 从 "bring-up starting" 到会话建立的耗时（min/median/max）
  * 车机与手机的环境事实（Android API、board、build、分辨率、蓝牙/Wi-Fi 状态、iPhone 型号）

用法：
    python analyze_log.py <日志目录或若干 txt> [--md] [--out FILE]

例：
    python analyze_log.py ./logs
    python analyze_log.py ./logs --md --out report.md

脚本只读日志，不联网、不上传任何内容。日志里的设备名 / 蓝牙 MAC / IP
不会被打印（统计只依赖结构化字段），可安全分享输出。
"""

import argparse
import collections
import glob
import io
import os
import re
import sys

TS_RE = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d+)")
RETRY_RE = re.compile(r"retrying in (\d+)ms")
CLOSE_RE = re.compile(r"airplay control closing reason=(.*?)\s+activeStreams")

ENV_PATTERNS = [
    ("android", r"Android\s+(?P<v>[0-9.]+)\s*/\s*API\s*(?P<api>\d+)"),
    ("android_api", r"Android API=(\d+) release=([0-9.]+)"),
    ("board", r"Head-unit board:\s*([^;]+)"),
    ("hardware", r"hardware:\s*([^;]+)"),
    ("build", r"build:\s*([0-9.]+)"),
    ("screen", r"(\d{3,4})x(\d{3,4})"),
    ("iphone", r"(iPhone\d+,\d+)\s+ios=([0-9.]+)\s+airplay=([0-9.]+)"),
    ("wifi", r"WiFi service=(\w+)\s+enabled=(\w+)\s+p2pFeature=(\w+)"),
    ("bt", r"Bluetooth adapter=(\w+)\s+enabled=(\w+)"),
]

FAIL_BUCKETS = [
    ("P2P not startable", ("Wi-Fi Direct could not start",)),
    ("control bind no address", ("EADDRNOTAVAIL",)),
    ("BT RFCOMM unavailable", ("RFCOMM",)),
    ("existing wifi unavailable", ("EXISTING_WIFI",)),
]


def ts(line):
    m = TS_RE.match(line)
    if not m:
        return None
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.txt")) + glob.glob(os.path.join(p, "*.log")))
        elif os.path.exists(p):
            files.append(p)
    if not files:
        sys.exit("没有找到日志文件：%s" % " ".join(paths))
    return files


def bucket(fail_lines):
    c = collections.Counter()
    for l in fail_lines:
        for name, keys in FAIL_BUCKETS:
            if any(k in l for k in keys):
                c[name] += 1
                break
        else:
            c["other"] += 1
    return c


def env_facts(text):
    facts = collections.OrderedDict()
    for name, pat in ENV_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        facts[name] = " / ".join(g for g in m.groups() if g)
    return facts


def durations(lines, text):
    starts, end_marker = [], "session established atNs"
    out = []
    start = None
    for l in lines:
        if "wireless bring-up generation=" in l and "starting" in l:
            start = ts(l)
        elif end_marker in l and start is not None:
            end = ts(l)
            if end is not None and end >= start:
                out.append(end - start)
            start = None
    return out


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def analyse(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    text = "\n".join(lines)

    succ = sum(1 for l in lines if "SUCCESS: AirPlay session active" in l)
    fails = [l for l in lines if "bring-up failed" in l]
    ends = sum(1 for l in lines if "AirPlay session ended" in l)
    closes = collections.Counter(m.group(1) for l in lines for m in [CLOSE_RE.search(l)] if m)
    delays = collections.Counter(RETRY_RE.findall(text))
    durs = durations(lines, text)

    return {
        "name": os.path.basename(path),
        "lines": len(lines),
        "succ": succ,
        "fails": len(fails),
        "buckets": bucket(fails),
        "ends": ends,
        "closes": closes,
        "delays": delays,
        "durs": durs,
        "env": env_facts(text),
    }


def render_text(results):
    out = []
    for r in results:
        out.append("#### %s (%d 行)" % (r["name"], r["lines"]))
        if r["env"]:
            out.append("  环境: " + "; ".join("%s=%s" % kv for kv in r["env"].items()))
        out.append("  成功会话: %d" % r["succ"])
        out.append("  无线启动失败: %d  %s" % (r["fails"], dict(r["buckets"])))
        out.append("  会话结束事件: %d  %s" % (r["ends"], dict(r["closes"])))
        if r["durs"]:
            out.append("  建链耗时(s): n=%d min=%.1f median=%.1f max=%.1f"
                       % (len(r["durs"]), min(r["durs"]), median(r["durs"]), max(r["durs"])))
        total = sum(r["delays"].values())
        dist = ", ".join("%sms×%s" % (k, v) for k, v in sorted(r["delays"].items(), key=lambda x: int(x[0])))
        out.append("  退避分布: %s (共 %d 次)" % (dist, total))
        out.append("")
    # 汇总
    out.append("## 汇总")
    out.append("  成功会话合计: %d" % sum(r["succ"] for r in results))
    out.append("  失败合计: %d" % sum(r["fails"] for r in results))
    agg = collections.Counter()
    for r in results:
        agg.update(r["buckets"])
    out.append("  失败分类: %s" % dict(agg))
    if agg:
        out.append("  最大单一原因占比: %.0f%%" % (100.0 * agg.most_common(1)[0][1] / sum(agg.values())))
    slow = sum(v for r in results for k, v in r["delays"].items() if int(k) >= 30000)
    out.append("  30 秒及以上退避次数: %d" % slow)
    return "\n".join(out)


def render_md(results):
    out = ["# DiPlay 车机日志统计", "", "## 逐份日志", "",
           "| 日志 | 行数 | 成功会话 | 启动失败 | 建链耗时(s) |",
           "|---|---|---|---|---|"]
    for r in results:
        d = "-"
        if r["durs"]:
            d = "n=%d, min=%.1f, 中位=%.1f, max=%.1f" % (len(r["durs"]), min(r["durs"]), median(r["durs"]), max(r["durs"]))
        out.append("| %s | %d | %d | %d | %s |" % (r["name"], r["lines"], r["succ"], r["fails"], d))
    out += ["", "## 失败原因分布", "", "| 日志 | " + " | ".join(k for k, _ in FAIL_BUCKETS + [("other", ())]) + " |",
            "|---|" + "---|" * (len(FAIL_BUCKETS) + 1)]
    for r in results:
        out.append("| %s | %s |" % (r["name"], " | ".join(str(r["buckets"].get(k, 0)) for k, _ in FAIL_BUCKETS + [("other", ())])))
    out += ["", "## 退避间隔分布", "", "| 日志 | 分布 |", "|---|---|"]
    for r in results:
        out.append("| %s | %s |" % (r["name"], ", ".join(
            "%sms×%s" % (k, v) for k, v in sorted(r["delays"].items(), key=lambda x: int(x[0])))))
    out += ["", "## 环境", "", "| 日志 | 环境事实 |", "|---|---|"]
    for r in results:
        out.append("| %s | %s |" % (r["name"], "; ".join("%s=%s" % kv for kv in r["env"].items()) or "-"))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="DiPlay 车机日志统计")
    ap.add_argument("paths", nargs="+", help="日志文件或包含 *.txt 的目录")
    ap.add_argument("--md", action="store_true", help="输出 Markdown 表格")
    ap.add_argument("--out", help="写入文件而不是打印")
    args = ap.parse_args()

    results = [analyse(p) for p in collect(args.paths)]
    body = render_md(results) if args.md else render_text(results)
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        print("written: %s" % args.out)
    else:
        print(body)


if __name__ == "__main__":
    main()
