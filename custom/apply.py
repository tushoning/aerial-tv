#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把上游 Aerial TV 的源码改成 OPPO ColorOS TV 定制版。

    python3 custom/apply.py <上游源码目录> [--version 1.46] [--repo 用户名/仓库名]

--repo 不传时读环境变量 AERIAL_REPO，再退到 GITHUB_REPOSITORY（CI 里自动就是本仓库）。

执行顺序就是依赖顺序，别随意调换：

    1. 预检          确认这是「干净的上游源码」，不是已经打过补丁的
    2. 放新增文件    custom/files 下的文件（当前为空）放进老包名目录，跟着改名一起搬家
    3. 全量改名      app.orchard.tv -> com.oneplus.tv.launcher2（源码目录 + 所有引用 + namespace）
    4. 定点补丁      清单 MODERN_HOME、v1+v2 签名、名称设置、更新地址
    5. 终检          任何一条没落地就失败，绝不产出「看起来成功」的错包

每条补丁都是「锚点必须恰好命中一次」。上游改动到这些位置时 CI 会红，
报错里带文件名和期望原文，照着改本文件即可。
"""

import argparse
import os
import re
import shutil
import sys

PKG = "com.oneplus.tv.launcher2"
OLD = "app.orchard.tv"
PKG_PATH = PKG.replace(".", "/")
OLD_PATH = OLD.replace(".", "/")

GRADLE = "app/build.gradle.kts"
MANIFEST = "app/src/standard/AndroidManifest.xml"
STRINGS_EN = "app/src/main/res/values/strings.xml"
STRINGS_ZH = "app/src/main/res/values-zh/strings.xml"

# 收集所有失败，最后一起报，一次就能把上游的改动全看明白。
PROBLEMS = []


def j(rel):
    """app/src/main/java/<新包名>/ 下的文件。"""
    return "app/src/main/java/%s/%s" % (PKG_PATH, rel)


def die(message):
    print("\n[apply.py 失败] " + message, file=sys.stderr)
    sys.exit(1)


def fail(message):
    PROBLEMS.append(message)


def short(rel):
    prefix = "app/src/main/java/%s/" % PKG_PATH
    return rel[len(prefix):] if rel.startswith(prefix) else rel


def fence(text, pad="      | "):
    return "\n".join(pad + line for line in text.splitlines())


def read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        fail("%s\n    读不到文件：%s" % (rel, exc))
        return None


def write(root, rel, text):
    with open(os.path.join(root, rel), "w", encoding="utf-8", newline="") as f:
        f.write(text)


def patch(root, rel, old, new, what):
    """把 old 换成 new；old 必须恰好出现一次，否则记账等最后统一报错。"""
    text = read(root, rel)
    if text is None:
        return
    hits = text.count(old)
    if hits != 1:
        why = "上游可能删了或改了这一段" if hits == 0 else "这段在上游出现了 %d 次，锚点不够独特" % hits
        fail(
            "%s\n    要改的地方：%s\n    问题：锚点命中 %d 次（应当 1 次），%s\n    期望原文：\n%s"
            % (rel, what, hits, why, fence(old))
        )
        return
    write(root, rel, text.replace(old, new))
    print("  ok  %-44s %s" % (short(rel), what))


# ---------------------------------------------------------------- 结构化锚点
#
# 上面那个 patch() 是「照抄一段上游原文当锚点」：上游在里面插一行、换个措辞，锚点就命中 0 次。
# 亮度这块已经因此红过两次（1.57 在 AppTile 里加 PI/cos，1.61 在 level() 里加极米分支）。
# 所以这几处改成认结构，上游只改块内容时不会失配：
#
#   insert_in_body()   只认函数签名，把代码插到块的最前面 / 最末尾
#   set_call_arg()     只认调用里某一行的参数，改它所属调用里的另一个参数
#   ensure_import()    按「行」补 import，按字母序插
#
# 三个都带 marker：内容已经在文件里就算做完，同一份源码重复跑不会插两遍。


def _indent_of(line):
    return re.match(r"[ \t]*", line).group(0)


def _block(lines, indent):
    """把若干行源码按 indent 缩进成一段；空行不留尾随空格。"""
    return "".join(indent + line + "\n" if line.strip() else "\n" for line in lines)


def _skip_literal(text, i):
    """i 在字符串/字符/注释开头时，返回它结束后的下标；否则返回 None。"""
    n = len(text)
    if text[i] == '"':
        if text.startswith('"""', i):
            j = text.find('"""', i + 3)
            return n if j < 0 else j + 3
        i += 1
        while i < n:
            if text[i] == "\\":
                i += 2
            elif text[i] == '"':
                return i + 1
            elif text[i] == "\n":
                return i + 1  # 普通字符串不跨行，别跑飞
            else:
                i += 1
        return n
    if text[i] == "'":
        i += 1
        while i < n:
            if text[i] == "\\":
                i += 2
            elif text[i] == "'":
                return i + 1
            elif text[i] == "\n":
                return i + 1
            else:
                i += 1
        return n
    if text.startswith("//", i):
        j = text.find("\n", i)
        return n if j < 0 else j
    if text.startswith("/*", i):
        j = text.find("*/", i + 2)
        return n if j < 0 else j + 2
    return None


def _close_brace(text, start):
    """start 落在 '{' 之后，返回与它配对的 '}' 下标；-1 表示没闭合。"""
    depth = 1
    i = start
    n = len(text)
    while i < n:
        skip = _skip_literal(text, i)
        if skip is not None:
            i = skip
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _close_paren(text, start):
    """start 落在 '(' 之后，返回与它配对的 ')' 下标；-1 表示没闭合。"""
    depth = 1
    i = start
    n = len(text)
    while i < n:
        skip = _skip_literal(text, i)
        if skip is not None:
            i = skip
            continue
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _value_end(text, start):
    """start 在一个参数值开头，返回这个值结束的位置（深度 0 的 ',' 或调用收尾的 ')'）。"""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        skip = _skip_literal(text, i)
        if skip is not None:
            i = skip
            continue
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif ch == "," and depth == 0:
            return i
        i += 1
    return n


def _call_span(text, pos):
    """pos 落在某个调用的参数里时，返回最内层那个调用的 (开, 闭) 下标。

    从 pos 往前找 '('，用括号配对确认它确实把 pos 包在里面；先找到的就是最内层。
    这样参数值里自带括号（比如 `steps = if (xgimi) … else 20`）也不会被当成调用。
    """
    i = pos
    n = len(text)
    while i > 0:
        i -= 1
        if text[i] != "(":
            continue
        close = _close_paren(text, i + 1)
        if close >= pos:
            return i, close
    return None


def _only_match(text, pattern, rel, what):
    found = list(re.finditer(pattern, text, re.M))
    if len(found) == 1:
        return found[0]
    fail(
        "%s\n    要改的地方：%s\n    问题：按结构定位「%s」命中 %d 次（应当 1 次）。\n"
        "    上游多半改了函数签名（参数、返回类型，或改成扩展函数）。按下面这个正则更新 apply.py：\n%s"
        % (rel, what, pattern.strip(), len(found), fence(pattern))
    )
    return None


