# Aerial OPPO 定制版：自动同步上游 + 自动发版

配置一次，之后**上游发新 release，6 小时内本仓库自动出一个同版本号的定制包**（签名、包名、全部定制都在）。
你只需要装一次，以后电视上点「检查更新」就能装到新版本。

---

## 一、需要放进仓库的两个文件

```
custom/apply.py                              # 全部定制逻辑（唯一真相）
.github/workflows/sync-upstream-release.yml  # CI：拉上游 → 打补丁 → 签名 → 发 release
```

`custom/files/` 是给「需要整份新增的源文件」用的，当前定制**全是定点补丁，不需要它**，
所以这个目录可以不存在（`apply.py` 会打印「没有新增文件，跳过」）。以后真要加整份文件，
建 `custom/files/` 把 `.kt` 丢进去即可，编译时会被复制进源码并跟着改名一起搬家。

除此之外**不要提交**任何上游源码。CI 每次都从上游 tag 重新解压一份干净的，从不复用旧目录。

```bash
cd <你 clone 的 tushoning/xxx 目录>
mkdir -p custom .github/workflows
# 把上面两个文件放到对应位置
git add -A
git commit -m "OPPO 定制：补丁脚本 + 自动发版流程"
git push
```

> 仓库名不是 `aerial-tv` 也没关系：CI 里用 `${{ github.repository }}` 自动取，
> 应用内更新地址、release 上传全跟着实际仓库走，**代码里没有写死仓库名**。

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

之后**不用再管**：定时任务每 6 小时看一次上游，有新 tag 就自动出包。

---

## 五、这些是怎么自动接上的

| 环节 | 做法 |
|---|---|
| 发现新版 | `schedule` 每 6 小时查 `pengchujin/aerial-tv` 的最新 release（fork 收不到上游的 release 事件，只能轮询） |
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

---

## 八、几个容易踩的坑

- **别把 `local/` 提交上去**。CI 里推 `src/<tag>` 分支的步骤排在写密钥之前，并且会先断言 `local/` 不存在。
- **fork 的默认分支**必须是放这三个文件的那个分支，否则 `schedule` 不会触发。
- GitHub 对 **60 天无提交**的仓库会暂停定时任务。这里每次发版都会 push `src/<tag>` 分支，所以会一直活着；
  万一被停了，Actions 页面点一下 **Enable workflow**。
- **不要手动改 release 里的 `aerial-standard.apk` 文件名**：应用内更新按 `aerial-<flavor>.apk` 精确取名字。
- 电视上覆盖安装前，确认旧包也是这把密钥签的（`apksigner verify --print-certs` 看 SHA-256）。
