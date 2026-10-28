# Aerial OPPO 定制版：自动同步上游 + 自动发版

配置一次，之后**上游发新 release，1 小时内本仓库自动出一个同版本号的定制包**（签名、包名、全部定制都在）。
你只需要装一次，以后电视上点「检查更新」就能装到新版本。

---

## 一、需要放进仓库的文件

```
custom/apply.py                              # 全部定制逻辑（唯一真相）
.github/workflows/sync-upstream-release.yml  # CI：拉上游 → 打补丁 → 签名 → 发 release
```

另外两个**可选的附属文件**，CI 不读，只是给人看/给人用，都放在 `custom/` 下跟 `apply.py` 挨着：

```
custom/files/OppoBacklight.kt   # 整份新增的源文件（走 apply.py 的第 2 阶段复制进源码并跟着改名）
custom/SETUP-自动发版.md        # 就是本文：配一次之后怎么跑、出问题怎么改
```

`custom/files/` 是给「需要整份新增、无法用锚点插进上游文件」的源文件用的。当前里面是
`OppoBacklight.kt`（亮度走固件服务那一层，整份新增），`apply.py` 第 2 阶段会把它放进**老包名**
目录，再跟着第 3 阶段的改名一起搬到 `com/oneplus/tv/launcher2/system/`。以后再加整份文件，
把 `.kt` 丢进这个目录即可，不需要改脚本。

除此之外**不要提交**任何上游源码。CI 每次都从上游 tag 重新解压一份干净的，从不复用旧目录。

```bash
cd <你 clone 的 tushoning/xxx 目录>
mkdir -p custom .github/workflows
cp -r <这份 fork 目录>/custom/. custom/                 # apply.py + files/ + 本文档
cp -r <这份 fork 目录>/.github/. .github/               # 工作流
git add -A
git commit -m "OPPO 定制：补丁脚本 + 自动发版流程"
git push
```

> 只要 `custom/` 和 `.github/` 两处，**不要**把上游源码一起提交。

> 仓库名不是 `aerial-tv` 也没关系：CI 里用 `${{ github.repository }}` 自动取，
> 应用内更新地址、release 上传全跟着实际仓库走，**代码里没有写死仓库名**。

### 这版定制了什么（功能清单）

跟上游 v1.62 的差异全在下面，都由 `apply.py` 一次生成：

**安装与进程**

- 包名 `app.orchard.tv` → `com.oneplus.tv.launcher2`；主进程挂在 `:com.heytap.tv.remote`
  （`tvapisvc` 按**进程名**白名单放行，不挂就调不动背光），背光服务也留在这个进程里。
- HOME 加 `MODERN_HOME` 类别；release 强制 v1 + v2 签名。
- `custom/files/OppoBacklight.kt` 整份新增（进老包名目录，跟着第 3 阶段一起改名搬走）。

**亮度**

- 读写走固件服务 `tvapisvc`（`disPatchCommand("ModulePicture", 1, [亮度])`），不是 Settings ——
  这台电视 Settings 是影子数据，写它面板不一定动。
- 步进 5% 一档，范围 **0..100**（跟系统那条一致，0 是最低）。
- 控制中心的亮度条：确认键进电视自己的「图像与亮度」。
- 同步（外界改 → Aerial 跟着变）：常驻 `ContentObserver` 盯
  `Settings.Global.tv_picture_backlight`。**注意覆盖面**：只有「设置 → 图像与亮度」那条会写这个键；
  快捷亮度条（`ScreenLightAdjustDialog`）实测既不写任何 Settings 键、也不发广播，所以那条**跟不动**——
  想跟只能轮询固件（一次查询约 2~10ms），当前选择不做。
- `set()` 会把这个键镜像写一份（给电视设置页冷启动留快照）；`onRead()` **不回写**——
  这个键在设置应用活着时能推动面板，而校准读到的可能是渐变中间值，回写会把面板拽歪。

**屏保时间**

- 个性化里的「进入时间」直接读写系统 `Settings.System.SCREEN_OFF_TIMEOUT`，**双向同步**
  （Aerial 改 → 系统跟着；系统改 → Aerial 跟着，常驻 `ContentObserver` 盯 `SCREEN_OFF_TIMEOUT`
  和小米那条 `screen_saver_timeout`）。
