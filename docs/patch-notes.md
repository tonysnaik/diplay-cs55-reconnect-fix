# 补丁说明：改了哪几处、为什么、怎么确认改进去了

> 本文只描述**改动语义**与**新增的代码片段**，不复制原软件的成段代码或资源。
> 目标：在自己的环境里复现同类修复，或核对别人的包是不是真改了这三处。

## 0. 三个目标方法

| 文件（反编译后的相对路径） | 方法 | 作用 |
|---|---|---|
| `smali_classes*/com/shilapi/xcertplay/network/LegacyWifiP2pGroupManager.smali` | `checkPrerequisites()V` | 建热点前的环境检查，Wi‑Fi 关闭时抛异常 |
| `smali_classes*/com/shilapi/xcertplay/WirelessStartupRetryBudget.smali` | `nextDelayMillis(J)J`（或同签名方法） | 计算下一次无线启动的重试间隔，有上限与放弃点 |
| `smali_classes*/com/shilapi/xcertplay/CarPlayHostActivity.smali` | `reconnectAfterLoss(...)` | 会话丢失后的重连退避上限 |

版本不同，类可能落在 `smali_classes2` / `smali_classes3`；用 `grep -rn "checkPrerequisites"` 定位即可。

---

## 1. 前置检查：从"直接放弃"改为"自己开 + 等"

**原行为（散文描述）**：读 `WifiManager.isWifiEnabled()`，若为 false，立刻抛出一个"请去车机设置里打开 Wi‑Fi"的 IOException；调用方捕获后结束本轮启动，不做任何补救。

**新行为**：

1. 读到未开启时，尝试 `setWifiEnabled(true)`；用 `try/catch Exception` 包住，失败就继续往下走（有些车机 ROM 会因权限或系统策略拒绝）；
2. 然后**轮询等待**：最多 40 次 × 200ms（共 8 秒），每次重读 `isWifiEnabled()`；一旦为 true 就继续正常建组；
3. 8 秒还没开起来，才抛出异常，文案换成能指导用户的中文。

为什么是 8 秒：日志里车机冷启动 Wi‑Fi 的可见耗时在 3–5 秒量级，留一倍余量；再长会让 UI 卡住感更强。

新增片段（smali，示意；寄存器编号随版本不同）：

```smali
    # 主动开启 Wi-Fi，异常吞掉继续
    :try_start_0
    invoke-virtual {v0, v1}, Landroid/net/wifi/WifiManager;->setWifiEnabled(Z)Z
    :try_end_0
    .catch Ljava/lang/Exception; {:try_start_0 .. :try_end_0} :catch_0

    # 轮询等待最多 40 次 × 200ms = 8s
    const/4 v1, 0x0
    :goto_wait
    const/16 v2, 0x28          # 40
    if-ge v1, v2, :cond_exit
    invoke-virtual {v0}, Landroid/net/wifi/WifiManager;->isWifiEnabled()Z
    move-result v3
    if-eqz v3, :cond_sleep
    goto :cond_exit

    :cond_sleep
    const-wide/16 v4, 0xc8     # 200ms
    invoke-static {v4, v5}, Ljava/lang/Thread;->sleep(J)V
    add-int/lit8 v1, v1, 0x1
    goto :goto_wait

    :cond_exit
    # 仍然 false → 抛中文提示
    const-string/jumbo v1, "请在车机设置中打开 Wi-Fi（无线网络）后重试：DiPlay 需要用车机 Wi-Fi 创建 CarPlay 热点"
```

> 注意：`Thread.sleep` 在 UI 线程上会卡界面。若要更稳，应改为"启动后异步等待 + 到点再判"，本文的原实现选择简单直接，因为该分支只在启动路径上短暂触发。

---

## 2/3. 退避上限：30 秒 → 5 秒

**原行为**：`min((1 << retries) * 2000, 30000)`，`retries >= 5` 返回"放弃"。即 2s → 4s → 8s → 16s → 30s → 放弃。
`reconnectAfterLoss` 里另有一处 30000ms 的上限。

**新行为**：把上限常量 `0x7530`（30000）改成 `0x1388`（5000），基数 2000 不动。
效果：2s → 4s → 5s → 5s → 5s，5 次覆盖约 21 秒，而不是 60 秒以上。

```smali
    # 原来是 const-wide/16 v2, 0x7530  (30000ms)
    const-wide/16 v2, 0x1388        # 5000ms
```

**刻意没动的**：
- iAP2 隧道那条 15000ms（`0x3a98`）的重试——协议层的节奏不该跟着改；
- 蓝牙自动开启——改系统设置、可能撞上车机自己的 BT 服务，属于需要机主确认的行为；
- CarPlay / iAP2 / MFi 协议实现。

---

## 4. 版本号

`apktool.yml` 里 `versionCode: 135 → 136`、`versionName: 0.3.15 → 0.3.15-CS55fix`。
目的：装完能一眼确认是改过的包（也避免"到底装没装新的"这种排查成本）。

---

## 5. 怎么确认补丁真的在包里（不靠"构建成功"）

反编译**成品** APK（`apktool d --no-res` 足够，把资源解码省掉），然后按字节码特征核对：

```bash
apktool d --no-res new.apk -o verify

grep -n "const/16 v2, 0x28"      verify/smali*/com/shilapi/xcertplay/network/LegacyWifiP2pGroupManager.smali
grep -n "0x1388"                verify/smali*/com/shilapi/xcertplay/WirelessStartupRetryBudget.smali
grep -n "0x1388"                verify/smali*/com/shilapi/xcertplay/CarPlayHostActivity.smali
grep -n "u8bf7u5728"            verify/smali*/com/shilapi/xcertplay/network/LegacyWifiP2pGroupManager.smali   # 中文文案的 unicode 转义
grep -c  "Turn on Wi-Fi in the head unit settings" \
     -r verify/smali*           # 应该 0 命中：旧英文文案已被替换
```

再跑结构体检：

```bash
python tools/verify_repack.py --original old.apk --patched new.apk --aapt /path/to/aapt --expect-classes <原包类数>
```

要点：**类数必须与原包一致**（只改常量不会增删类），**资源 ID 表必须逐个对齐**（重打包最容易在这里翻车，导致 App 一进就崩）。

---

## 6. 踩过的坑（重打包时大概率会撞上）

| 现象 | 原因 | 处理 |
|---|---|---|
| `aapt` 报 `Illegal byte sequence` / `I/O error` | 路径含中文/非 ASCII | 先把 APK 复制到纯 ASCII 路径再 `dump` |
| 装不上，或"应用未安装" | 签名不匹配（原包签名 vs 你的签名） | 先卸载旧版；或用与旧包相同的签名 |
| 装上了但一启动就崩 | `resources.arsc` 被压缩，或资源 ID 顺序变了 | `resources.arsc` 必须 `ZIP_STORED`；用 `verify_repack.py` 比对 ID 表 |
| Android 4.x 提示"解析包时出错" | 只签了 v2/v3，没有 v1 | `jarsigner -digestalg SHA1 -sigalg SHA1withRSA`（v1） |
| 改完类数对不上 | 用了会增删类的工具/混淆 | 只改常量与分支；核对 `dex class_defs_size` 之和 |
