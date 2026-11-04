# DiPlay × 长安 CS55 车机：无线 CarPlay 连不上的诊断笔记与修复方法

一套可复现的**诊断 + 修复方法**，用于「DiPlay 在这台老车机上连不上/连上又掉」的问题。
仓库里**只有脚本、数据和结论，不含任何 APK、不含原软件的代码或资源、不含个人日志原文**。

- 目标设备：东软（Neusoft）车机，型号 CHANGAN，`build 01.05.14502.09341`，Android 4.3 / API 18，armeabi-v7a，737 MB RAM，800×480
- 涉及的软件：DiPlay（包名 `com.shihab.diplay`）0.3.13 / 0.3.14 / 0.3.15
- 结论：**不是 CarPlay/MFi 协议不兼容，也不是 Wi‑Fi Direct 无线电坏了**；是启动前置检查 + 重试节奏的问题

> 本仓库是"方法"，不是"改版安装包"。请只对自己合法拥有/已授权的软件与设备使用这些步骤。

---

## 1. 现象

- 车机上 DiPlay 一直转圈，偶尔能连上，多数时候连不上；
- 连上后能正常出画面，但会莫名断开；
- 车机上重启 DiPlay、重开 Wi‑Fi 有时能好使，有时要等半分钟以上。

## 2. 根因（三层）

| 层 | 机制 | 占失败比例 |
|---|---|---|
| 直接原因 | 拆掉上一个 P2P 热点后车机 Wi‑Fi 处于关闭态，`checkPrerequisites()` 读到 `isWifiEnabled()==false` 就**直接抛异常，这一轮彻底放弃**（日志里从 "starting" 到 "failed" 只隔 60 毫秒） | **约 89%**（62/70） |
| 放大器 | 退避间隔 `2s→4s→8s→16s→30s`，最多 5 次后放弃；日志里 30 秒档出现 38 次，用户感受即"卡死" | — |
| 次要原因 | 蓝牙 RFCOMM `read ret: -1`（蓝牙被关/Siri 抢占控制通道）；会话建立后 `ECONNRESET` / `Socket closed` / `peer EOF` | 约 8% |

**被排除的怀疑**：协议层不兼容（同一环境下成功会话 40 次、建链中位 5.6 秒）、P2P 不可用（成功时 `p2p0 / 2.4GHz / ch1 / 2412MHz / error=none`）、MFi 认证失败（日志为本地离线 MFi，且能认证通过）。

## 3. 数据

三份日志的统计（由 `tools/analyze_log.py` 生成，未做任何手工修改）：

| 日志 | 行数 | 成功会话 | 启动失败 | P2P 被拒 | 30 秒退避 | 建链耗时 |
|---|---|---|---|---|---|---|
| `DiPlay-20261031-110942-510` | 5084 | 18 | 24 | 20 | 10 | 中位 5.6s（4.4–9.4） |
| `DiPlay-20261031-111159-318` | 5274 | 18 | 23 | 20 | 10 | 中位 5.6s（4.4–9.4） |
| `DiPlay-20261102-111553-162` | 3412 | 4 | 23 | 22 | 18 | 5.6s / 39.3s（n=2） |
| **合计** | 13770 | **40** | **70** | **62** | **38** | — |

详细分析见 [`docs/diagnosis.md`](docs/diagnosis.md)，改动清单见 [`docs/patch-notes.md`](docs/patch-notes.md)。

## 4. 修复（4 处，语义级改动）

| # | 位置 | 改动 | 为什么 |
|---|---|---|---|
| 1 | `LegacyWifiP2pGroupManager.checkPrerequisites()` | Wi‑Fi 关闭时不再抛异常，改为**主动开启 Wi‑Fi 并最多等待 8 秒**（40×200ms 轮询），仍失败才报错（并给出可读的中文提示） | 覆盖 89% 的失败路径 |
| 2 | `WirelessStartupRetryBudget.nextDelayMillis()` | 退避上限 `30000ms → 5000ms`（基数 2000ms 不变） | 失败后 5 秒内就重试，而不是干等半分钟 |
| 3 | `CarPlayHostActivity.reconnectAfterLoss()` | 重连退避上限 `30000ms → 5000ms`（iAP2 隧道那条 15000ms 不动） | 同上 |
| 4 | 版本号 | `versionCode 135→136`，`versionName → 0.3.15-CS55fix` | 装完能肉眼确认是哪一个包 |

CarPlay / iAP2 / MFi 协议层**一个字节都没动**。

## 5. 怎么用