def insert_in_body(root, rel, signature, lines, what, marker, at="start"):
    """把 lines 插进 signature 指的块：at="start" 最前面，at="end" 闭合花括号前。

    signature 只描述「一行签名 + 换行」，块里的内容一律不看。上游在 level() 里加分支、
    在 init 里多写两句，都不会影响这里。
    """
    text = read(root, rel)
    if text is None:
        return
    if marker in text:
        print("  ok  %-44s %s（已存在）" % (short(rel), what))
        return
    found = _only_match(text, signature, rel, what)
    if found is None:
        return
    if at == "start":
        indent = _indent_of(found.group(0)) + "    "
        body = _block(lines, indent)
        if not text[: found.end()].endswith("\n"):
            body = "\n" + body
        text = text[: found.end()] + body + text[found.end():]
    else:
        close = _close_brace(text, found.end())
        if close < 0:
            fail("%s\n    要改的地方：%s\n    问题：花括号没闭合，定位不到块尾" % (rel, what))
            return
        line_start = text.rfind("\n", 0, close) + 1
        indent = _indent_of(text[line_start:close]) + "    "
        text = text[:line_start] + _block(lines, indent) + text[line_start:]
    write(root, rel, text)
    print("  ok  %-44s %s" % (short(rel), what))


def set_call_arg(root, rel, anchor, arg, lines, what, marker):
    """把 anchor 那一行所在调用里的 `arg = …` 换成 lines 写的 lambda 体。

    只认调用里的一个参数名：参数顺序、别的参数怎么写、原来 arg 是 {} 还是多行 lambda，
    都不影响。上游给这一行加参数、挪位置也仍然能用。
    """
    text = read(root, rel)
    if text is None:
        return
    if text.count(anchor) != 1:
        fail(
            "%s\n    要改的地方：%s\n    问题：锚点「%s」命中 %d 次（应当 1 次）。\n"
            "    上游可能换了参数名，或这一整行没了。"
            % (rel, what, anchor.strip(), text.count(anchor))
        )
        return
    span = _call_span(text, text.find(anchor))
    if span is None:
        fail("%s\n    要改的地方：%s\n    问题：找不到「%s」所在的那个调用" % (rel, what, anchor.strip()))
        return
    open_at, close_at = span
    found = re.compile(r"(?m)^([ \t]*)%s = " % re.escape(arg)).search(text, open_at, close_at)
    if found is None:
        fail(
            "%s\n    要改的地方：%s\n    问题：这个调用里没有 %s = …（上游可能删了或改名了）"
            % (rel, what, arg)
        )
        return
    # marker 只在这个参数自己的值里找：上游别处本来就有同名调用（比如亮度不可调时的 MenuRow），
    # 拿全文找会误判成「已改过」。
    value_end = _value_end(text, found.end())
    if marker in text[found.end():value_end]:
        print("  ok  %-44s %s（已存在）" % (short(rel), what))
        return
    indent = found.group(1)
    value = "{\n" + _block(lines, indent + "    ") + indent + "}"
    text = text[: found.end()] + value + text[value_end:]
    write(root, rel, text)
    print("  ok  %-44s %s" % (short(rel), what))


def ensure_import(root, rel, line, what):
    """按「行」补一条 import：没有就插到字母序的位置上。

    上游爱在 import 段里插一行（1.50 加 SourceKind、1.61 加 Channel、1.57 加 PI/cos），
    所以这里不拿相邻几行原文当锚点，也不会补出重复的一条。
    """
    text = read(root, rel)
    if text is None:
        return
    if re.search(r"(?m)^%s$" % re.escape(line), text):
        print("  ok  %-44s %s（已有）" % (short(rel), what))
        return
    lines = text.split("\n")
    imports = [i for i, item in enumerate(lines) if item.startswith("import ")]
    if not imports:
        fail("%s\n    要改的地方：%s\n    问题：文件里没有 import 段" % (rel, what))
        return
    at = imports[-1] + 1
    for i in imports:
        if lines[i] > line:
            at = i
            break
    lines.insert(at, line)
    write(root, rel, "\n".join(lines))
    print("  ok  %-44s %s" % (short(rel), what))


# ---------------------------------------------------------------- 1. 预检


def preflight(root):
    if not os.path.isfile(os.path.join(root, GRADLE)):
        die("%s 不像 Aerial 源码：没有 %s" % (root, GRADLE))
    if not os.path.isdir(os.path.join(root, "app/src/standard")):
        die("没有 app/src/standard：上游的 flavor 结构变了，standard 包无从下手")

    java = os.path.join(root, "app/src/main/java")
    if not os.path.isdir(os.path.join(java, OLD_PATH)):
        if os.path.isdir(os.path.join(java, PKG_PATH)):
            die(
                "这份源码已经改过包名了（已有 %s）。\n"
                "补丁只能打在原始上游源码上：请从 release 重新解压一份干净的再跑，"
                "否则第二次改名会把已经改好的东西弄成半新半旧。" % PKG
            )
        die("app/src/main/java/%s 不存在：上游包名变了？" % OLD_PATH)

    with open(os.path.join(root, GRADLE), encoding="utf-8") as f:
        if 'namespace = "%s"' % OLD not in f.read():
            die("%s 里没有 namespace = \"%s\"：上游的写法变了" % (GRADLE, OLD))
    print("  ok  源码干净：%s 判据齐全" % OLD)


# ---------------------------------------------------------------- 2. 放新增文件


def install_files(root):
    here = os.path.dirname(os.path.abspath(__file__))
    files_dir = os.path.join(here, "files")
    # 放进老包名目录，第 3 步搬家时连它们一起搬、包名一起换。
    target = os.path.join(root, "app/src/main/java", *OLD.split("."), "system")
    if not os.path.isdir(target):
        die("没找到 %s/system：上游目录结构变了" % OLD_PATH)

    # 没有新增文件是正常状态（当前定制全是定点补丁），此时 custom/files 可以是空的，
    # 也可能整个目录都不存在——Git 不跟踪空目录，删掉最后一个文件后它就消失了。
    names = []
    if os.path.isdir(files_dir):
        names = [n for n in sorted(os.listdir(files_dir)) if os.path.isfile(os.path.join(files_dir, n))]
    if not names:
        print("  --  %-44s 没有新增文件，跳过" % "custom/files")
        return names
    for name in names:
        shutil.copyfile(os.path.join(files_dir, name), os.path.join(target, name))
        print("  ok  %-44s 新增" % ("system/" + name))
    return names


# ---------------------------------------------------------------- 3. 包名全量改名


