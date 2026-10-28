# 雷鸟 75S78A Plus（FFALCON T966D5，Android 14）

来自 #817 报告人的实测（2026-10-28），我们手上没有这台。

- `ro.product.model` = `Smart TV Pro`，`ro.product.device` = `T966D5`，`ro.product.name` = `T966D5_4K_CN`；品牌 TCL、厂商 FFALCON。
- Amlogic `t6x`，Android 14（SDK 34），固件 `AU01`；界面 1920×1080 / 320dpi，面板 4K（`ro.feature.graphics_4kui = false`）。
- 原桌面 `com.tcl.cyberui/.MainActivity`；`com.tcl.settings` 没有无障碍入口（`ACCESSIBILITY_SETTINGS` 只解析到 framework stub）。
- 安装：`adb install` 被拒（`INSTALL_FAILED_VERIFICATION_FAILURE`），只能多屏互动推 guard 版。ADB 明文 5555，重启后能自动重连。

## 守护开不了

- 固件大约每 52 秒把 `enabled_accessibility_services` 清成 `null`、`accessibility_enabled` 改回 0，Aerial 写回去（日志 `Home Guard had been switched off; turned it back on`）马上又被清。shell 写也一样。所以主页键回 TCL 桌面，没有放行办法。
- TCL 自启动管理（`TclAppBoot`）拒绝 `BootReceiver`、`RevivalSyncService`、`RevivalListener`，理由 `callee_does't_have_OP_AUTO_START_permission`。这台的 AppOps 叫 **`AUTO_START` / `ASSOC_START`**（MT5879 上叫 `APP_AUTO_START` / `APP_ASSOC_START`）。电视「权限管理 → 应用自动启动」或 `appops set` 能打开，但**每次开机前被固件改回 `ignore`**。`install.sh` 1.67 起两套名字都设，只管到下次重启。

## 开机进 Aerial：`boot_app_componentname`

TCL 自己的「开机进入」设置在这台上有效（MT5879 上试过无效，见 [tcl-mt5879-cn.md](tcl-mt5879-cn.md)）：

```bash
adb shell settings get system boot_app_componentname          # 原值 com.tcl.cyberui/.MainActivity，先记下
adb shell settings put system boot_app_componentname app.orchard.tv/.MainActivity
# 还原
adb shell settings put system boot_app_componentname com.tcl.cyberui/.MainActivity
```

报告人连续重启 3 次，开机 26 秒内前台都是 Aerial，之后没被打回。主页键仍然回 TCL 桌面（守护被清）。