### 5.1 先看日志，确认是不是同一类问题

```bash
python tools/analyze_log.py <DiPlay 日志目录或若干 txt>          # 纯文本
python tools/analyze_log.py ./logs --md --out report.md          # Markdown 表格
```

输出包含：成功/失败计数、失败按原因分类、退避间隔分布、建链耗时、环境事实（Android API、board、build、分辨率、蓝牙/Wi‑Fi 状态、iPhone 型号）。
脚本不打印设备名/MAC/IP，输出可直接分享。

判据：**若"P2P 被拒"占失败数的绝大头，且日志里 `bring-up failed` 紧跟在 `legacy Wi-Fi Direct starting` 之后几十毫秒，就是本文档这一类问题。**

### 5.2 自己改包（可选，按需）

```bash
# 1) 反编译你合法拥有的 APK
apktool d DiPlay-x.y.z.apk -o decoded

# 2) 按 docs/patch-notes.md 改三处 smali（改源码，不要盲目改二进制）

# 3) 重打包
apktool b decoded -o patched-unsigned.apk

# 4) 去掉旧签名（否则会和你的新签名打架），resources.arsc 必须保持 STORED
python - <<'PY'
import zipfile
z = zipfile.ZipFile("patched-unsigned.apk")
drop = [n for n in z.namelist() if n.upper().startswith("META-INF/")
        and n.upper().endswith((".SF", ".RSA", ".DSA", ".MF"))]
out = zipfile.ZipFile("patched-clean.apk", "w", zipfile.ZIP_DEFLATED)
for n in z.namelist():
    if n in drop:
        continue
    out.writestr(z.getinfo(n), z.read(n),
                 compress_type=zipfile.ZIP_STORED if n == "resources.arsc" else zipfile.ZIP_DEFLATED)
out.close()
PY

# 5) 签名。Android 4.3/API 18 只认 v1，所以显式用 jarsigner
jarsigner -keystore my.keystore -digestalg SHA1 -sigalg SHA1withRSA patched-clean.apk myalias
```

### 5.3 交付前体检

```bash
python tools/verify_repack.py --original old.apk --patched new.apk \
    --aapt /path/to/aapt --expect-classes <原包类数>
```

它会检查：zip 完整性、条目数/目录结构是否与原包一致、每个 dex 的类数（直接读 dex 头，不需要反编译）、v1 签名是否存在、`aapt dump badging` 的逐行差异、**资源 ID 全表是否逐个对齐**（这是"重打包后 App 不崩"的关键指标）。

`aapt` 可从 `apktool.jar` 里直接取：`prebuilt/windows/aapt.exe`（linux/macos 同名目录）。

## 6. 边界（重要）

- **未做真机验证**：本仓库结论基于日志统计 + 静态/字节码级校验，没有在车机上实测。请自行确认后再长期使用。
- 签名换了 → 装新包前**必须先卸载旧版**（否则签名冲突）。
- 蓝牙那 ~8% 的失败（`RFCOMM read: -1`、蓝牙被关、Siri 抢占控制通道）**没有修**，只是重试变快了。
- 该车机 737MB 内存 / 800×480，视频 15fps 上下已是性能上限（日志里出现过 `decode p90=288ms`、`late=24`），这属于性能问题，不是连接问题。

## 7. 目录

```
.
├─ README.md
├─ LICENSE                  # 仅覆盖本仓库的脚本与文档
├─ docs/
│  ├─ diagnosis.md          # 完整诊断（含证据与排除项）
│  └─ patch-notes.md        # 三处 smali 改动的语义、片段与自检方法
├─ tools/
│  ├─ analyze_log.py        # 日志统计（无需第三方依赖）
│  └─ verify_repack.py      # 重打包产物体检（aapt 可选）
└─ samples/
   ├─ sample-log-report.txt # analyze_log.py 的真实输出样例
   └─ sample-verify.txt     # verify_repack.py 的真实输出样例
```

## 8. 隐私与法律

- 仓库不含日志原文、不含设备名/蓝牙 MAC/IP、不含车机照片。
- 仓库不含 DiPlay 的 APK、dex、资源或反编译产物；`docs/patch-notes.md` 只描述"改了什么、为什么"，代码片段是本仓库作者新写的 smali，不是原软件的成段代码。
- 这些步骤只应用于你合法拥有或已获授权的软件与设备；不要分发改版安装包。

## 9. 许可

脚本与文档：MIT（见 `LICENSE`）。第三方软件名称与商标归其各自所有者。
