# 创维 / 酷开（Skyworth / Coocaa）

来自用户反馈，维护者手上没有创维。以下都是报告人的实测。

| 机型 | 系统 / 平台 | 结果 | 来源 |
|---|---|---|---|
| 65G60 / 酷开 9S60 | Android 8.0，MStar MSD6A848 | 主页键能回 Aerial；开机一定进酷开桌面：`sky_launcher`（在 system_server 里）读 `/system/pcfg/9S60_G60/config/general_config.xml` 的 `DEFAULT_HOMEPAGE` 按组件启动，不走 HOME 解析；守护在开机时的 `startActivity` 被 `CooCaaCheckDialogPermission` 丢掉（调用方没有可见 Activity）；`skyRMService` 切前台时会 force-stop 上一个第三方 App；开机把第三方 App 置成 stopped。报告人用 root 改 `DEFAULT_HOMEPAGE` 为 `app.orchard.tv/app.orchard.tv.MainActivity` 才开机直达。开机广告是 `com.tianci.ad`，`pm uninstall --user 0` 就没了 | #316 |
| 7T641（mt9633） | Android 13 | 冷启动约 60 秒黑屏：Aerial 早已画完，但 SurfaceFlinger 里它的窗口 `crop=[0,0,0,0]`；遥控器「播放」键在这台上发的是 Home，看视频时按到会被守护拉回 Aerial，然后又是 60 秒黑屏 | #321、#326 |
| 75A6E（8R710_A6E，Realtek RTD2885P） | Android 13 | standard 版是唯一的 HOME 候选，按主页键整机卡死；创维电视应用 `com.skyworth.tv` 一直占着输入焦点，Aerial 的窗口 `NOT_VISIBLE`、`focus false`，guard 版也一样。目前没有办法 | #365 |
| 酷开 8R710_P8F（同一机芯 titanite） | Android 13 | 只有诊断报告，现象待补，估计同 #365 | #358 |
| 松下 TH-55HX560C（创维代工，天赐系统，GX580C，m7221） | Android 8.0 | 主页键、返回都能回 Aerial；`TvInputManager` 一个信号源都没有，控制中心显示「没有可用的信号源」。还不知道天赐切信号源的接口 | #370 |
| 8R134 / 8R145、K3 Pro 等 | Android 11 | 见 #43、#155：开机先进自家桌面，切信号源时经过 `com.tianci.tv/.SkyLauncher` | #43、#155 |

## 共同点

- 开机自启基本做不到：桌面是固件按组件或配置文件拉起的，不看 HOME 解析。
- logcat 常常看不到第三方 App 的日志，看 `dumpsys activity app.orchard.tv/.MainActivity` 末尾的 DiagLog。
- `adb install -r` 之后系统会删掉本包的无障碍登记，重装后要先打开一次 Aerial（#316）。
