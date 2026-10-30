# Aerial OPPO 定制版：自动同步上游 + 自动发版

配一次，之后上游发新 release，本仓库会在 1 小时内自动出一个同版本号的定制包。
只需要装一次，以后电视上「检查更新」就能装到新版。

---

## 1. 仓库里放什么

```
custom/apply.py                              # 全部定制逻辑（唯一真相）
custom/files/TvApi.kt                        # 固件 tvapisvc 的反射封装（背光/待机共用）
custom/files/OppoBacklight.kt                # 亮度走固件服务（整份新增）
custom/files/OppoPower.kt                    # 待机 / 关机走固件服务（整份新增）
custom/SETUP-自动发版.md                      # 就是本文
.github/workflows/sync-upstream-release.yml  # CI：拉上游 → 打补丁 → 签名 → 发 release
```

- `custom/files/` 放「整份新增、无法用锚点插进上游文件」的源码。第 2 阶段放进 `system/`，
  第 3 阶段的包名改名会一起搬到 `com/oneplus/tv/launcher2/system/`，**所以文件里包名要写
  `com.oneplus.tv.launcher2.system`**。以后再加整份文件丢进去即可，不用改脚本。
- 除此之外**不要提交任何上游源码**，CI 每次都从上游 tag 解压一份干净的。

---

## 2. 这版定制了什么

| 模块 | 做法 |
|---|---|
| 包名 / 进程 | `app.orchard.tv` → `com.oneplus.tv.launcher2`；主进程挂 `:com.heytap.tv.remote`（固件 `tvapisvc` 按**进程名**白名单放行，不挂就调不动背光和关机）。HOME 加 `MODERN_HOME`；release 强制 v1+v2 签名 |
| 亮度 | 读写走固件 `disPatchCommand("ModulePicture", 1, [亮度])`，不走 Settings（这台机器 Settings 是影子数据）。5% 一档，范围 **0..100**。确认键进「图像与亮度」 |
| 亮度同步 | 打开控制中心时**先同步查一次固件**（`OppoBacklight.levelNow()`，阻塞几毫秒，本来就在 `Dispatchers.Default` 上），所以面板画出来就是真值；面板开着时每 3 秒随 `refreshPanel()` 再查一次。另外常驻 `ContentObserver` 盯 `Settings.Global.tv_picture_backlight`——但只有「设置 → 图像与亮度」那条会写这个键，快捷亮度条既不写 Settings 也不发广播，只能靠上面那次查询 |
| 屏保时间 | 读写系统 `SCREEN_OFF_TIMEOUT` 双向同步（也盯小米的 `screen_saver_timeout`）；档位加 1 分钟：`1/2/5/10/20分 + 从不`，文案「N分」；打开个性化时补刷一次，读数在 `Dispatchers.IO` |
| 屏保压暗 | 1.68 起上游不再给影片压暗（1.69 依旧）。只把**屏保**那层补回来：影片之上、黑幕之下画一层 `Images.homeShade`，参数与 1.67 完全相同。Home 壁纸保持上游新的亮法 |
| 应用名称 | 第一排名称画进图标内部（三态：仅焦点 / 全部 / 关闭）；进入抽屉后第一排回归网格规则，名称回到图标下方。判据是 `onPlateRow = !inGrid && ` + 上游 `nameOnPlate` 自己那句表达式（跟着上游走） |
| 电源菜单 | 菜单里**再按电源键 → 真关机**；**按 OK 选「息屏」→ 仍是息屏**。不动 SystemUI，改为接管：菜单窗口一出现就上膛，随后到来的 `QUICK` 改写成 `DEPTH` |
| 应用内更新 | 版本检查与 APK 都指回本仓库 release，三级国内加速（ghfast / gh-proxy / 直连）。安装方式保持上游原样：**Android 9（API 28）及以上走安装会话**（1.72 把门槛从 33 降到 28），包里带 `assets/aerial-dm/28.dm` / `31.dm`，系统**装的时候就编译好** Aerial，装完不用再等后台编译；8.1 及以下交给系统安装器 |