def rename_package(root):
    """搬 Kotlin 包目录，再把源码里所有老包名换掉（含 namespace / applicationId）。"""
    src = os.path.join(root, "app/src")
    moved = 0
    for source_set in sorted(os.listdir(src)):
        old_dir = os.path.join(src, source_set, "java", *OLD.split("."))
        if not os.path.isdir(old_dir):
            continue
        new_dir = os.path.join(src, source_set, "java", *PKG.split("."))
        os.makedirs(os.path.dirname(new_dir), exist_ok=True)
        if os.path.exists(new_dir):
            shutil.rmtree(new_dir)
        shutil.move(old_dir, new_dir)
        # 顺手清掉搬家后留下的空壳（app/orchard）。
        base = os.path.join(src, source_set, "java")
        parent = os.path.dirname(old_dir)
        while os.path.abspath(parent) != os.path.abspath(base) and not os.listdir(parent):
            os.rmdir(parent)
            parent = os.path.dirname(parent)
        moved += 1
        print("  ok  app/src/%s/java/%s -> %s" % (source_set, OLD_PATH, PKG_PATH))

    if moved == 0:
        die("没有可搬的源码目录：%s" % OLD_PATH)

    suffixes = (".kt", ".java", ".xml", ".kts", ".pro", ".properties", ".txt", ".json")
    changed = 0
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "app")):
        dirnames[:] = [d for d in dirnames if d not in ("build", ".gradle", ".kotlin")]
        for name in filenames:
            if not name.endswith(suffixes):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            if OLD not in text:
                continue
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace(OLD, PKG))
            changed += 1
    print("  ok  %-44s %d 个文件" % ("包名引用换成 " + PKG, changed))


# ---------------------------------------------------------------- 4. 定点补丁


def set_version(root, version):
    """版本号对齐上游 tag：应用内更新靠它拼 release 地址。"""
    if not version:
        return
    text = read(root, GRADLE)
    if text is None:
        return
    text, hits = re.subn(
        r'(?m)^        versionName = "[^"]+"$', '        versionName = "%s"' % version, text, count=1
    )
    if hits != 1:
        fail("%s\n    要改的地方：defaultConfig 里的 versionName\n    问题：没匹配到（上游缩进可能变了）" % GRADLE)
        return
    write(root, GRADLE, text)
    print("  ok  %-44s %s" % (GRADLE, "versionName = " + version))


def patch_signature(root):
    # OPPO 的安装器和老固件读 v1，新校验器读 v2，两个都显式打开。
    patch(
        root, GRADLE,
        '                keyPassword = p.getProperty("keyPassword")\n            }',
        '                keyPassword = p.getProperty("keyPassword")\n'
        '                // JAR (v1) + APK Signature Scheme v2, both explicit: OPPO\'s installer and\n'
        '                // Android 9 read v1 for a sideload, newer verifiers prefer v2.\n'
        '                enableV1Signing = true\n'
        '                enableV2Signing = true\n'
        '            }',
        "release 强制 v1+v2 签名",
    )


def patch_manifest(root):
    # 少了 MODERN_HOME，OPPO 开机不把桌面角色交出去，会一直卡在品牌动画。
    patch(
        root, MANIFEST,
        '                <category android:name="android.intent.category.HOME" />\n'
        '                <category android:name="android.intent.category.DEFAULT" />\n'
        '            </intent-filter>',
        '                <category android:name="android.intent.category.HOME" />\n'
        '                <category android:name="android.intent.category.DEFAULT" />\n'
        '                <!-- OPPO ColorOS TV only hands the launcher role to a home activity that\n'
        '                     carries this marker; without it the TV waits forever for "its"\n'
        '                     launcher and hangs on the boot logo. See docs/devices/oppo-coloros-tv.md. -->\n'
        '                <category android:name="com.heytap.tv.launcher.category.MODERN_HOME" />\n'
        '            </intent-filter>',
        "HOME 加 MODERN_HOME 类别",
    )

    # 背光只能由固件的 tvapisvc 驱动，它按「进程名包含 com.heytap.tv.remote」放行。
    # 整个桌面直接跑在这个进程名里：进程数不增加，OppoBacklight 也就不必跨进程。
    # 服务上再显式写一遍是双保险——merge 万一没带过来 application 的 process，
    # 服务仍旧落在白名单进程里，功能不受影响，只是多回一个进程。
    patch(
        root, MANIFEST,
        '    <application>',
        '    <!-- 进程名带 :com.heytap.tv.remote，固件的 tvapisvc 才会接受本应用的背光调用。\n'
        '         主进程名不含白名单串，所以让进程名本身带上它，比另开一个进程省事。 -->\n'
        '    <application android:process=":com.heytap.tv.remote">',
        "主进程挂白名单进程名",
    )

    patch(
        root, MANIFEST,
        '        </activity>\n'
        '    </application>',
        '        </activity>\n'
        '        <service\n'
        '            android:name="%s.system.OppoBrightnessService"\n'
        '            android:exported="false"\n'
        '            android:process=":com.heytap.tv.remote" />\n'
        '    </application>' % PKG,
        "背光服务留在白名单进程",
    )


def patch_brightness(root):
    """OPPO 的面板只认固件服务，Settings 是影子数据：把这一支接到 Brightness 最前面。

    这一组全部按结构定位（见上面的结构化锚点）：
      - level() / adjust()：认函数签名，插到函数体第一行，上游在函数体里加分支不影响；
      - 控制中心那条：认 onStep = vm::adjustBrightness 这个参数，改它所属调用的 onClick；
      - HomeViewModel：import 按行补，init / onCleared 的钩子插在块尾。
    上游改了签名或参数名才会报错，那才是真需要人看的时候。
    """
    rel = j("system/Brightness.kt")
    insert_in_body(
        root, rel,
        r"^[ \t]*fun level\(context: Context\)[ \t]*:[ \t]*Float\?[ \t]*\{\n",
        [
            "// OPPO ColorOS TV：背光在固件里，Settings 写得进去但面板不动。",
            "if (OppoBacklight.usable(context)) return OppoBacklight.level(context) / 100f",
        ],
        "亮度读取走固件服务",
        "return OppoBacklight.level(context) / 100f",
    )
    insert_in_body(
        root, rel,
        r"^[ \t]*fun adjust\(context: Context, direction: Int\)[ \t]*:[ \t]*Float\?[ \t]*\{\n",
        [
            "if (OppoBacklight.usable(context)) {",
            "    // 5% 一档，和设置页那条一致；0 会黑屏，所以下限是 1。",
            "    val from = OppoBacklight.level(context)",
            "    val next = (if (direction > 0) (from / 5 + 1) * 5 else ((from + 4) / 5 - 1) * 5).coerceIn(1, 100)",
            "    OppoBacklight.set(context, next)",
            "    return next / 100f",
            "}",
        ],
        "亮度步进走固件服务",
        "OppoBacklight.set(context, next)",
    )

    # 控制中心那一条：左右键调背光，确认键进电视自己的图像设置。没有背光可调时
    # 上游本来就是 MenuRow 跳到同一个地方，所以两边的去处一致，不会出现两种行为。
    set_call_arg(
        root, j("ui/control/ControlCenter.kt"),
        "onStep = vm::adjustBrightness,",
        "onClick",
        [
            "vm.overlay = Overlay.None",
            "SystemProbe.openPictureSettings(context)",
        ],
        "亮度条确认键跳转图像设置",
        "SystemProbe.openPictureSettings(context)",
    )

    # 面板真值（query）回来时可能和记账值不同：小布或设置页改过亮度，记账值就旧了。
    # 让 ViewModel 接住这个校正，滑块才会跟着变；否则要等下次自己调才刷新。
    vm = j("HomeViewModel.kt")
    ensure_import(
        root, vm,
        "import %s.system.OppoBacklight" % PKG,
        "HomeViewModel 引用 OppoBacklight",
    )
    insert_in_body(
        root, vm,
        r"^[ \t]*init[ \t]*\{\n",
        [
            "// OPPO 的背光在固件里，Aerial 记下的只是副本：读到面板真值时它会报回来一次",
            "// （OppoBacklight.calibrate）。用小布或电视自己的图像菜单改过亮度，靠这一条才不显示旧值。",
            "OppoBacklight.onLevel = { percent -> brightness = percent / 100f }",
        ],
        "HomeViewModel 接住亮度校正",
        "OppoBacklight.onLevel = { percent ->",
        at="end",
    )
    insert_in_body(
        root, vm,
        r"^[ \t]*override fun onCleared\(\)[ \t]*\{\n",
        ["OppoBacklight.onLevel = null"],
        "HomeViewModel 松手时摘掉回调",
        "OppoBacklight.onLevel = null",
        at="end",
    )


