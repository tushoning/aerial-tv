# 腾讯极光盒子

来自用户反馈，维护者手上没有。

| 机型 | 系统 / 平台 | 结果 | 来源 |
|---|---|---|---|
| 极光盒子 6 / Q5043（创维数字代工，cupid-p2，全志） | Android 10 | 开机直达和主页键都正常（守护接管，主页键后约 4 秒回 Aerial）。任何 `settings` 子命令都会让 adbd 卡住 120 秒，`pm`、`appops`、`dumpsys` 正常；Aerial 拿到 `WRITE_SECURE_SETTINGS` 后自己打开了守护，所以不需要手写设置。HOME 候选只认平台签名的桌面（极光、小湃），Aerial 进不了候选，`role add-role-holder` 不报错但无效 | #367 |
| 极光盒子 6SE、7S（BA101，Amlogic s7d） | Android 14 | 原桌面 `com.ktcp.launcher`，真正显示的是 `LauncherHomeSingleTaskActivity`（1.35 起守护认得它）。7S 上 1.36 仍然开机停在原桌面，原因还没查到，需要开机后的 `AerialGuard` 日志 | #269、#270 |