- 档位加 **1 分钟**：`1分 / 2分 / 5分 / 10分 / 20分 / 从不`；文案压成「N分」。
- 打开个性化时再刷一次（兜底，防某些固件改设置不发通知）；同步读数走 `Dispatchers.IO`，
  不挡抽屉滑入动画。

**应用名称**

- 第一排（玻璃板那排）名称画进**图标内部**，三态：仅焦点 / 全部 / 关闭（`plateNames`）。
- 第二排及以下保持上游原样：名称画在图标下方，是否全部显示由 `showAllNames` 决定。
- **进入抽屉（焦点下到应用网格）后，第一排也回归网格规则**：名称回到图标下方、跟第二排同一个开关。
  判定用上游已有的 `inGrid`，一个 `onPlateRow = !inGrid && index < COLUMNS` 同时驱动
  `showName` / `plateName`（`nameOnPlate` 走默认值 false，那是个上游参数、在本定制里恒不生效）。

---

## 二、签名密钥（4 个 Secret）

这一步不能随便：电视上已经装的包是某一把密钥签的，**换密钥就只能卸载重装**，桌面设置全丢。
所以必须沿用你本机那把 `aerial.keystore`（证书 SHA-256 `4ca2921…581b5`）。

### 1. 把 keystore 转成单行 base64

Git Bash：

```bash
base64 -w0 aerial.keystore > aerial-keystore.b64
wc -c aerial-keystore.b64      # 应该是一行、约 3638 个字符
```

PowerShell：

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("aerial.keystore")) |
  Set-Content -NoNewline -Encoding ascii aerial-keystore.b64
```

### 2. 填进仓库

有 `gh` 命令（最省事，`gh auth login` 之后）：

```bash
R=tushoning/aerial-tv
gh secret set AERIAL_KEYSTORE_B64    --repo $R < aerial-keystore.b64
gh secret set AERIAL_STORE_PASSWORD  --repo $R -b "aerial123"
gh secret set AERIAL_KEY_ALIAS       --repo $R -b "aerial"
gh secret set AERIAL_KEY_PASSWORD    --repo $R -b "aerial123"
```

或者网页：仓库 → **Settings → Secrets and variables → Actions → New repository secret**，逐个建：

| Secret 名 | 值 |
|---|---|
| `AERIAL_KEYSTORE_B64` | `aerial-keystore.b64` 的**全文**（一行，别带换行/空格） |
| `AERIAL_STORE_PASSWORD` | `aerial123` |
| `AERIAL_KEY_ALIAS` | `aerial` |
| `AERIAL_KEY_PASSWORD` | `aerial123` |

> 口令如果不是这三个值，用你当初 `keytool` 生成时写的；不确定就本地跑一次
> `keytool -list -v -keystore aerial.keystore` 验证。

### 3.（建议）加一个指纹强校验

**Settings → Secrets and variables → Actions → 顶部切到 Variables 标签页 → New repository variable**

| Name | Value |
|---|---|
| `AERIAL_CERT_SHA256` | `4ca292129ad97359a262d5f5269e7fd7ef0287b33cd19f6e761dd671946581b5` |

配上之后，CI 每次都会核对签名证书；万一 Secret 里的 keystore 传错，**在发 release 前就失败**，
不会出现"版本发出来了，但电视装不上"。没配也能跑，只是这层校验不生效，日志里仍会打印实际指纹。

---

## 三、打开 Actions 的写权限

**Settings → Actions → General → Workflow permissions** → 选 **Read and write permissions** → Save。

不开这一步，最后 `创建 release` 会 403。

---

## 四、第一次手动跑一次

**Actions → 左侧「同步上游并发布 OPPO 定制包」→ Run workflow**
（tag 留空 = 自动取上游最新正式版）

跑完检查三件事：

1. Release 页出现 `v1.46`，附件有 `aerial-standard.apk` 和 `version.txt`
2. 分支列表多出 `src/v1.46`（打完补丁的源码，可以直接对照看改了什么）
3. 日志最后一行有 `已发布：https://github.com/...`

之后**不用再管**：定时任务每小时看一次上游，有新 tag 就自动出包。

### 自己写版本描述（可选）

手动跑时有两个额外的输入框：

| 输入 | 说明 |
|---|---|
| `notes` | 版本描述正文。留空＝沿用默认那套（定制说明 + 上游原文）。输入框只有一行，**换行写 `\n`**，支持 Markdown |
| `keep_upstream` | 勾着（默认）＝你写的描述末尾仍附上「以下是上游原文」。不想看就取消勾选 |