def patch_names(root):
    """名称设置拆成两套：第一排（画在图标里）、第一排以下（画在图标下方）。"""
    settings = j("settings/AerialSettings.kt")
    tile = j("ui/home/AppTile.kt")
    home = j("ui/home/HomeScreen.kt")
    sheet = j("ui/wallpaper/WallpaperSheet.kt")

    patch(
        root, settings,
        "/** Aerial's own preferences. Compose state, written on the main thread only. */\nobject AerialSettings {",
        "/** The top row's names, drawn inside the tiles: the selected app's, every one, or none. */\n"
        "enum class PlateNames(val key: String) {\n"
        '    Selected("selected"), All("all"), Off("off");\n'
        "\n"
        "    companion object {\n"
        "        fun of(key: String?) = entries.firstOrNull { it.key == key } ?: Selected\n"
        "    }\n"
        "}\n"
        "\n"
        "/** Aerial's own preferences. Compose state, written on the main thread only. */\n"
        "object AerialSettings {",
        "新增 PlateNames 枚举",
    )
    patch(
        root, settings,
        "    var showAllNames by mutableStateOf(false); private set",
        "    var showAllNames by mutableStateOf(false); private set\n"
        "\n"
        "    /** The glass plate's own names, setting the top row apart from the grid below it. */\n"
        "    var plateNames by mutableStateOf(PlateNames.Selected); private set",
        "新增 plateNames 状态",
    )
    patch(
        root, settings,
        '        showAllNames = prefs.getBoolean("show_all_names", false)',
        '        showAllNames = prefs.getBoolean("show_all_names", false)\n'
        '        plateNames = PlateNames.of(prefs.getString("plate_names", null))',
        "读 plateNames",
    )
    patch(
        root, settings,
        "    fun chooseShowAllNames(on: Boolean) {\n"
        "        showAllNames = on\n"
        '        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putBoolean("show_all_names", on) }\n'
        "    }",
        "    fun chooseShowAllNames(on: Boolean) {\n"
        "        showAllNames = on\n"
        '        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putBoolean("show_all_names", on) }\n'
        "    }\n"
        "\n"
        "    fun choosePlateNames(names: PlateNames) {\n"
        "        plateNames = names\n"
        '        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putString("plate_names", names.key) }\n'
        "    }",
        "写 plateNames",
    )

    # kotlin.math.PI / cos 不补：上游 1.57 起 AppTile 自己引了（SineEasing 用），再补一遍就是两份。
    # sin 上游还没有，仍然补。逐条 ensure_import：上游在 import 段里加什么、删什么都顶不开这一块，
    # 已经有的也不重复补。终检会核对 PI / cos / sin 三行都在，而不是等 Kotlin 编译才炸。
    for imp in (
        "import %s.settings.PlateNames" % PKG,
        "import androidx.compose.ui.geometry.Size",
        "import androidx.compose.ui.graphics.Canvas as GraphicsCanvas",
        "import androidx.compose.ui.graphics.drawscope.CanvasDrawScope",
        "import androidx.compose.ui.platform.LocalDensity",
        "import androidx.compose.ui.platform.LocalLayoutDirection",
        "import androidx.compose.ui.text.drawText",
        "import androidx.compose.ui.text.font.FontWeight",
        "import androidx.compose.ui.text.rememberTextMeasurer",
        "import androidx.compose.ui.unit.Constraints",
        "import androidx.compose.ui.unit.LayoutDirection",
        "import kotlin.math.sin",
    ):
        ensure_import(root, tile, imp, "导入 %s" % imp.rsplit(".", 1)[-1])
    patch(
        root, tile,
        "private val RingWidth = 3.dp",
        "private val RingWidth = 3.dp\n"
        "\n"
        "// A name inside a tile on the glass plate: a band along its bottom edge.\n"
        "private val NameInTileBand = 24.dp\n"
        "private val NameInTileBottom = 6.dp\n"
        "\n"
        "// The plate's names carry a hard outline and a short drop shadow instead of a blurred one:\n"
        "// both stay inside the band, so a fading layer never crops them.\n"
        "private val NameOutline = 1.1.dp\n"
        "private val NameDrop = 1.0.dp",
        "图标内名称的尺寸常量",
    )
    patch(
        root, tile,
        "    /** False on the glass plate: the top row shows no names there, as on tvOS 26. */\n"
        "    showName: Boolean = true,",
        "    /** False on the glass plate: the top row shows no names there, as on tvOS 26. */\n"
        "    showName: Boolean = true,\n"
        "    /** On the glass plate: whose name shows inside the tile. Null off the plate. */\n"
        "    plateName: PlateNames? = null,",
        "AppTile 新增 plateName 参数",
    )
    patch(
        root, tile,
        "                    translationY = height.toPx() * (scale - 1f) / 2f - (1f - lift) * 6.dp.toPx() +\n"
        "                        if (nameOnPlate) 0f else ring * ringIn * (RingGap + RingWidth).toPx() * scale\n"
        "                },\n"
        "        )\n"
        "    }\n"
        "}",
        "                    translationY = height.toPx() * (scale - 1f) / 2f - (1f - lift) * 6.dp.toPx() +\n"
        "                        if (nameOnPlate) 0f else ring * ringIn * (RingGap + RingWidth).toPx() * scale\n"
        "                },\n"
        "        )\n"
        "\n"
        "        // On the glass plate the name sits inside the tile, bottom centre: the grid's own\n"
        "        // type at its own size, one step bolder, outlined and shadowed so the artwork\n"
        "        // underneath cannot swallow it.\n"
        "        if (plateName != null && plateName != PlateNames.Off &&\n"
        "            (plateName == PlateNames.All || lift > 0.01f)\n"
        "        ) {\n"
        "            Box(\n"
        "                modifier = Modifier\n"
        "                    .align(Alignment.BottomCenter)\n"
        "                    .offset(y = -NameInTileBottom)\n"
        "                    .requiredSize(width - 12.dp, NameInTileBand)\n"
        "                    .graphicsLayer {\n"
        "                        alpha = if (plateName == PlateNames.All) 0.7f + 0.3f * lift else lift\n"
        "                        // The tile grows about its centre, so its bottom edge travels down by half.\n"
        "                        translationY = height.toPx() * (scale - 1f) / 2f\n"
        "                    },\n"
        "            ) {\n"
        "                PlateTileName(app.label, width - 12.dp)\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}",
        "图标内画名称",
    )
    patch(
        root, tile,
        "private fun focusedPx(px: Float): Int {",
        "/**\n"
        " * The name inside a plate's tile: [Type.Headline] at its own size, one step bolder, with a\n"
        " * hard outline and a short drop shadow.\n"
        " *\n"
        " * Not a blurred [Shadow]. While a tile is still growing the name is drawn through a fading\n"
        " * layer, and a blur reaches past this band, so the layer crops it and leaves a rectangle of\n"
        " * half-transparent grey over the artwork - a seam that shows on light tiles mid-animation.\n"
        " *\n"
        " * Baked into one picture, not drawn ten times a frame: the outline is the same text at eight\n"
        " * offsets, and separate text passes can land a frame apart while the tile is growing, which\n"
        " * reads as the name sliding off its own shadow. One picture cannot come apart.\n"
        " */\n"
        "@Composable\n"
        "private fun PlateTileName(text: String, maxWidth: Dp) {\n"
        "    val measurer = rememberTextMeasurer()\n"
        "    val density = LocalDensity.current\n"
        "    val direction = LocalLayoutDirection.current\n"
        "    val style = remember {\n"
        "        Type.Headline.copy(fontWeight = FontWeight.W700, textAlign = TextAlign.Center)\n"
        "    }\n"
        "    val widthPx = with(density) { maxWidth.roundToPx() }\n"
        "    val bandPx = with(density) { NameInTileBand.roundToPx() }\n"
        "    val name = remember(text, widthPx, bandPx, style, density, direction) {\n"
        "        val layout = measurer.measure(\n"
        "            text = text,\n"
        "            style = style,\n"
        "            maxLines = 1,\n"
        "            overflow = TextOverflow.Ellipsis,\n"
        "            constraints = Constraints(maxWidth = widthPx),\n"
        "        )\n"
        "        val image = ImageBitmap(widthPx.coerceAtLeast(1), bandPx.coerceAtLeast(1))\n"
        "        CanvasDrawScope().draw(\n"
        "            density,\n"
        "            direction,\n"
        "            GraphicsCanvas(image),\n"
        "            Size(widthPx.toFloat(), bandPx.toFloat()),\n"
        "        ) {\n"
        "            val at = Offset(\n"
        "                (widthPx - layout.size.width) / 2f,\n"
        "                (bandPx - layout.size.height) / 2f,\n"
        "            )\n"
        "            // The drop shadow: one more copy, a little lower.\n"
        "            drawText(\n"
        "                layout,\n"
        "                color = Color.Black,\n"
        "                alpha = 0.35f,\n"
        "                topLeft = at + Offset(0f, NameDrop.toPx()),\n"
        "            )\n"
        "            // The outline: the same glyphs all round the fill.\n"
        "            val r = NameOutline.toPx()\n"
        "            for (i in 0 until 8) {\n"
        "                val a = i * PI / 4\n"
        "                drawText(\n"
        "                    layout,\n"
        "                    color = Color.Black,\n"
        "                    alpha = 0.8f,\n"
        "                    topLeft = at + Offset(cos(a).toFloat() * r, sin(a).toFloat() * r),\n"
        "                )\n"
        "            }\n"
        "            drawText(layout, topLeft = at)\n"
        "        }\n"
        "        image\n"
        "    }\n"
        "    Canvas(Modifier.fillMaxSize()) {\n"
        "        drawImage(name)\n"
        "    }\n"
        "}\n"
        "\n"
        "private fun focusedPx(px: Float): Int {",
        "图标内名称：烘焙成一张位图",
    )

    # 桌面上把两套设置分开：第一排的名称画在图标里，底板不再为它变高。
    patch(
        root, home,
        "        // With every name shown, the top row's go on its glass plate, which grows to hold them.\n"
        "        val plateNames = AerialSettings.showAllNames && AerialSettings.homePlate",
        "        // The top row's names sit inside its tiles now, so the plate never grows to hold them.\n"
        "        val plateNames = false",
        "底板不再为名称增高",
    )
    patch(
        root, home,
        "                            showName = plate == null || index >= COLUMNS || plateNames,\n"
        "                            nameOnPlate = plate != null && index < COLUMNS,",
        "                            // The top row's names are drawn inside its tiles (plateName);\n"
        "                            // the grid's keep the original rule below the tile.\n"
        "                            showName = index >= COLUMNS,\n"
        "                            nameOnPlate = plate != null && index < COLUMNS,\n"
        "                            plateName = if (index < COLUMNS) AerialSettings.plateNames else null,",
        "第一排改用 plateName",
    )

    patch(
        root, sheet,
        "import %s.settings.AerialSettings" % PKG,
        "import %s.settings.AerialSettings\nimport %s.settings.PlateNames" % (PKG, PKG),
        "设置页导入 PlateNames",
    )
    patch(
        root, sheet,
        "                        Choices(\n"
        "                            R.string.st_names, stringResource(R.string.st_names_hint), note,\n"
        "                            listOf(false to R.string.st_names_focused, true to R.string.st_names_all),\n"
        "                            AerialSettings.showAllNames, AerialSettings::chooseShowAllNames,\n"
        "                        )",
        "                        // The glass plate and the grid under it are set apart: the top\n"
        "                        // row's names sit inside its icons, so \"off\" looks nothing like\n"
        "                        // the grid's \"off\".\n"
        "                        Choices(\n"
        "                            R.string.st_plate_names, stringResource(R.string.st_plate_names_hint), note,\n"
        "                            listOf(\n"
        "                                PlateNames.Selected to R.string.st_names_focused,\n"
        "                                PlateNames.All to R.string.st_names_all,\n"
        "                                PlateNames.Off to R.string.st_names_off,\n"
        "                            ),\n"
        "                            AerialSettings.plateNames, AerialSettings::choosePlateNames,\n"
        "                        )\n"
        "                        Choices(\n"
        "                            R.string.st_names, stringResource(R.string.st_names_hint), note,\n"
        "                            listOf(false to R.string.st_names_focused, true to R.string.st_names_all),\n"
        "                            AerialSettings.showAllNames, AerialSettings::chooseShowAllNames,\n"
        "                        )",
        "设置页拆成两条",
    )


