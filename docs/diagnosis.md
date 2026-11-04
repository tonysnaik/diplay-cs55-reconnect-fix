# 诊断：为什么这台车机连不上无线 CarPlay

> 数据来源：DiPlay 自带的运行日志（3 份，共 13770 行）+ 车机版本信息页照片。
> 统计由 `tools/analyze_log.py` 生成；本文只记录结论与证据，不含日志原文中的设备名 / MAC / IP。

## 1. 环境基线

| 项目 | 值 | 来源 |
|---|---|---|
| 车机厂商 / 型号 | Neusoft / CHANGAN（板级 `C3ALFUS`） | 照片 + 日志 `Head-unit board` |
| 系统版本 | `01.05.14502.09341`（MCU `01.05.14531`，导航 `2.7.8.22550`，OTA `SW.C.0.5`） | 照片 + 日志 `build` |
| Android | 4.3 / API 18（系统显示与真实 API 一致，无伪装） | 日志 + 自检页 |
| CPU / 内存 / 屏幕 | armeabi-v7a / 737 MB / 800×480（物理 300×180mm） | 照片 + 日志 |
| 蓝牙 | 适配器存在，名称 CHANGAN，BLE 支持；但日志里有 `enabled=false` 的时段 | 照片 + 日志 |
| Wi‑Fi | `service=true enabled=true p2pFeature=true` | 日志 |
| 手机 | `iPhone14,5` / `ios=26.5.2` / `airplay=950.7.1` | 日志 |
| DiPlay | 0.2.90 / 0.3.13 / 0.3.14 / 0.3.15（versionCode 110/133/134/135，minSdk 17，4 个 dex） | APK 元信息 |

> 车机自带的旧检测页写「WiFi 直连(P2P)：不支持」，但日志里 `p2pFeature=true`，且确实成功建起过 `p2p0` 分组——该判定不成立，不能据此认定硬件不支持。

## 2. 统计结果

| 指标 | 10‑31 11:09 | 10‑31 11:11 | 11‑02 11:15 | 合计 |
|---|---|---|---|---|
| 日志行数 | 5084 | 5274 | 3412 | 13770 |
| 成功会话 | 18 | 18 | 4 | **40** |
| 无线启动失败 | 24 | 23 | 23 | **70** |
| └ P2P 不可启动（Wi‑Fi 关闭被拒） | 20 | 20 | 22 | **62** |
| └ 控制通道绑定失败 `EADDRNOTAVAIL` | 3 | 3 | 0 | 6 |
| └ 蓝牙 RFCOMM 不可用 | 0 | 0 | 1 | 1 |
| └ EXISTING_WIFI 不可用 | 1 | 0 | 0 | 1 |
| 退避 2s / 4s / 8s / 16s / 30s | 9/4/3/3/10 | 9/4/3/3/10 | 4/2/1/1/18 | 30s 档共 **38** |
| 建链耗时 | 中位 5.6s（4.4–9.4，n=9） | 中位 5.6s（4.4–9.4，n=9） | 5.6s 与 39.3s（n=2） | — |

单看比例：**62/70 = 89%** 的失败都落在同一条路径上。

## 3. 证据链

### 3.1 失败发生在"开始"之后 60 毫秒内

```
wireless legacy Wi-Fi Direct starting
CONNECTION_DIAGNOSTIC ... teardown resource=legacy-wifi-p2p completed=true elapsedMs=10
wireless bring-up failed: IOException: Wi-Fi Direct could not start on this device;
   use the car hotspot: Turn on Wi-Fi in the head unit settings before using Wi-Fi Direct
   @ CarPlayController.startLegacyWifiDirectWithFallback(...)
   <- IOException: Turn on Wi-Fi in the head unit settings before using Wi-Fi Direct
   @ LegacyWifiP2pGroupManager.checkPrerequisites(...)
```

时间戳差值：开始 → 拆组 18ms，开始 → 抛错 61ms。**一次真正的 P2P 尝试都没有发生**，说明是前置检查直接退出，而不是"试了但失败"。

### 3.2 同一环境下链路是通的

```
Legacy Wi-Fi Direct radio iface=p2p0 frequency=2412 error=none
Legacy Wi-Fi Direct ready iface=p2p0 band=2.4 GHz channel=1 frequency=2412
WIRELESS-CHAIN [6/12] hotspot ready backend=Wi-Fi P2P iface=p2p0
wireless Bonjour services started mode=interface iface=p2p0
WIRELESS-CHAIN [4/12] iAP2 CSM channel opened over RFCOMM
wireless startup elapsedMs=5629 authenticated=true ... sessionActive=true
```

→ 无线电、P2P 分组、Bonjour 广播、iAP2 隧道、MFi 认证全部可用，中位建链 5.6 秒。

### 3.3 失败与"拆组"强相关

每次失败前都紧跟着 `teardown resource=legacy-wifi-p2p`。也就是说：**拆上一个热点后，车机 Wi‑Fi 状态翻回关闭**，下一轮（尤其是自动重连）一进来就撞上 Wi‑Fi 关闭分支。

### 3.4 重试节奏把"偶发"放大成"不可用"

```
retrying in 2000ms → 4000ms → 8000ms → 16000ms → 30000ms
```

`WirelessStartupRetryBudget.nextDelayMillis()` 上限 30 秒、`retries>=5` 直接放弃；`reconnectAfterLoss()` 同款上限。一旦失败一次，用户就要干等 30 秒以上——这是"连不上/一直转圈"的直接体感来源。

### 3.5 次要原因：蓝牙与控制通道

```
Bluetooth RFCOMM unavailable: read failed, socket might closed or timeout, read ret: -1
```

11‑02 日志里连续 4 次 RFCOMM 尝试全灭，之后才回落到 Wi‑Fi 发现；另外 `Bluetooth adapter enabled=false` 的时段必然失败。
会话建立后还有 `control I/O failed: recvfrom failed: ECONNRESET`、`Socket closed`、`peer EOF`——日志中出现 `speechMode=1`（Siri/语音），控制链路被 iPhone 侧重置。

## 4. 为什么不是别的原因

| 怀疑 | 为什么不成立 |
|---|---|
| CarPlay / AirPlay 版本不兼容 | 同环境成功建立 40 次会话，`airplay=950.7.1` 协商正常 |
| Wi‑Fi Direct 硬件不支持 | 成功路径里 `p2p0` 分组、2412MHz、ch1 全部正常；`p2pFeature=true` |
| MFi 认证失败 | 日志为 `MFI 本地离线`，且认证通过（`authenticated=true`） |
| 屏幕/性能导致连接失败 | 性能问题只影响画面（15fps、`decode p90` 偏大），不影响握手 |
| 车机自带检测页说"P2P 不支持" | 该检测用的是 `hasSystemFeature` 判定，与实际能否建组不一致（见上） |

## 5. 结论

1. 主因是**前置检查把"Wi‑Fi 暂时关闭"当成"环境不合格"直接放弃**，而车机在拆组后恰好处于这个状态；
2. 次因是**退避过钝**（30 秒上限 + 5 次即弃），把偶发失败放大成"不可用"；
3. 蓝牙与控制通道的问题是真实存在但占比小的第三层，本次未处理。

修复方案见 [`patch-notes.md`](patch-notes.md)。