例子：

```
notes = 1.61 定制版：\n\n- 亮度条确认键进图像设置\n- 遥控器菜单键不再丢焦点
```

会得到：

```markdown
1.61 定制版：

- 亮度条确认键进图像设置
- 遥控器菜单键不再丢焦点

---

以下是上游原文：
…
```

两个细节：

- **只想改描述、不重新编译**：把 `tag` 填成已经发布过的那个（比如 `v1.61`），`notes` 填上，直接跑。
  CI 会看到这个 tag 的包已经完整，只 `gh release edit` 换掉描述就走，不编包（几十秒）。
- 文字里有反斜杠要写两条（`\\`），因为 `\n` 是靠 `printf %b` 还原的。

---

## 五、这些是怎么自动接上的

| 环节 | 做法 |
|---|---|
| 发现新版 | `schedule` 每小时查 `pengchujin/aerial-tv` 的最新 release（fork 收不到上游的 release 事件，只能轮询） |
| 拉源码 | `codeload.github.com` 下 `tar.gz`，解到临时目录，`--strip-components=1` |
| 打补丁 | `python3 custom/apply.py <目录> --version <tag> --repo <本仓库>` |
| 编译 | 只 `:app:assembleStandardRelease`，guard flavor 不参与 |
| 签名 | `local/signing.properties` 从 4 个 Secret 现写，v1 + v2 都开 |
| 校验 | 包名 / versionName / v1+v2 / 证书指纹，任一不符直接失败 |
| 发布 | 同 tag、同标题、同正文（前面加一段定制说明），附件 `aerial-standard.apk` + `version.txt` |

### 应用内更新为什么能对上

原来更新是「上游官网 `version.txt` 说最新几版 + 上游 Release 下 APK」。搬到 fork 后有个空窗：
官网先更新，本仓库的包要等 CI 编完才有，于是 Control Center 提示有新版本、点下载却 404 —— 就是你遇到的「下载失败」。

所以定制版把版本检查也收回自己仓库：

```
版本检查：.../releases/latest/download/version.txt
下载 APK：.../releases/download/v<版本>/aerial-standard.apk
```

两条链路都是**三级国内加速**，前一个源失败就换下一个：

| 顺序 | 源 |
|---|---|
| 1 | `https://ghfast.top/https://github.com/<本仓库>/…` |
| 2 | `https://gh-proxy.com/https://github.com/<本仓库>/…` |
| 3 | `https://github.com/<本仓库>/…`（直连） |

版本检查还会校验响应内容是不是版本号——镜像返回错误页面时，不会被当成「已是最新」，而是继续试下一个源。

`version.txt` 由 CI 每次随 release 一起上传，内容就是版本号。**提示和可下载永远同源，不会再出现提示了却下不到。**

---

## 六、上游改代码导致 CI 变红怎么办

`apply.py` 每条补丁都要求「锚点恰好命中一次」。上游动了这些地方，CI 会在 `打上 OPPO 定制` 这步失败，
报错长这样：

```
[apply.py 失败] 2 处没改成：

1) app/src/main/java/com/oneplus/tv/launcher2/ui/home/AppTile.kt
    要改的地方：图标内画名称
    问题：锚点命中 0 次（应当 1 次），上游可能删了或改了这一段
    期望原文：
      |                     translationY = height.toPx() * (scale - 1f) / 2f - ...
```

它**不会**默默产出一个半成品包。处理方式：

```bash
# 本地拿同样的源码复现
curl -fsSL https://codeload.github.com/pengchujin/aerial-tv/tar.gz/refs/tags/v1.47 -o src.tgz
mkdir -p tmp && tar xzf src.tgz -C tmp --strip-components=1
python3 custom/apply.py tmp --version 1.47 --repo tushoning/aerial-tv
```

照着报错里的「期望原文」，去上游源码里找对应的新写法，把 `apply.py` 里那条补丁的 `old` 改成新文本，
再跑一次，全绿了提交。**所有失败会一次列全**，不用改一条跑一次。

### 亮度那几处已经不用手改了

`patch_brightness()` 用的是「结构化锚点」，不再照抄上游原文（`apply.py` 顶部有 `insert_in_body` /
`set_call_arg` / `ensure_import` 三个函数，注释写明了原理）：