def patch_strings(root):
    patch(
        root, STRINGS_EN,
        '    <string name="st_names">App Names</string>\n'
        '    <string name="st_names_focused">Selected App</string>\n'
        '    <string name="st_names_all">All Apps</string>\n'
        '    <string name="st_names_hint">Names under every app, or only under the selected one, as on Apple TV. With every name shown, the top row\\\'s names sit on its glass plate too.</string>',
        '    <string name="st_plate_names">Top Row Names</string>\n'
        '    <string name="st_plate_names_hint">Names of the apps on the glass plate, drawn inside the icons: the selected app\\\'s, every name, or none at all.</string>\n'
        '    <string name="st_names">Other App Names</string>\n'
        '    <string name="st_names_focused">Selected App</string>\n'
        '    <string name="st_names_all">All Apps</string>\n'
        '    <string name="st_names_off">Off</string>\n'
        '    <string name="st_names_hint">Names of the apps below the top row, under their icons: every app, or only the selected one, as on Apple TV. The top row has its own setting.</string>',
        "英文文案",
    )
    patch(
        root, STRINGS_ZH,
        '    <string name="st_names">App 名称</string>\n'
        '    <string name="st_names_focused">只显示选中的</string>\n'
        '    <string name="st_names_all">全部显示</string>\n'
        '    <string name="st_names_hint">每个 App 下面都显示名称，或者像 Apple TV 一样只显示选中的。全部显示时，第一排的名称也显示在玻璃底板里。</string>',
        '    <string name="st_plate_names">第一排 APP 名称</string>\n'
        '    <string name="st_plate_names_hint">玻璃底板上那一排的名称，显示在图标内部：只显示选中的、全部显示，或者关闭显示。</string>\n'
        '    <string name="st_names">其他 APP 名称</string>\n'
        '    <string name="st_names_focused">只显示选中的</string>\n'
        '    <string name="st_names_all">全部显示</string>\n'
        '    <string name="st_names_off">关闭显示</string>\n'
        '    <string name="st_names_hint">第一排以下各排的名称，显示在图标下方：每个 App 都显示，或者像 Apple TV 一样只显示选中的。不影响第一排。</string>',
        "中文文案",
    )