### 电源菜单的边界（改代码前先看这段）

- 命令映射是实测的：`disPatchCommand("ModulePower", 4, ["QUICK"])` = 息屏（黑屏不睡），
  `["DEPTH"]` = 真关机。
- 区分「按 OK」和「按电源」靠**按键过滤**：菜单弹出后 **12 秒**内临时打开
  `FLAG_REQUEST_FILTER_KEY_EVENTS`。菜单里按方向键 / OK 会进 `onKeyEvent` → 撤销改写；
  电源键在 `PhoneWindowManager` 就被吃掉、不产生事件 → 改写生效。
- **12 秒是上界，不能改大**：菜单自带 **15 秒倒计时**，到点会自己执行「息屏」
  （实测 15:51:27 弹出 → 15:51:42 自动 QUICK），那一条必须保持息屏。
- 过滤只在「屏幕亮 + 菜单 12 秒内」开，三个出口：按过键 300ms、屏幕熄灭、12 秒超时。
  平时一次都不开（Aerial 是默认桌面，上游规则 `homeWanted = wanted && !isDefaultHome()` 恒 false）。
  这 12 秒里音量键和 HOME 都**原样放行**；音量键不取消改写（它既不合上菜单也不选行），HOME 会取消。
- 判据够窄：菜单里选「重启」不进 QUICK，自动待机超时也没有菜单，两者都不受影响。

---

## 3. 一次性配置

### 3.1 签名（4 个 Secret）

电视上已装的包是某把密钥签的，**换密钥只能卸载重装**，桌面设置全丢。所以沿用本机
`aerial.keystore`（证书 SHA-256 `4ca2921…581b5`）。

```bash
# keystore 转单行 base64（Git Bash）
base64 -w0 aerial.keystore > aerial-keystore.b64

R=tushoning/aerial-tv
gh secret set AERIAL_KEYSTORE_B64    --repo $R < aerial-keystore.b64
gh secret set AERIAL_STORE_PASSWORD  --repo $R -b "aerial123"
gh secret set AERIAL_KEY_ALIAS       --repo $R -b "aerial"
gh secret set AERIAL_KEY_PASSWORD    --repo $R -b "aerial123"
```

网页等价路径：Settings → Secrets and variables → Actions → New repository secret。

可选但建议：Variables 里加 `AERIAL_CERT_SHA256` =
`4ca292129ad97359a262d5f5269e7fd7ef0287b33cd19f6e761dd671946581b5`。
配上之后 keystore 传错会在**发 release 之前**失败，而不是「版本发出去了、电视装不上」。

### 3.2 Actions 写权限

Settings → Actions → General → Workflow permissions → **Read and write permissions**。
不开，最后创建 release 会 403。

### 3.3 先手动跑一次

Actions → 「同步上游并发布 OPPO 定制包」→ Run workflow（tag 留空 = 取上游最新正式版）。
跑完检查：Release 有 `aerial-standard.apk` + `version.txt`；分支多出 `src/<tag>`（打完补丁的源码）；
日志最后一行有「已发布」。

之后不用管：定时任务每小时看一次上游，有新 tag 就出包。

**自定义版本描述**（手动跑时的两个输入）：`notes` 写正文（单行输入框，换行写 `\n`，支持 Markdown；
正文里的反斜杠要写成 `\\`），`keep_upstream` 勾着就在末尾附「以下是上游原文」。
只想改描述、不重编包：`tag` 填已发布过的版本，CI 检测到包已完整，只 `gh release edit` 就退出。

---

## 4. CI 怎么跑

