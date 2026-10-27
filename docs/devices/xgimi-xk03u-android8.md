# 极米 Z6X 第四代（XK03U / ironman / MStar m7221）信号源

- Android 8.0 / SDK 26，固件 `v1.1.21`；原生播放器 `com.xgimi.xhplayer` 1.4.2。
- 实机验证日期：2026-10-26。

## 实现

此固件没有注册 `TvInputService`，`TvInputManager` 因而列不出输入；MStar 通用输入列表又包含本机不存在的 HDMI 口和调谐器。Aerial 改为读取 `com.xgimi.clients.GimiConfigManager` 的 `global` 产品配置，只从真实配置中生成 HDMI 磁贴。

连接状态由 `TvCommonManager.GetInputSourceStatus()` 读取，并按运行时 `TvOsType.EnumInputSource` 的枚举序号对应状态数组。点击 HDMI 磁贴时，直接启动已导出的 `com.xgimi.xhplayer/.InputActivity`，action 为 `com.xgimi.action.hdmiPlayer`，extra `launcher_start_source` 传入 `HDMI1`。

适配仅在 Android 9 之前、播放器 Activity 可用且产品配置可读取时生效；异常或接口不可用时返回空列表，由 Aerial 原有的其他输入源探测路径继续处理。没有打包厂商 JAR 或新增权限。

## 验证范围

- 普通应用 UID，无额外权限：读取产品配置和输入连接状态成功。
- Aerial 首页及控制中心显示 HDMI1；点击后进入极米原生播放器。
- 测试时没有有效 HDMI 输入信号；未验证视频、声音、HDCP 或 CEC。
- 本适配只覆盖 Android 8 上实测的播放器协议；Android 9 及以上机型不在验证范围内。

## 与另一条极米适配路径的区别

[PR #437](https://github.com/pengchujin/aerial-tv/pull/437) 面向 Z7X / GMUI Android 9：系统能列出输入，但标准 passthrough Intent 无处理器，因此改为通过 `com.xgimi.newsettings.SettingService` 打开极米信号源页。本机 XK03U / Android 8 没有 TV 输入服务，使用产品配置生成端口并直接启动原生播放器；两条路径针对不同固件，不能互相替代。