def patch_updates(root, repo):
    """应用内更新全部回本仓库：版本检查读本仓库 release 里的 version.txt，APK 也下同一个 release。

    上游这一套是「官网 version.txt 说最新是几点几 + Release 下 APK」。照搬过来会有一个空窗：
    上游官网先更新，本仓库的包要等 CI 编完签名才有，于是 Control Center 提示有新版本，
    点下载却 404——「下载失败」。改成自己发 version.txt 之后，提示与可下载永远同源。
    """
    if not repo:
        die("没有仓库名：--repo 用户名/仓库名，或设置 AERIAL_REPO / GITHUB_REPOSITORY")
    updates = j("update/Updates.kt")

    patch(
        root, updates,
        '    private const val VERSION_URL = "https://aerial.qust.me/download/version.txt"',
        "    // This fork publishes this file with every release; three routes to it, as for the APK\n"
        "    // below. One mirror going down must not read as \"no new version\".\n"
        "    private val VERSION_URLS = listOf(\n"
        '        "https://ghfast.top/https://github.com/%s/releases/latest/download/version.txt",\n'
        '        "https://gh-proxy.com/https://github.com/%s/releases/latest/download/version.txt",\n'
        '        "https://github.com/%s/releases/latest/download/version.txt",\n'
        "    )" % (repo, repo, repo),
        "版本检查：三级国内加速",
    )
    patch(
        root, updates,
        '                runCatching { read(VERSION_URL).trim().removePrefix("v") }\n'
        '                    .onFailure { Log.w(TAG, "could not check for a new version", it) }\n'
        '                    .getOrNull()\n'
        '                    ?.takeIf { it.matches(Regex("\\\\d+(\\\\.\\\\d+)*")) }',
        "                // A mirror can answer with an error page rather than fail outright, so a\n"
        "                // reply that is not a version number moves on to the next source.\n"
        "                VERSION_URLS.firstNotNullOfOrNull { url ->\n"
        "                    runCatching { read(url).trim().removePrefix(\"v\") }\n"
        '                        .onFailure { Log.w(TAG, "could not check for a new version at $url", it) }\n'
        "                        .getOrNull()\n"
        '                        ?.takeIf { it.matches(Regex("\\\\d+(\\\\.\\\\d+)*")) }\n'
        "                }",
        "版本检查：逐个源试",
    )
    patch(
        root, updates,
        '        val sources = listOf(\n'
        '            "https://aerial.qust.me/download/$name",\n'
        '            "https://github.com/pengchujin/aerial-tv/releases/latest/download/$name",\n'
        '        )',
        '        // This build\'s updates come from its own fork\'s release of the same version, through\n'
        '        // domestic mirrors: the upstream APK is another package signed with another key, so\n'
        '        // verify() would turn it away anyway.\n'
        '        val tag = "v" + (available ?: current)\n'
        '        val sources = listOf(\n'
        '            "https://ghfast.top/https://github.com/%s/releases/download/$tag/$name",\n'
        '            "https://gh-proxy.com/https://github.com/%s/releases/download/$tag/$name",\n'
        '            "https://github.com/%s/releases/download/$tag/$name",\n'
        '        )' % (repo, repo, repo),
        "更新链接改本仓库",
    )


# ---------------------------------------------------------------- 5. 终检