| 环节 | 做法 |
|---|---|
| 发现新版 | `schedule` 每小时查 `pengchujin/aerial-tv` 最新 release（fork 收不到上游 release 事件，只能轮询）。**不要加 `push` 触发**：文件是逐个上传的，每传一个都触发，等于拿半套补丁编译 |
| 拉源码 | `codeload.github.com` 下 tar.gz，`--strip-components=1` 解到临时目录 |
| 打补丁 | `python3 custom/apply.py <目录> --version <tag> --repo <本仓库>` |
| 编译 | 只 `:app:assembleStandardRelease` |
| 签名 | `local/signing.properties` 从 4 个 Secret 现写，v1 + v2 都开 |
| 校验 | 包名 / versionName / v1+v2 / 证书指纹，任一不符直接失败 |
| 发布 | 同 tag 同标题，正文前面加一段定制说明，附件 `aerial-standard.apk` + `version.txt` |

`version.txt` 随 release 一起上传，内容就是版本号，所以「提示有新版」和「能下到」永远同源——
之前「提示了却 404」是因为版本检查还指着上游官网，而本仓库还没编完。

---

## 5. 上游改代码、CI 变红怎么办

每条补丁都要求「锚点恰好命中一次」，命不中就失败，**不会产出半成品包**。报错长这样：

```
[apply.py 失败] 2 处没改成：

1) .../ui/home/AppTile.kt
    要改的地方：图标内画名称
    问题：锚点命中 0 次（应当 1 次），上游可能删了或改了这一段
```

本地复现（所有失败会一次列全，不用改一条跑一次）：

```bash
curl -fsSL https://ghfast.top/https://github.com/pengchujin/aerial-tv/archive/refs/tags/v1.69.tar.gz -o v.tgz
mkdir -p tmp && tar xzf v.tgz -C tmp --strip-components=1
python3 custom/apply.py tmp --version 1.69 --repo tushoning/aerial-tv
```

### 已经做过的防抖

| 机制 | 挡住什么 |
|---|---|
| **结构化锚点**：`insert_in_body`（认函数签名，插到块首/块尾）、`insert_after_block`（认签名，插在块尾之后）、`set_call_arg`（认参数名）、`ensure_import`（按行补 import） | 上游在函数体里加分支、换参数顺序、改导入顺序 |
| **按名字改资源**：`set_string` / `insert_strings` 只认 `<string name="…">` | 上游重写文案（1.68 一口气改了好几个字符串） |
| **缩进自适应**：`patch()` 逐字找不到时，允许首行缩进浮动（块内相对缩进仍钉死），命中后写入的代码按同一位移缩进 | 整段被挪进/挪出一个 `if`、多包一层 `Box`（1.69 的 `WallpaperSheet` 就是每行少 4 个空格） |
| **注释当通配**（`_tolerant_match`）：锚点里的纯注释行可缺可改，代码行仍须逐字对上 | 上游只删/改注释、代码一字不动（1.69 的 `ss_delay` 就是这么红的） |
| **单行锚点**：`insert_after_line` / `insert_before_line` 只认「某一整行」，缩进跟着它走；可以给一组候选 | 上游在同一个列表里插自己的一行 —— 1.70 往 HOME 的 intent-filter 里加了 `COMMON_HOME`；1.69 起名字那行多了 `DiagLayers` 判据 |
| **跨段替换**（`patch_span`）：只认「起始行 → 结束行」，中间内容一概不看（`absorb` 还能连带吃掉收尾的 `}`） | 要整段换掉的地方，上游在里面加行、改缩进、改措辞都不影响（屏保那一档、MenuRow 取色、更新行） |
| **语句换位**（`move_line_before`）：只认两行，把它们对调 | 上游在同一个块里多写一句（控制中心「先刷再等」） |
| **多组候选**（`patch_any`）：一条补丁备「新版写法 + 旧版写法」几组锚点，命中任一即过 | 上游局部重构 —— 1.72 把 `refreshPanel` 拆成两句、把版本检查收进新的 `UpdateCheck` 类 |
| **上游做了就跳过**：屏保压暗、控制中心开面板刷新这两处，先看上游是不是自己做了 | 上游把我们要的功能做进去之后，不会画两遍/刷两遍（1.70 就自己加了 `LaunchedEffect(visible)` 刷新） |
| **幂等 marker + 终检** | 重复插入；「脚本说 ok 包里其实没有」 |