| 补丁 | 定位方式 | 什么情况下才会红 |
|---|---|---|
| 亮度读取 / 步进 | 认 `fun level(...)`、`fun adjust(...)` 的函数签名，代码插到函数体**第一行** | 上游改签名（参数、返回类型、改成扩展函数） |
| 亮度条确认键 | 认 `onStep = vm::adjustBrightness,` 这一行，改**它所属调用**的 `onClick` | 这个参数改名，或整行被删 |
| HomeViewModel 的钩子 | `import` 按字母序补；`init {}` / `onCleared()` 的代码插在**块尾** | 这两个块改名 |
| 屏保那组（读写 / 档位 / 观察者） | 多数是结构化锚点：`insert_in_body` 认函数签名、`ensure_import` 补 import；只有 `WallpaperSheet` 里那两处是原文锚点 | 改了 `AerialSettings` / `SystemScreensaver` 的函数签名，或改了 `WallpaperSheet` 里那两行 |
| 名称那组（第一排 / 抽屉） | **仍是原文锚点**（`patch()`），但只认两小段：`animateIntOffsetAsState` 那一段、以及 `showName`/`nameOnPlate` 那两行；「每行有几个」的标识符（1.65 前是常量 `COLUMNS`，1.65 起是变量 `columns`）由脚本**自动识别**，不写死 | 上游改了这两段里的**措辞**（1.65 把 `COLUMNS` 换成 `columns` 踩过一次，已做成自动识别） |

所以上游在函数体里加分支、加注释、调换参数顺序（1.57 的 AppTile 导入、1.61 的极米光机分支就是这种），
都不会再把 CI 顶红。只有真的动了签名/参数名才会报错——那种情况本来也该人工看一眼。

这三处都有 marker：内容已经在文件里就跳过，不会插两遍；插不进去时终检也会报「这一步没落地」，
所以不会出现「脚本说 ok、包里其实没有」。

---

## 七、本地手动出一版（不依赖 CI）

```bash
# 1. 干净的上游源码
tar xzf v146.tar.gz -C work/src146 --strip-components=1

# 2. 打补丁
python3 custom/apply.py work/src146 --version 1.46 --repo tushoning/aerial-tv

# 3. 放签名
mkdir -p work/src146/local
cp aerial.keystore work/src146/local/
cat > work/src146/local/signing.properties <<'EOF'
storeFile=aerial.keystore
storePassword=aerial123
keyAlias=aerial
keyPassword=aerial123
EOF
printf 'sdk.dir=C:/path/to/android-sdk\n' > work/src146/local.properties

# 4. 编译
cd work/src146 && ./gradlew :app:assembleStandardRelease
# 产物：app/build/outputs/apk/standard/release/app-standard-release.apk
```

`local.properties` 里 SDK 路径**必须用正斜杠**，反斜杠会被 Gradle 当转义符，报 dex 合并失败。

### 断网时（上游能下、Gradle 不能下）

`./gradlew` 会去 `services.gradle.org` 下发行包，没有外网就卡在 `Downloading`。这时用本地已装好的
Gradle 二进制直接跑，并且加 `--offline` 让依赖只走本地缓存：

```bash
# 1. 源码（走镜像，直连 github 会被代理挡成 502）
curl -fsSL --noproxy '*' \
  https://ghfast.top/https://github.com/pengchujin/aerial-tv/archive/refs/tags/v1.49.tar.gz \
  -o v149.tar.gz
mkdir -p src149 && tar xzf v149.tar.gz -C src149 --strip-components=1

# 2. 打补丁
python custom/apply.py src149 --version 1.49 --repo tushoning/aerial-tv

# 3. 签名 + SDK
mkdir -p src149/local && cp aerial.keystore src149/local/
cat > src149/local/signing.properties <<'EOF'
storeFile=aerial.keystore
storePassword=aerial123
keyAlias=aerial
keyPassword=aerial123
EOF
printf 'sdk.dir=C:/path/to/android-sdk\n' > src149/local.properties

# 4. 编译（JDK 17，本地 gradle 二进制，离线）
export JAVA_HOME="C:/path/to/jdk-17.0.20.1+1"
"$JAVA_HOME/bin/java" -version
gradle-8.14.3/bin/gradle assembleStandardRelease --offline -g gradle-home
# 产物：src149/app/build/outputs/apk/standard/release/app-standard-release.apk

# 5. 装上电视
adb install -r src149/app/build/outputs/apk/standard/release/app-standard-release.apk
```