def verify(root, repo, dropped):
    """不看日志、只看源码：该在的都在，才允许打包。"""
    # 老包名必须一个不剩。
    leftovers = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "app")):
        dirnames[:] = [d for d in dirnames if d not in ("build", ".gradle", ".kotlin")]
        for name in filenames:
            if not name.endswith((".kt", ".java", ".xml", ".kts", ".pro")):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    if OLD in f.read():
                        leftovers.append(os.path.relpath(path, root))
            except (UnicodeDecodeError, OSError):
                continue
    if leftovers:
        fail("这些文件还带着老包名 %s：\n%s" % (OLD, fence("\n".join(leftovers))))

    manifest = read(root, MANIFEST) or ""
    if "com.heytap.tv.launcher.category.MODERN_HOME" not in manifest:
        fail("standard 清单里没写进 MODERN_HOME：OPPO 会把开机卡在品牌动画")
    if PKG not in manifest:
        fail("standard 清单里的 MainActivity 没跟着改名")

    gradle = read(root, GRADLE) or ""
    for needle in (
        'namespace = "%s"' % PKG,
        'applicationId = "%s"' % PKG,
        "enableV1Signing = true",
        "enableV2Signing = true",
    ):
        if needle not in gradle:
            fail("%s 里缺 %s" % (GRADLE, needle))

    # 新增文件必须搬到位，且包名跟着换了。
    for name in dropped:
        rel = j("system/" + name)
        text = read(root, rel)
        if text is None:
            continue
        if "package %s.system" % PKG not in text:
            fail("%s 的 package 行没改：仍不是 %s.system" % (rel, PKG))

    for rel, needles in (
        # 亮度这几处是按结构插的：插不进去不会报错，只会「什么都没做」，所以这里再核一遍。
        (
            j("system/Brightness.kt"),
            ("OppoBacklight.usable(context)", "OppoBacklight.set(context, next)"),
        ),
        (
            j("ui/control/ControlCenter.kt"),
            ("vm.overlay = Overlay.None\n                SystemProbe.openPictureSettings(context)",),
        ),
        (
            j("HomeViewModel.kt"),
            (
                "import %s.system.OppoBacklight" % PKG,
                "OppoBacklight.onLevel = { percent -> brightness = percent / 100f }",
                "OppoBacklight.onLevel = null",
            ),
        ),
        # 名字画在图标里要靠 PI / cos / sin：上游自己引的（PI、cos）和本脚本补的（sin）
        # 必须都在，否则是 Kotlin 编译期才报错，比这里报得晚也更难懂。
        (
            j("ui/home/AppTile.kt"),
            ("PlateTileName(", "plateName: PlateNames?", "import kotlin.math.PI",
             "import kotlin.math.cos", "import kotlin.math.sin"),
        ),
        (j("ui/home/HomeScreen.kt"), ("AerialSettings.plateNames",)),
        (j("settings/AerialSettings.kt"), ("enum class PlateNames", "choosePlateNames")),
        (STRINGS_ZH, ('name="st_plate_names"', 'name="st_names_off"')),
        (STRINGS_EN, ('name="st_plate_names"',)),
    ):
        text = read(root, rel)
        if text is None:
            continue
        for needle in needles:
            if needle not in text:
                fail("%s 里缺 %s：这一步没落地" % (rel, needle))

    if repo:
        updates = read(root, j("update/Updates.kt")) or ""
        if "github.com/%s/releases/download" % repo not in updates:
            fail("Update 的 APK 地址没写成 %s\n    （应用内更新会去别的仓库下载，必然 404）" % repo)
        if "github.com/%s/releases/latest/download/version.txt" % repo not in updates:
            fail("Update 的版本检查地址没写成 %s 的 version.txt" % repo)
        if "gh-proxy.com/https://github.com/%s/releases/latest/download/version.txt" % repo not in updates:
            fail("版本检查没有第二级加速源：镜像挂了就会一直显示「没有新版本」")
        if "aerial.qust.me" in updates:
            fail("Update 里还留着上游官网地址：版本会先于本仓库的包被播报，点下载就是 404")

    if PROBLEMS:
        print("\n[apply.py 失败] %d 处没改成：\n" % len(PROBLEMS), file=sys.stderr)
        for i, problem in enumerate(PROBLEMS, 1):
            print("%d) %s\n" % (i, problem), file=sys.stderr)
        print(
            "上游多半改了这些地方。照着上面的「期望原文」更新 custom/apply.py 里的锚点，"
            "本地用上游源码重跑一遍再推。",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  ok  %-44s 全绿" % "终检")


def patch_update_row(root):
    """控制中心那行更新提示改成常驻：没有新版本时也能点一下去查。

    上游只在「有东西可装」时才显示这一行，于是刚更新完的那一版反而没有入口能再查一次。
    这里让它一直在，没新版本就叫「检测更新」；点了查不到结果就灰着显示「暂无新版本」5 秒，
    然后变回可点的「检测更新」。
    """
    # Updates 加一个「等结果」的检查。原来的 checkIfStale 发起就返回，界面问不到结果，
    # 也就无从知道该显示「暂无新版本」还是「有新版本」。
    patch(
        root, j("update/Updates.kt"),
        '''    /** Whether version [a] comes after [b], comparing dotted numbers ("1.10" after "1.9"). */''',
        '''    /**
     * Asks once, now, and waits for the answer. Control Center's Check for Updates row has to say
     * what came of the check, which [checkIfStale] cannot tell it: that one launches and returns.
     * Null means either that no source answered or that this build is already the latest.
     */
    suspend fun checkNow(): String? {
        if (!started) return null
        lastAttempt = System.currentTimeMillis()
        val latest = withContext(Dispatchers.IO) {
            VERSION_URLS.firstNotNullOfOrNull { url ->
                runCatching { read(url).trim().removePrefix("v") }
                    .onFailure { Log.w(TAG, "could not check for a new version at $url", it) }
                    .getOrNull()
                    ?.takeIf { it.matches(Regex("\\\\d+(\\\\.\\\\d+)*")) }
            }
        } ?: return null
        prefs.edit { putString("latest", latest); putLong("checked_at", System.currentTimeMillis()) }
        available = latest.takeIf { newer(it, current) }
        return available
    }

    /** Whether version [a] comes after [b], comparing dotted numbers ("1.10" after "1.9"). */''',
        "Updates 增加可等待的检查",
    )

    # 聚焦底板是浅色surface。Apple 的 label 每套外观各有一份值，上游只搬了深色那套，
    # 于是「白 60%」压在白板上等于没有字。这里补上浅色外观的 secondaryLabel。
    patch(
        root, j("ui/theme/Theme.kt"),
        '''    /** tvOS draws a focused control as a near-white plate with dark content. */
    val Focus = Color(0xFFF5F5F7)
    val OnFocus = Color(0xFF1D1D1F)
}''',
        '''    /** tvOS draws a focused control as a near-white plate with dark content. */
    val Focus = Color(0xFFF5F5F7)
    val OnFocus = Color(0xFF1D1D1F)

    /**
     * The labels above, resolved for the plate. Apple gives every label a value per appearance,
     * and the focused plate is the light one: `secondaryLabel` there is rgb(60,60,67) at 60%, not
     * the white at 60% used on glass. Drawing the dark value on the plate puts white on white.
     */
    val OnFocusSecondary = Color(0x993C3C43)
}''',
        "Palette 补上浅色外观的次要文字色",
    )

    # 菜单行要能变灰：灰色自成一档，聚焦时不跟着变白，否则看起来仍然可以点。
    overlay = j("ui/home/Overlays.kt")
    patch(
        root, overlay,
        """    destructive: Boolean = false,
    trailing: String? = null,
) {""",
        """    destructive: Boolean = false,
    /** Dimmed: the row is showing an answer rather than offering itself (Control Center's check). */
    muted: Boolean = false,
    trailing: String? = null,
) {""",
        "MenuRow 支持变灰",
    )
    patch(
        root, overlay,
        """        val fg = when {
            state.focused -> if (destructive) Color(0xFFD70015) else Palette.OnFocus
            destructive -> Palette.Red
            else -> Palette.Label
        }""",
        """        // The colour is resolved against the surface the row is drawn on, not against the dark
        // appearance the rest of Home uses: on the near-white plate a white-at-60% label reads as
        // nothing at all. Focus fades the plate in over 140 ms, so the text fades with it rather
        // than staying white on a background that is turning light underneath it.
        val fg by animateColorAsState(
            when {
                // An answer, not an offer. Secondary rather than tertiary: tertiary is the
                // placeholder shade and this line is the whole point of the press, so it is read
                // at secondaryLabel's weight on either surface.
                muted -> if (state.focused) Palette.OnFocusSecondary else Palette.SecondaryLabel
                state.focused -> if (destructive) Color(0xFFD70015) else Palette.OnFocus
                destructive -> Palette.Red
                else -> Palette.Label
            },
            tween(140, easing = Motion.EaseOut), label = "row-fg",
        )""",
        "MenuRow 变灰的取色",
    )
    # 右侧说明文字同理，并且跟底板一起过渡。
    patch(
        root, overlay,
        """            if (trailing != null) {
                BasicText(
                    trailing,
                    style = Type.Callout.copy(color = if (state.focused) Palette.OnFocus.copy(alpha = 0.6f) else Palette.SecondaryLabel),
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 200.dp),
                )
            }""",
        """            if (trailing != null) {
                // Same pairing as the label: dark and muted on the plate, light and muted on glass.
                val trailingFg by animateColorAsState(
                    if (state.focused) Palette.OnFocus.copy(alpha = 0.6f) else Palette.SecondaryLabel,
                    tween(140, easing = Motion.EaseOut), label = "row-trailing",
                )
                BasicText(
                    trailing,
                    style = Type.Callout.copy(color = trailingFg),
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 200.dp),
                )
            }""",
        "MenuRow 右侧文字与底板同步过渡",
    )

    center = j("ui/control/ControlCenter.kt")
    patch(
        root, center,
        """        Updates.available?.let { latest ->
            MenuRow(
                Glyph.Info,
                stringResource(R.string.cc_update_available, latest),
                onClick = { vm.overlay = Overlay.ControlCenter(CcPage.Update) },
            )
        }
""",
        """        UpdateRow(vm)
""",
        "更新行改为常驻",
    )
    patch(
        root, center,
        '''/**
 * How to move to the new version: "Download and Install" fetches the APK and opens the system''',
        '''/**
 * The row for a new version, always in the menu. Upstream only showed it when there was something
 * to install, so a build that had just been updated offered no way to ask again. With a new version
 * it names it and opens [CcPage.Update]; without one it offers a check, and a check that comes back
 * empty says "no new version" — dimmed and inert — for five seconds, then offers itself again.
 *
 * The check runs from [LaunchedEffect] rather than from the click so the row can say "checking"
 * while it runs, and so a second press cannot start a second one.
 */
@Composable
private fun UpdateRow(vm: HomeViewModel) {
    val latest = Updates.available
    var checking by remember { mutableStateOf(false) }
    var nothing by remember { mutableStateOf(false) }
    LaunchedEffect(checking) {
        if (!checking) return@LaunchedEffect
        val found = Updates.checkNow()
        checking = false
        // A version found here shows through Updates.available, which this row reads every
        // composition: it turns into the "new version" row by itself.
        if (found == null) nothing = true
    }
    // An answer, not a state: after a moment the row can be asked again.
    LaunchedEffect(nothing) {
        if (!nothing) return@LaunchedEffect
        delay(5_000)
        nothing = false
    }
    // One call site, whichever of the three things the row has to say. Written as an if/else with
    // a MenuRow in each branch, the two branches are two composition groups: the moment a check
    // turns up a version the branch flips, the row that holds the focus is disposed and a fresh
    // one composed, so the focus goes with it and lands on the panel's first button. Varying only
    // the arguments keeps the node, and the focus, where the viewer left it.
    val asking = latest == null && !checking && !nothing
    MenuRow(
        Glyph.Info,
        when {
            latest != null -> stringResource(R.string.cc_update_available, latest)
            checking -> stringResource(R.string.cc_update_checking)
            nothing -> stringResource(R.string.cc_update_none)
            else -> stringResource(R.string.cc_update_check)
        },
        onClick = {
            if (latest != null) {
                vm.overlay = Overlay.ControlCenter(CcPage.Update)
            } else if (asking) {
                checking = true
            }
        },
        // Dimmed only while it has nothing to offer: a check in flight, or the answer just given.
        muted = latest == null && !asking,
    )
}

/**
 * How to move to the new version: "Download and Install" fetches the APK and opens the system''',
        "新增常驻更新行",
    )

    patch(
        root, STRINGS_EN,
        '''    <string name="cc_update_available">New Version: %s</string>''',
        '''    <string name="cc_update_available">New Version: %s</string>
    <string name="cc_update_check">Check for Updates</string>
    <string name="cc_update_checking">Checking…</string>
    <string name="cc_update_none">No New Version</string>''',
        "英文文案：检测更新",
    )
    patch(
        root, STRINGS_ZH,
        '''    <string name="cc_update_available">有新版本 %s</string>''',
        '''    <string name="cc_update_available">有新版本 %s</string>
    <string name="cc_update_check">检测更新</string>
    <string name="cc_update_checking">正在检测…</string>
    <string name="cc_update_none">暂无新版本</string>''',
        "中文文案：检测更新",
    )


# ---------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description="Aerial -> OPPO ColorOS TV 定制")
    parser.add_argument("source", help="上游源码解压后的目录")
    parser.add_argument("--version", default="", help="上游 tag 去掉 v，例如 1.46")
    parser.add_argument(
        "--repo",
        default=os.environ.get("AERIAL_REPO") or os.environ.get("GITHUB_REPOSITORY", ""),
        help="自己仓库的 用户名/仓库名，应用内更新从这里下载",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.source)
    repo = args.repo.strip().strip("/")
    if repo and repo.count("/") != 1:
        die("--repo 要写成 用户名/仓库名，收到的是 %r" % repo)

    print("定制 %s" % root)
    print("版本 %s，更新源 %s\n" % (args.version or "(沿用源码)", repo or "(未指定)"))

    print("[1/5] 预检")
    preflight(root)

    print("\n[2/5] 放 OPPO 专用文件")
    dropped = install_files(root)

    print("\n[3/5] 包名全量改名")
    rename_package(root)

    print("\n[4/5] 定点补丁")
    set_version(root, args.version)
    patch_signature(root)
    patch_manifest(root)
    patch_brightness(root)
    patch_names(root)
    patch_strings(root)
    patch_updates(root, repo)
    patch_update_row(root)

    print("\n[5/5] 终检")
    verify(root, repo, dropped)
    print("\n完成：%s" % root)


if __name__ == "__main__":
    main()