现在整块原文（≥3 行）的锚点**一处不剩**，`python3 custom/apply.py --lint` 会报这个数字，涨了就是又有人往 `patch()` 里抄了一整段。

剩下会红的只有真改动：函数被删/改名、参数名变化、整段逻辑搬去别处且没有任何一行留下 —— 那种本来也该看一眼。

---

## 6. 本地出一版（不依赖 CI）

```bash
tar xzf v169.tar.gz -C work/src --strip-components=1
python3 custom/apply.py work/src --version 1.69 --repo tushoning/aerial-tv

mkdir -p work/src/local && cp aerial.keystore work/src/local/
cat > work/src/local/signing.properties <<'EOF'
storeFile=aerial.keystore
storePassword=aerial123
keyAlias=aerial
keyPassword=aerial123
EOF
printf 'sdk.dir=C:/path/to/android-sdk\n' > work/src/local.properties   # 必须正斜杠

export JAVA_HOME="C:/path/to/jdk-17"
work/src/gradlew :app:assembleStandardRelease            # 或本地 gradle-8.14.3/bin/gradle
adb install -r work/src/app/build/outputs/apk/standard/release/app-standard-release.apk
```

Gradle 发行包下载不动时（`services.gradle.org` 卡在 `Downloading`）：从
`https://mirrors.huaweicloud.com/gradle/gradle-8.14.3-bin.zip` 下，解压后直接用 `bin/gradle`，
或塞进 `~/.gradle/wrapper/dists/gradle-8.14.3-bin/<hash>/` 并 `touch gradle-8.14.3-bin.zip.ok`。
依赖已有缓存时可加 `--offline`。

---

## 7. 坑

- **别提交 `local/`**：CI 推 `src/<tag>` 分支的步骤排在写密钥之前，并会断言 `local/` 不存在。
- **fork 的默认分支**必须是放着 `custom/` 与 `.github/` 的那个，否则 `schedule` 不触发。
- **定时任务不保证准点**：`schedule` 是 best effort，高峰期会延后、严重时整班不出现
  （2026-10-28 实测 08:23 那班没有 run 记录）。所以设成每小时（`23 * * * *`，UTC → 北京 0X:23）：
  做过的版本走到「取上游最新 release」就 skip，**根本不编译**，跑勤几乎免费；漏一班最多晚 1 小时。
  60 天无提交的仓库会被暂停定时任务，点一下 **Enable workflow** 即可。
- **不要改 release 里 `aerial-standard.apk` 的文件名**：应用内更新按 `aerial-<flavor>.apk` 精确取。
- **主进程名不是包名**：`pidof com.oneplus.tv.launcher2` 查不到，要用
  `com.oneplus.tv.launcher2:com.heytap.tv.remote`。
- **adb 不能 root**：`/data/data` 读不到，别想看应用记住的亮度。
- **降级安装被拒**（`INSTALL_FAILED_VERSION_DOWNGRADE`）：`adb -d` / `pm install -r -d` 只对可调试包有效；
  装旧版只能卸载（数据丢）或抬 versionCode 重编。
- **默认桌面没固定时禁用 Aerial 会让遥控全哑**：没有桌面在跑，方向键没人接。先按 HOME 在「选择应用」里勾「始终」。
- **Gradle 偶发写 APK 时 `IOException`**：不是代码问题，删 `app/build/outputs/apk/standard` 重跑，前面全 up-to-date。
- **覆盖安装前确认签名一致**：`apksigner verify --print-certs` 看 SHA-256。
