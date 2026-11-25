# 创维小湃 P3（Q5001 / cupid-p2，Skyworth Box）

一台 2019 年的创维小湃 P3 **盒子**（不是电视）：Android 10（API 29），全志 H616，2 GB 内存 /
16 GB eMMC，百兆有线网口，**32 位用户空间**（`armeabi-v7a`），无 root（`adbd can't run as root`，
无 su，`/system` 只读）。固件 `cupid_p2-userdebug 10 QP1A.191105.004 eng.Androi.20220117.163650`，
安全补丁 2019-12-05。

报告人 2026-10-24、25 实测，用的是 1.49 的 `standard` 版改包名。目的是让家里老人按下电源键
就能进直播，所以下面还记了「开机自动打开某个 App」在这台机器上怎么做到。

## 先打开 ADB

1. 设置 → 关于 → 连点版本号，打开开发者选项。
2. 开发者选项里打开网络调试。
3. `adb connect <盒子IP>:5555`（IP 在设置 → 网络 → 已连接的网络里）。

- 创维的 `adb install` 报 `INSTALL_FAILED_USER_RESTRICTED` 时，先运行
  `adb shell settings put global verifier_verify_adb_installs 0` 再装。
- **盒子会休眠**：休眠后 ADB 无响应，要先用遥控器唤醒，不是脚本出错。

## 官方包能装，但当不了 HOME

`adb install` 本身正常，问题是设不成 HOME。固件在 framework 层按包名**前缀**过滤 HOME 候选，
logcat 关键字：

```
PackageManager: block home apk <pkg>
PackageManager: allow home apk <pkg>
```

白名单是前缀匹配，其中一条是：

```
com.skyworthdigital.sky2dlauncher
com.ktcp.launcher            ← 系统里根本没有这个包，但前缀被放行
com.android.provision
com.skyworthdigital.bluetooth.settings
```

**解法**：把 `applicationId` 改成 `com.ktcp.launcher.aerial`（用子前缀就行，不用抢本体）。
Java 命名空间保持原样，代码一行不用改，只是组件全名变成
`com.ktcp.launcher.aerial/app.orchard.tv.MainActivity` 这种混合形式。

```
adb shell cmd package set-home-activity com.ktcp.launcher.aerial/app.orchard.tv.MainActivity
```

改完开机和主页键都直接进 Aerial。以下两条**都无效**，开机时被系统服务重置，别浪费时间：

```
settings put global enable_launcher_components ...
setprop sys.launcherlist.enable 0
```

和 OPPO K9（`docs/devices/oppo-coloros-tv.md`）一样要改包名，但这台机器上没有那两个内部名字
的坑：改完包名后 `WRITE_SECURE_SETTINGS`、通知监听、无障碍、悬浮窗都正常授予，官方包也没有
因为 provider 撞车而装不进来。

## 开机广播全被拦（想开机自动打开别的 App 时注意）

```
E BroadcastQueue: Block auto start app from broadcast: <pkg>
```

BOOT_COMPLETED、加 `enable_running_backgroud_components` 白名单、借白名单里的空包名、
`monkey` 清 stopped 态、前台服务自救——都试过，全被拦（连预装的搜狗输入法都被拦）。

唯一走得通的路：**让桌面在开机首次启动时显式 `startForegroundService` 拉起目标服务**。
桌面本来就一定会跑，而「前台应用显式 startService」是这套固件唯一放行的自启方式。
Aerial 自带 `firstStartSinceBoot()`（用 `Settings.Global.BOOT_COUNT` 去重），挂在它后面即可，
不用自己维护开机标记。

补充：`BOOT_COMPLETED` 并不是永远收不到，包处于非 stopped 态时确实会派发，所以被调用方
对「被 arm 两次」要做幂等（只认第一次），否则第二次会用更晚的计时覆盖第一次。

## 屏保

固件**每次开机**把 `screensaver_components` 写回创维的广告组件：

```
adb shell settings get secure screensaver_components
→ com.skyworthdigital.ott.advert.app/com.skyworthdigital.ott.advert.app.screensaver.ScreenSaverService
```

所以换屏保不是一次性的，开机后要补回来。

## 实测结果（1.49，standard 版改包名）

| 项目 | 结果 |
|---|---|
| 安装 | 成功（改包名后；覆盖安装保留壁纸和排序） |
| 授权 | `WRITE_SECURE_SETTINGS`、通知监听、无障碍、悬浮窗都正常 |
| 开机 | 自动进 Aerial |
| 按主页键 | 回 Aerial |
| 航拍屏保 | 正常 |
| 天气 | 正常，可以固定城市 |
| 32 位用户空间 | `armeabi-v7a`，纯 64 位 so 的应用装不上 |
| 内存 2 GB | 1.48 起自动改用「流畅」效果，也可以在 个性化 → 效果 里手动选 |
| 手机配置页 | 未逐项验证 |
| 信号源 | 不适用（这是盒子，只有输出没有输入） |

## 已知问题

- 屏保每次开机被固件写回广告组件，见上。
- **别用 Aerial 的「检查更新」**：它下载官方 `app.orchard.tv` 包，会装成第二个应用。
  升级要重新改包名编译安装。
- 盒子休眠后 ADB 断，需遥控器唤醒。

## 顺带：这台机器上的瘦身清单

不是装 Aerial 必需的，但 2 GB 内存上差别明显。都是 `pm disable-user --user 0`，可还原：

```
com.tianci.ad                         # 广告服务，屏保也是它
com.skyworthdigital.ott.guide         # 开机引导
com.mipt.store                        # 创维应用商店
com.jd.smartservicetwo                # 京东服务
com.skyworthdigital.softap            # Wi-Fi 热点（这台走有线）
com.skyworthdigital.smarthome
com.skyworthdigital.skywechatclient   # 微信 TV 版
com.skyworthdigital.optimization
com.skyworthdigital.skycleandisk
com.skyworthdigital.remotedebug       # 厂商远程调试
com.nanosic.www.otaservice            # 需要固件更新时再打开
com.skyworthdigital.miracast          # 投屏
com.softwinner.dragonbox              # 全志板级测试应用
com.softwinner.agingdragonbox         # 工厂老化测试
```

官方桌面 `com.skyworthdigital.sky2dlauncherv4` 建议**只在 Aerial 已经接管 HOME 之后**才停用，
还原时要先把它恢复。完整的清单和脚本在报告人的仓库里：
<https://github.com/JohnnyLong2026/skyworthbox-p3-launcher-kit>