---

## 八、确认补丁覆盖完整（没有「只在成品里」的改动）

打完补丁的树和「真正编出 APK 的那棵树」应当逐文件一致。两条命令即可自证：

```bash
# A. 干净源 + apply.py 的产出，与编出 APK 的源码树比（排除构建产物与签名材料）
diff -rq build-app-src patched-src -x build -x .gradle -x .kotlin -x local -x local.properties
# 期望：没有任何输出

# B. 与上游的差异清单（看清到底动了什么）
diff -rq upstream-src patched-src
```

改了什么、每条补丁落在哪个文件、为什么这么改，`apply.py` 里每条补丁的 `what`/注释就写着，
跑一遍 `python3 custom/apply.py <干净源码>` 也会把 38 条补丁逐条打印出来。

> 因为定制里有一整步**包名全量改名**（`app.orchard.tv` → `com.oneplus.tv.launcher2`），
> 树里每个 `.kt` 的路径都变了，所以不存在一份能 `git apply` 的文本 patch —— 那会长成几百个删除+新增。
> 补丁只能以「脚本 + 锚点」的形式存在，`apply.py` 就是它，也是 CI 唯一使用的东西。

---

## 九、几个容易踩的坑

- **别把 `local/` 提交上去**。CI 里推 `src/<tag>` 分支的步骤排在写密钥之前，并且会先断言 `local/` 不存在。
- **fork 的默认分支**必须是放着 `custom/` 与 `.github/` 的那个分支，否则 `schedule` 不会触发。
- GitHub 对 **60 天无提交**的仓库会暂停定时任务。这里每次发版都会 push `src/<tag>` 分支，所以会一直活着；
  万一被停了，Actions 页面点一下 **Enable workflow**。
- **GitHub 的定时任务不保证准点**：`schedule` 是 best effort，高峰期会被延后，严重时整班都不出现
  （2026-10-28 实测过 08:23 那一班完全没有 run 记录）。所以频率设成**每小时一次**
  （`cron: "23 * * * *"`，UTC 的每小时第 23 分 = 北京时间 0X:23），不要设成每 6 小时 ——
  上游一天能发好几个版本，6 小时一轮最坏要等 6 小时。跑得勤几乎不要钱：已经做过的版本会走到
  「取上游最新 release」就 `skip`，**根本不编译**；而漏掉一班最多也只晚 1 小时。
  注意看运行记录里的 `workflow_dispatch` / `schedule` 区分：手动跑和定时跑走的是同一条路。
- **不要手动改 release 里的 `aerial-standard.apk` 文件名**：应用内更新按 `aerial-<flavor>.apk` 精确取名字。
- 电视上覆盖安装前，确认旧包也是这把密钥签的（`apksigner verify --print-certs` 看 SHA-256）。
- **Gradle 偶发在写 APK 那一步抛 `IOException: Failed to create …apk`**：不是代码问题（Kotlin
  编译那时已经过了）。删掉 `app/build/outputs/apk/standard` 重跑即可，前面的任务全是 up-to-date，
  十几秒就好。
- **主进程名不是包名**。补丁把主进程挂在 `:com.heytap.tv.remote` 上，所以
  `pidof com.oneplus.tv.launcher2` 永远查不到；`ps` / `dumpsys gfxinfo` / `dumpsys content` 要用
  `com.oneplus.tv.launcher2:com.heytap.tv.remote`。
- **adb 不能 root**（`adbd cannot run as root in production builds`），`/data/data` 读不到，
  别指望读应用的 SharedPreferences 去看它记了什么。要看只能加日志再编一版。
- **降级安装被系统拒**（`INSTALL_FAILED_VERSION_DOWNGRADE`）：`adb -d` 只对可调试包有效，
  `pm install -r -d` 也一样。要装旧版只能卸载（数据会丢），或把旧源码的 versionCode 抬高重编。
- **默认桌面没固定时，禁用 Aerial 会让遥控全哑**：系统不知道该起谁，于是没有任何桌面在跑，
  方向键没人接收，看起来像遥控坏了。先按一次 HOME，在弹出的「选择应用」里勾「始终」。
