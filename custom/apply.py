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

# 屏保压暗这次是不是真加了（1.68 之前上游自己压，就不加）。终检只核对加过的那一种。
SAVER_SHADE_ADDED = False


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


def _reindent(text, base, to):
    """把 text 每行的公共缩进 base 换成 to：只动以 base 开头的行，空行和更深的缩进原样。"""
    if base == to:
        return text
    out = []
    for line in text.split("\n"):
        if line.strip() == "":
            out.append(line)
        elif line.startswith(base):
            out.append(to + line[len(base):])
        else:
            out.append(line)
    return "\n".join(out)


def _flexible_match(text, old):
    """只让「首行缩进」浮动，块内部的相对缩进仍然钉死。

    上游整块挪进/挪出一个 if、换一层 Box，缩进就整体变了 —— 这是照抄式锚点最常见的死法
    （1.69 的 WallpaperSheet 就是这样：内容一字没改，每行少 4 个空格）。这里把首行缩进当变量，
    命中之后再把要写进去的代码按同样的位移缩进，内容本身仍然必须逐字对上。

    返回 (start, end, indent)；命中 0 次给 None，命中多次给 False（宁可报错也不猜）。
    """
    core = old[:-1] if old.endswith("\n") else old
    lines = core.split("\n")
    if not lines:
        return None
    base = _indent_of(lines[0])
    parts = [r"(?m)^([ \t]*)"]
    for i, line in enumerate(lines):
        rest = line[len(base):] if line.startswith(base) else line.lstrip()
        if i:
            parts.append(r"[ \t]*\r?\n[ \t]*")
        parts.append(re.escape(rest))
    found = list(re.finditer("".join(parts), text))
    if not found:
        return None
    if len(found) > 1:
        return False
    m = found[0]
    return m.start(), m.end(), m.group(1)


def patch(root, rel, old, new, what):
    """把 old 换成 new；old 必须恰好出现一次，否则记账等最后统一报错。

    先按原文逐字找；找不到再放宽成「缩进可变」找一次（见 _flexible_match）。
    """
    text = read(root, rel)
    if text is None:
        return
    hits = text.count(old)
    if hits == 1:
        write(root, rel, text.replace(old, new, 1))
        print("  ok  %-44s %s" % (short(rel), what))
        return
    if hits == 0:
        found = _flexible_match(text, old)
        if found:
            start, end, indent = found
            base = _indent_of(old.split("\n")[0])
            body = new[:-1] if new.endswith("\n") else new
            body = _reindent(body, base, indent) + ("\n" if old.endswith("\n") else "")
            write(root, rel, text[:start] + body + text[end:])
            print("  ok  %-44s %s  （上游改了缩进，已自适应）" % (short(rel), what))
            return
    why = "上游可能删了或改了这一段" if hits == 0 else "这段在上游出现了 %d 次，锚点不够独特" % hits
    fail(
        "%s\n    要改的地方：%s\n    问题：锚点命中 %d 次（应当 1 次），%s\n    期望原文：\n%s"
        % (rel, what, hits, why, fence(old))
    )


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

    # 背光与关机的固件调用都只能由 tvapisvc 驱动，它按「进程名包含 com.heytap.tv.remote」放行。
    # 整个桌面直接跑在这个进程名里，所以 OppoBacklight / OppoPower 都在本进程内反射调用，
    # 不需要额外的 Service。
    patch(
        root, MANIFEST,
        '    <application>',
        '    <!-- 进程名带 :com.heytap.tv.remote，固件的 tvapisvc 才会接受本应用的背光 / 关机调用。\n'
        '         主进程名不含白名单串，所以让进程名本身带上它。 -->\n'
        '    <application android:process=":com.heytap.tv.remote">',
        "主进程挂白名单进程名",
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
            "    // 5% 一档，和设置页那条一致；范围 0..100，跟系统那条对齐（0 = 最低）。",
            "    val from = OppoBacklight.level(context)",
            "    val next = (if (direction > 0) (from / 5 + 1) * 5 else ((from + 4) / 5 - 1) * 5).coerceIn(0, 100)",
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

    # 上游在 BrightnessRow 里那段轮询（`LaunchedEffect(xgimi) { … delay(700) … }`）**保持原样不动**：
    # 它是给极米开的（极米亮度在 HAL 里，没有任何 Settings 键可盯，只能轮询），OPPO 用不上。
    # OPPO 走的是 OppoBacklight 里那个常驻 ContentObserver，零成本、且控制中心没开着时也在同步。
    #
    # 实测过另一条路：电视那个「快捷亮度条」（ScreenLightAdjustDialog）改亮度时**既不写任何
    # Settings 键、也不发广播**，只走 PictureManagerImpl 直连面板。所以观察者对它无效；
    # 想让 Aerial 那条实时跟随，只能轮询固件（一次查询约 2~10ms）。当前选择：**不做**。
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

    # 打开控制中心那一下，亮度直接问固件要真值（几毫秒），不等异步回读。
    # 上游这条路径读的是「记着的值」，我们的记账值只有在 Aerial 自己调过时才准 —— 用电视自己的
    # 亮度条调过之后再打开控制中心，会先显示旧值、一两秒后才被校正回来。
    patch(
        root, vm,
        "            val (home, level) = withContext(Dispatchers.Default) { SystemProbe.homeStatus(ctx) to Brightness.level(ctx) }\n",
        "            // OPPO 的亮度只有固件知道当前值：面板打开这一下同步问一次（阻塞几毫秒，\n"
        "            // 本来就在 Default 线程上），否则先显示记账值、再等异步回读纠正，看着像慢半拍。\n"
        "            val (home, level) = withContext(Dispatchers.Default) {\n"
        "                val now = if (OppoBacklight.usable(ctx)) OppoBacklight.levelNow(ctx) / 100f else Brightness.level(ctx)\n"
        "                SystemProbe.homeStatus(ctx) to now\n"
        "            }\n",
        "打开面板时同步读一次面板亮度",
    )

    # 上游是「先等 3 秒再刷一次」：面板刚打开时显示的其实是上一次的缓存。先刷再等，
    # 打开瞬间就对齐（这条对所有机型都成立，只是我们的亮度非同步不可）。
    patch(
        root, j("MainActivity.kt"),
        "                    while (open) {\n"
        "                        delay(PANEL_REFRESH_MS)\n"
        "                        vm.refreshPanel()\n"
        "                    }\n",
        "                    // Look once before waiting: the panel is drawn from cached values, and\n"
        "                    // the first look used to be 3 s later (a stale brightness until then).\n"
        "                    while (open) {\n"
        "                        vm.refreshPanel()\n"
        "                        delay(PANEL_REFRESH_MS)\n"
        "                    }\n",
        "控制中心一打开就先刷新一次",
    )


def patch_screensaver(root):
    """个性化里的「进入时间」直接读写系统的屏保时间，另加 1 分钟档、文案压成「N分」。

    背景：1.62 起系统的屏保可以就是 Aerial 自己（ScreensaverMode.System），但延时那一行只把
    系统值显示出来，改不了。这台 OPPO（OPPOCNM632）系统的屏保时间就存在
    Settings.System.SCREEN_OFF_TIMEOUT（实测 120000），Aerial 的 WRITE_SETTINGS 已被放行
    （appops allow），所以两边可以直接互通：打开设置页时读一次，改的时候写回去。
    """
    # 1) 系统屏保的读 / 写：读用的就是 firmwareDelayMs 那个键，写也写同一个键。
    insert_in_body(
        root, j("system/SystemScreensaver.kt"),
        r"^object SystemScreensaver \{\n",
        [
            "",
            "/** The delay in minutes the TV's own screensaver waits; null where unknown, 0 when off. */",
            "fun delayMinutes(context: Context): Int? {",
            "    val ms = firmwareDelayMs(context) ?: return null",
            "    if (ms <= 0) return 0",
            "    return ((ms + 30_000) / 60_000).toInt().coerceAtLeast(1)",
            "}",
            "",
            "/**",
            " * Writes the setting the firmware starts its screensaver from, so Aerial's row and the TV's",
            " * own screensaver page agree. False where WRITE_SETTINGS was not granted, or the firmware",
            " * has no such key. \"Never\" (0) never gets here: AerialSettings turns the system screensaver",
            " * off through GuardSetup before this is called, so there is nothing to write.",
            " */",
            "fun setDelayMinutes(context: Context, minutes: Int): Boolean {",
            "    if (minutes <= 0) return true",
            "    val xiaomi = runCatching { context.packageManager.getPackageInfo(XIAOMI, 0) }.isSuccess",
            "    val key = if (xiaomi) \"screen_saver_timeout\" else Settings.System.SCREEN_OFF_TIMEOUT",
            "    return runCatching { Settings.System.putLong(context.contentResolver, key, minutes * 60_000L) }",
            "        .onFailure { Log.w(TAG, \"could not set the screensaver delay\", it) }",
            "        .isSuccess",
            "}",
        ],
        "屏保延时：读写系统设置",
        "fun setDelayMinutes(context: Context",
        at="end",
    )

    # 2) 档位：多一个 1 分钟；列表要能带上系统里那种「不在档位表里」的值。
    settings = j("settings/AerialSettings.kt")
    patch(
        root, settings,
        "    val screensaverChoices = listOf(2, 5, 10, 20, NEVER)",
        "    // 这台电视的系统屏保支持 1 分钟，Aerial 原版只有 2 起。\n"
        "    val screensaverChoices = listOf(1, 2, 5, 10, 20, NEVER)",
        "档位加 1 分钟",
    )
    ensure_import(
        root, settings,
        "import %s.system.SystemScreensaver" % PKG,
        "AerialSettings 引用 SystemScreensaver",
    )
    insert_in_body(
        root, settings,
        r"^object AerialSettings \{\n",
        [
            "/** The choices, plus the TV's own value when it is not one of them, so the row never lies. */",
            "val screensaverChoicesNow: List<Int>",
            "    get() = screensaverMinutes.takeIf { it != NEVER && it !in screensaverChoices }",
            "        ?.let { (screensaverChoices - NEVER + it).sorted() + NEVER }",
            "        ?: screensaverChoices",
            "",
            "/**",
            " * Reads the delay from the TV's own screensaver setting, so this row shows what the system's",
            " * page shows. Run each time the settings sheet opens: the TV can change it on its own.",
            " *",
            " * 读一次要跨进程问三次（PackageManager + SettingsProvider 的两个键），几十毫秒；调用点正好在",
            " * 抽屉滑入的那一帧上，放主线程会卡一下，所以整段读到 IO 上去，回来再写状态。",
            " */",
            "suspend fun syncScreensaverFromSystem(context: Context) {",
            "    if (!started) return",
            "    val minutes = withContext(Dispatchers.IO) { SystemScreensaver.delayMinutes(context) } ?: return",
            "    if (minutes == screensaverMinutes) return",
            "    screensaverMinutes = minutes",
            "    app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putInt(\"screensaver_minutes\", minutes) }",
            "}",
        ],
        "屏保时间跟随系统（读 + 档位表）",
        "fun syncScreensaverFromSystem(context: Context)",
        at="end",
    )
    # 上面那个 suspend 要用的调度器；上游这个文件里一条协程 import 都没有。
    ensure_import(
        root, settings,
        "import kotlinx.coroutines.Dispatchers",
        "AerialSettings 引 Dispatchers",
    )
    ensure_import(
        root, settings,
        "import kotlinx.coroutines.withContext",
        "AerialSettings 引 withContext",
    )

    # 4) 真正该同步的地方：盯住系统那个键，而不是等打开设置页。
    # screensaverMinutes 不只是给那一行显示用的 —— MainActivity.restartIdleTimer() 每次按键
    # 都拿它算屏保倒计时（idleBeforeScreensaver），旁边读系统值的两处上游本来就是实时读的。
    # 上游自己在 HomeGuardService 里就用 ContentObserver 盯 screensaver_components / adb_enabled，
    # 写法照抄：主线程 Handler + runCatching 注册。回调里仍然走 IO 读，不把几十毫秒搬回主线程。
    insert_in_body(
        root, settings,
        r"^object AerialSettings \{\n",
        [
            "",
            "/**",
            " * The TV's own screensaver page writes the same key Aerial reads, and it is the only",
            " * other writer: watch it, so the delay the idle timer counts on is the one the TV shows.",
            " * The row in Personalize is not the only place the value is used (restartIdleTimer).",
            " */",
            "private val watchScope = MainScope()",
            "private val screensaverObserver by lazy {",
            "    object : ContentObserver(Handler(Looper.getMainLooper())) {",
            "        override fun onChange(selfChange: Boolean) {",
            "            // 我们自己改档位时也会写系统，回调跟着来一次；值相同就在 sync 里 return，不会打乒乓。",
            "            watchScope.launch { syncScreensaverFromSystem(app) }",
            "        }",
            "    }",
            "}",
            "",
            "/** Once, from init(): the firmware's own page is the only other writer of these two keys. */",
            "private fun watchScreensaverDelay() {",
            "    val r = app.contentResolver",
            "    runCatching { r.registerContentObserver(Settings.System.getUriFor(Settings.System.SCREEN_OFF_TIMEOUT), false, screensaverObserver) }",
            "    runCatching { r.registerContentObserver(Settings.System.getUriFor(\"screen_saver_timeout\"), false, screensaverObserver) }",
            "}",
        ],
        "常驻观察者盯住系统屏保延时",
        "private fun watchScreensaverDelay()",
        at="end",
    )
    ensure_import(
        root, settings,
        "import android.database.ContentObserver",
        "AerialSettings 引 ContentObserver",
    )
    ensure_import(
        root, settings,
        "import android.os.Handler",
        "AerialSettings 引 Handler",
    )
    ensure_import(
        root, settings,
        "import android.os.Looper",
        "AerialSettings 引 Looper",
    )
    ensure_import(
        root, settings,
        "import kotlinx.coroutines.MainScope",
        "AerialSettings 引 MainScope",
    )
    ensure_import(
        root, settings,
        "import kotlinx.coroutines.launch",
        "AerialSettings 引 launch",
    )
    # 注册点：init() 里，那里本来就有 started 防重入，只跑一次。
    insert_in_body(
        root, settings,
        r"^[ \t]*fun init\(context: Context\) \{\n",
        [
            "// 屏保延时跟着系统走：注册一次观察，之后系统那边一改就同步（不只是打开设置页那一次）。",
            "watchScreensaverDelay()",
        ],
        "init 里挂上系统屏保观察",
        # 标记不能用函数名：上面那段定义里就含 watchScreensaverDelay() 这个串，会被判成「已存在」。
        "// 屏保延时跟着系统走",
        at="end",
    )
    insert_in_body(
        root, settings,
        r"^[ \t]*fun chooseScreensaverMinutes\(minutes: Int\) \{\n",
        [
            "// 双向：电视自己的屏保页读的是同一个键，改这边那边跟着变。",
            "SystemScreensaver.setDelayMinutes(app, minutes)",
        ],
        "改档位时写回系统",
        "SystemScreensaver.setDelayMinutes(app, minutes)",
        at="end",
    )

    # 3) 设置页：打开时读一次系统值；延时那一行改成可选档位（原来 System 模式只显示、不能改）。
    sheet = j("ui/wallpaper/WallpaperSheet.kt")
    patch(
        root, sheet,
        "        if (!visible) return@LaunchedEffect\n",
        "        if (!visible) return@LaunchedEffect\n"
        "        // 系统那边的屏保时间随时可能被改：每次打开都重新读一遍（函数内部走 IO，不挡滑入动画）。\n"
        "        AerialSettings.syncScreensaverFromSystem(context)\n",
        "打开设置页时刷新屏保时间",
    )
    # 锚点里不带上游的注释行：1.69 只删了这两行注释就整条失配，而代码一字未改。
    patch(
        root, sheet,
        '                        if (Wallpapers.screensaver == ScreensaverMode.System) SettingRow(R.string.ss_delay, null, note) {\n'
        '                            val delay = remember { SystemScreensaver.firmwareDelayMs(context) }\n'
        '                            BasicText(\n'
        '                                when {\n'
        '                                    delay == null -> stringResource(R.string.ss_delay_system)\n'
        '                                    delay == 0L -> stringResource(R.string.ss_delay_system_off)\n'
        '                                    else -> stringResource(R.string.ss_delay_system_minutes, ((delay + 30_000) / 60_000).coerceAtLeast(1).toInt())\n'
        '                                },\n'
        '                                style = Type.Body.copy(color = Palette.SecondaryLabel),\n'
        '                                modifier = Modifier.padding(end = 8.dp),\n'
        '                            )\n'
        '                        } else Choices(\n'
        '                            R.string.ss_delay, null, note,\n'
        '                            AerialSettings.screensaverChoices.map {',
        '                        // One setting, and it is the TV\'s own: the same key the system\'s screensaver\n'
        '                        // page writes, so choosing here changes the TV and the other way round.\n'
        '                        Choices(\n'
        '                            R.string.ss_delay, null, note,\n'
        '                            AerialSettings.screensaverChoicesNow.map {',
        "屏保时间改成可选档位（含系统值）",
    )

    # 4) 文案：档位压成「1分 / 2分 / …」，菜单一行放得下。
    patch(
        root, STRINGS_ZH,
        '    <string name="ss_minutes">%d 分钟</string>',
        '    <string name="ss_minutes">%d分</string>',
        "中文文案：档位写成分",
    )


def patch_saver_shade(root, version):
    """把屏保画面的「压暗」补回来。

    1.68 起上游不再给影片压暗 —— release notes 原话是「航拍壁纸、我的视频壁纸和航拍屏保不再压暗，
    画面更亮」；1.69 也一样：HomeChrome 里那层 Images.SHADE 现在只在 shadeMoving（输入源的画面）
    时才画，AerialSaver 自己也不画。这台电视的屏保就是航拍影片，少了这层，夜里看着发白。

    只补屏保这一处（Home 保持上游新的亮法）：在影片之上、黑色遮罩之下画一层 Images.homeShade，
    也就是 1.67 屏保上原本那一层（渐变参数一个不改），所以观感就是「原来那种」。

    三道判断：
      1) 1.68 之前的版本上游本来就压暗，跳过（不然屏保会被压两遍，反而偏黑）；
      2) 上游若自己又开始给屏保压暗（Screensaver.kt 里出现 Images.homeShade 的**代码**引用，
         注释里提到不算），跳过；
      3) 这段之前已经加过，跳过。
    """
    saver = j("ui/home/Screensaver.kt")
    try:
        floor = tuple(int(x) for x in version.split(".")[:2])
    except ValueError:
        floor = (0, 0)
    if floor < (1, 68):
        print("  ok  %-44s %s" % (short(saver), "屏保压暗（1.68 之前上游本来就压，跳过）"))
        return
    text = read(root, saver)
    if text is None:
        return
    if "OWNER: the film's shade" in text:
        print("  ok  %-44s %s" % (short(saver), "屏保压暗（已加过，跳过）"))
        return
    if re.search(r"(?m)^(?![ \t]*(?://|\*|/\*))[^\n]*Images\.homeShade", text):
        print("  ok  %-44s %s" % (short(saver), "屏保压暗（上游自己做了，跳过）"))
        return

    ensure_import(root, saver, "import %s.wallpaper.Images" % PKG, "屏保压暗 引用 Images")

    patch(
        root, saver,
        "    Box(Modifier.fillMaxSize().graphicsLayer { alpha = veil }.background(Color.Black))\n",
        "    // OWNER: the film's shade, as up to 1.67. Upstream stopped shading films in 1.68 and the\n"
        "    // aerials read washed out here, mostly at night. Only the screensaver gets it back: Home\n"
        "    // keeps upstream's brighter look. The same layer and the same values as 1.67's screensaver.\n"
        "    val saverShade = remember { Images.homeShade.asImageBitmap() }\n"
        "    Canvas(Modifier.fillMaxSize()) {\n"
        "        drawImage(\n"
        "            saverShade,\n"
        "            dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt()),\n"
        "            filterQuality = FilterQuality.Low,\n"
        "        )\n"
        "    }\n"
        "    Box(Modifier.fillMaxSize().graphicsLayer { alpha = veil }.background(Color.Black))\n",
        "屏保画面压暗（补回 1.67）",
    )
    global SAVER_SHADE_ADDED
    SAVER_SHADE_ADDED = True


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
        "        //\n"
        "        // `lift` is read only inside graphicsLayer below, never here: that is the draw phase,\n"
        "        // so the focus animation does not recompose the tile on every one of its frames. Read\n"
        "        // in this condition it would (upstream keeps it out of composition for the same reason).\n"
        "        if (plateName != null && plateName != PlateNames.Off) {\n"
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
    # 插在 focusedPx **之后**，而不是它的签名前面：那样会把它自己的 KDoc 和函数拆开，
    # 注释挂到我们新加的函数头上。锚点用那四行函数体（纯代码，不带注释）。
    patch(
        root, tile,
        "private fun focusedPx(px: Float): Int {\n"
        "    val r = px.roundToInt()\n"
        "    return r + 2 * (r * (FOCUSED_SCALE - 1f) / 2f).roundToInt()\n"
        "}\n",
        "private fun focusedPx(px: Float): Int {\n"
        "    val r = px.roundToInt()\n"
        "    return r + 2 * (r * (FOCUSED_SCALE - 1f) / 2f).roundToInt()\n"
        "}\n"
        "\n"
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
        "}\n",
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
    # 一旦焦点下到网格（抽屉），第一排就只是网格里的普通一行：名字回到图标下方，和第二排及以下
    # 完全同一套规则（显示全部还是仅焦点，由 showAllNames 决定）。
    # 做法是算一个 onPlateRow，下面三个参数都只看它 —— 一个判断，不会出现改了一处漏了另一处。
    #
    # 「一排有几个」上游换过写法：1.65 起每行 4/5/6 可选，常量 COLUMNS 换成了变量 columns
    # （GridMetrics 也多了一个 columns 参数）。这里跟着上游用哪个就用哪个，两个版本都能编译；
    # 锚点里的标识符也因此不能写死。
    # 这一组是唯一被上游顶开过三次的：1.57 的 import 段、1.65 的 COLUMNS→columns、1.67 的
    # `columns * metrics.shelfRows`。共同点都是**照抄了上游的表达式**。所以改成：
    #   1) 按参数名找行（`showName =` / `nameOnPlate =`），不看右边写成什么样；
    #   2) 「这一格在托盘上」的判据**原样借用上游自己写在 nameOnPlate 里的表达式**，
    #      上游加多少项（shelfRows、twoRow、将来的新条件）都自动跟得上。
    home_text = read(root, home) or ""
    arg = re.compile(r"(?m)^([ \t]*)nameOnPlate = (.+?),\r?$")
    found = arg.search(home_text)
    if found is None:
        fail(
            "%s\n    要改的地方：定义「台面第一排」判据\n"
            "    问题：找不到 `nameOnPlate = …,` 这一行（上游可能改了参数名或挪了位置）。\n"
            "    这一组补丁靠它推出「哪一格在玻璃托盘上」，没有它就做不出抽屉里的差异。"
            % home
        )
        return
    indent, plate_cond = found.group(1), found.group(2).strip()

    if "val onPlateRow" not in home_text:
        # 插在 AppTile( 那一行前面，缩进跟着它走
        m = re.search(r"(?m)^([ \t]*)AppTile\($", home_text)
        if m is None:
            fail("%s\n    要改的地方：定义「台面第一排」判据\n    问题：找不到独立的 `AppTile(` 行" % home)
            return
        at = m.start()
        lip = m.group(1)
        line = "%s// On the shelf the top row is the plate's row; down in the grid it is just a row.\n" % lip
        line += "%sval onPlateRow = !inGrid && %s\n" % (lip, plate_cond)
        home_text = home_text[:at] + line + home_text[at:]
        print("  ok  %-44s %s" % (short(home), "定义「台面第一排」判据"))

    # 名字改画在图标里：showName 由 onPlateRow 决定；nameOnPlate 整行删掉（走默认值 false），
    # 否则抽屉里的名字会被焦点环额外顶下去一截。
    show = re.compile(r"(?m)^[ \t]*showName = .+?,\r?$")
    if len(show.findall(home_text)) != 1:
        fail(
            "%s\n    要改的地方：第一排改用 plateName\n"
            "    问题：`showName = …,` 命中了 %d 次（应当 1 次）"
            % (home, len(show.findall(home_text)))
        )
        return
    home_text = show.sub("%sshowName = !onPlateRow," % indent, home_text, count=1)
    home_text = arg.sub(
        "%s// The plate's row draws its names inside the tiles; every other row, including that\n"
        "%s// same row while the grid is open, keeps the original name below the tile. nameOnPlate\n"
        "%s// stays at its default of false on purpose.\n"
        "%splateName = if (onPlateRow) AerialSettings.plateNames else null," % (indent, indent, indent, indent),
        home_text,
        count=1,
    )
    write(root, home, home_text)
    print("  ok  %-44s %s" % (short(home), "第一排改用 plateName"))

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


def patch_power_menu(root):
    """电源菜单里的「息屏」是快捷待机（QUICK，黑屏但 Android 不睡）；改成真关机（DEPTH）。

    为什么不直接改选单：那个 Dialog 在 `com.android.systemui` 里，我们动不了。所以改成**接管**——
    菜单一显示就上膛，紧随其后的 QUICK 待机被改写成 DEPTH。判据足够窄：
    菜单里选「重启」根本不进 QUICK，自动待机超时也没有菜单在前台，两者都不会被误伤。

    命令映射是实测的（OPPOCNM632，Android 9）：`disPatchCommand("ModulePower", 4, ["QUICK"])`
    → `doSetStandbyMode QUICK`；参数换成 `"DEPTH"` 就是真关机（SystemUI 的字符串写着
    `setpowermode DEPTH, do not need to do something to wifi`）。
    """
    guard = j("guard/HomeGuardService.kt")
    ensure_import(root, guard, "import %s.system.OppoPower" % PKG, "HomeGuardService 引用 OppoPower")

    # 电源菜单是个 Dialog，而 lastWindow 只记 Activity（上游注释写明了），所以自己记一笔。
    patch(
        root, guard,
        "        val pkg = event.packageName?.toString() ?: return\n"
        "        val cls = event.className?.toString() ?: return\n",
        "        val pkg = event.packageName?.toString() ?: return\n"
        "        val cls = event.className?.toString() ?: return\n"
        "        // The power menu is a dialog, not an activity, so lastWindow below ignores it. Its\n"
        "        // appearance arms the rewrite instead.\n"
        "        if (cls.contains(\"ActionsDialog\") || cls.contains(\"GlobalActions\")) {\n"
        "            Log.i(TAG, \"power menu shown; arming the shutdown rewrite\")\n"
        "            OppoPower.arm()\n"
        "            updateKeyFilter()\n"
        "            handler.removeCallbacks(powerMenuGone)\n"
        "            handler.postDelayed(powerMenuGone, OppoPower.WATCH_MS)\n"
        "        }\n",
        "电源菜单出现时上膛",
    )

    # 判据用**按键**，不用窗口焦点：实测两条路的焦点都在 QUICK 之后约 30ms 才回来，分不开。
    # 菜单里按方向键 / OK 会产生按键事件（本服务开了按键过滤），按电源不会（它在
    # PhoneWindowManager 就被吃掉了）。所以这里出现按键 = 用户自己选的「息屏」，保持息屏。
    patch(
        root, guard,
        "    override fun onKeyEvent(event: KeyEvent): Boolean {\n"
        "        if (!GuardSetup.isUserUnlocked(this)) return false\n",
        "    override fun onKeyEvent(event: KeyEvent): Boolean {\n"
        "        if (!GuardSetup.isUserUnlocked(this)) return false\n"
        "        if (event.action == KeyEvent.ACTION_DOWN && event.keyCode !in VOLUME_KEYS && OppoPower.armed()) {\n"
        "            Log.i(TAG, \"key \" + event.keyCode + \" while the power menu was up; leaving the screen-off alone\")\n"
        "            OppoPower.cancel()\n"
        "            // Volume is skipped on purpose: it neither dismisses the menu nor picks a row, so\n"
        "            // pressing it must not cancel the rewrite (else volume, then power = screen off).\n"
        "            // Never touch the filter from inside this callback: reconfiguring it while the key\n"
        "            // is being dispatched swallows that very event, and the menu then ignores the first\n"
        "            // press. Hand it to the looper instead, so this event finishes its round trip.\n"
        "            handler.removeCallbacks(powerMenuGone)\n"
        "            handler.postDelayed(powerMenuGone, 300)\n"
        "        }\n",
        "菜单里按键就取消改写",
    )

    # 按键过滤默认是关的：上游只在「需要拦 Home」或「CEC」时才开，而 Aerial 就是默认桌面，
    # 于是 homeWanted = false、一个键都收不到。但电源菜单那 12 秒里我们必须收到方向键 / OK ——
    # 那是唯一能区分「按 OK 选息屏」和「按电源」的信号。所以只在那 12 秒里把过滤打开。
    patch(
        root, guard,
        "    private var filteringForCecOnly = false\n",
        "    private var filteringForCecOnly = false\n"
        "\n"
        "    /** 这次开过滤只是为了看电源菜单里的按键（不是 CEC）。音量键这时不该被接管。 */\n"
        "    private var filteringForMenu = false\n",
        "记录「只为菜单开过滤」",
    )

    patch(
        root, guard,
        "        val homeWanted = wanted && !isDefaultHome()\n"
        "        val cecWanted = !screenOff && CecHome.active(this, lastWindow?.first)\n"
        "        filteringForCecOnly = cecWanted && !homeWanted\n"
        "        var want = if (homeWanted || cecWanted) info.flags or flag else info.flags and flag.inv()\n",
        "        val homeWanted = wanted && !isDefaultHome()\n"
        "        val cecWanted = !screenOff && CecHome.active(this, lastWindow?.first)\n"
        "        // The power menu is up: take keys for a moment. Direction keys and OK land here and\n"
        "        // tell the menu's own \"screen off\" (keep it) apart from a second press of the power\n"
        "        // key (rewritten into a shutdown). Off again once the menu is gone.\n"
        "        val menuWanted = !screenOff && OppoPower.armed()\n"
        "        filteringForMenu = menuWanted\n"
        "        filteringForCecOnly = cecWanted && !homeWanted && !menuWanted\n"
        "        var want = if (homeWanted || cecWanted || menuWanted) info.flags or flag else info.flags and flag.inv()\n",
        "菜单弹着时开按键过滤",
    )

    # 过滤只因为菜单才开着的时候，音量键不该被接管：这台电视平时从不开过滤，
    # takeVolumeKey() 那条路根本没被走过，没必要为了 12 秒的菜单把它拉进来看。
    patch(
        root, guard,
        "        if (event.keyCode in VOLUME_KEYS) return if (filteringForCecOnly) false else takeVolumeKey(event)\n",
        "        // Only the power menu wanted keys: volume goes straight to the system, the way it\n"
        "        // does when the guard filters nothing at all.\n"
        "        if (event.keyCode in VOLUME_KEYS) {\n"
        "            return if (filteringForCecOnly || filteringForMenu) false else takeVolumeKey(event)\n"
        "        }\n",
        "菜单期间音量键放行",
    )

    # 插在 cecInputObserver **之后**：它上面有一句讲 CecHome 的注释，插在前面会让那句注释
    # 悬空指着我们新加的字段。锚点取它完整的三行（纯代码）。
    patch(
        root, guard,
        "    private val cecInputObserver = object : ContentObserver(handler) {\n"
        "        override fun onChange(selfChange: Boolean) = updateKeyFilter()\n"
        "    }\n",
        "    private val cecInputObserver = object : ContentObserver(handler) {\n"
        "        override fun onChange(selfChange: Boolean) = updateKeyFilter()\n"
        "    }\n"
        "\n"
        "    /** 电源菜单那 12 秒过去后，把按键过滤关回去。 */\n"
        "    private val powerMenuGone = Runnable { updateKeyFilter() }\n"
        "\n"
        "    /**\n"
        "     * The power menu's \"screen off\" is a QUICK standby: black screen, Android still awake.\n"
        "     * It is rewritten into a real shutdown here. The menu itself lives in SystemUI and cannot\n"
        "     * be changed, but the standby mode it sets is observable. Only a QUICK that follows the\n"
        "     * menu is rewritten: choosing \"restart\" never enters QUICK, and the auto-standby timeout\n"
        "     * has no menu in front, so neither is touched.\n"
        "     */\n"
        "    private val powerModeObserver = object : ContentObserver(handler) {\n"
        "        override fun onChange(selfChange: Boolean) {\n"
        "            val mode = runCatching {\n"
        "                Settings.Global.getString(contentResolver, \"tv_power_mode\")\n"
        "            }.getOrNull()\n"
        "            if (mode != \"QUICK\" || !OppoPower.armed()) return\n"
        "            Log.i(TAG, \"QUICK standby right after the power menu; turning it into a real shutdown\")\n"
        "            OppoPower.shutdown()\n"
        "        }\n"
        "    }\n"
        "\n",
        "菜单息屏改写成真关机",
    )

    patch(
        root, guard,
        '        contentResolver.registerContentObserver(Settings.Secure.getUriFor("screensaver_components"), false, screensaverObserver)\n',
        '        contentResolver.registerContentObserver(Settings.Secure.getUriFor("screensaver_components"), false, screensaverObserver)\n'
        '        contentResolver.registerContentObserver(Settings.Global.getUriFor("tv_power_mode"), false, powerModeObserver)\n',
        "挂上 tv_power_mode 观察",
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
        "    )\n"
        "\n"
        "    /**\n"
        "     * The version the first source that answers with one reports; null when none did. A mirror\n"
        "     * can answer with an error page rather than fail outright, so a reply that is not a version\n"
        "     * number moves on to the next source. Blocking: both callers already run it on IO, and this\n"
        "     * is the only place the list is read.\n"
        "     */\n"
        "    private fun fetchLatest(): String? = VERSION_URLS.firstNotNullOfOrNull { url ->\n"
        "        runCatching { read(url).trim().removePrefix(\"v\") }\n"
        '            .onFailure { Log.w(TAG, "could not check for a new version at $url", it) }\n'
        "            .getOrNull()\n"
        '            ?.takeIf { it.matches(Regex("\\\\d+(\\\\.\\\\d+)*")) }\n'
        "    }" % (repo, repo, repo),
        "版本检查：三级国内加速",
    )
    patch(
        root, updates,
        '                runCatching { read(VERSION_URL).trim().removePrefix("v") }\n'
        '                    .onFailure { Log.w(TAG, "could not check for a new version", it) }\n'
        '                    .getOrNull()\n'
        '                    ?.takeIf { it.matches(Regex("\\\\d+(\\\\.\\\\d+)*")) }',
        "                fetchLatest()",
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
        val latest = withContext(Dispatchers.IO) { fetchLatest() } ?: return null
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
            (
                "vm.overlay = Overlay.None\n                SystemProbe.openPictureSettings(context)",
            ),
        ),
        (
            j("HomeViewModel.kt"),
            (
                "import %s.system.OppoBacklight" % PKG,
                "OppoBacklight.levelNow(ctx)",
                "OppoBacklight.onLevel = { percent -> brightness = percent / 100f }",
                "OppoBacklight.onLevel = null",
            ),
        ),
        # 屏保时间那一组：读写系统设置、档位加 1 分钟、设置页打开时刷新。
        (
            j("system/SystemScreensaver.kt"),
            ("fun delayMinutes(context: Context)", "Settings.System.putLong("),
        ),
        (
            j("settings/AerialSettings.kt"),
            ("listOf(1, 2, 5, 10, 20, NEVER)", "screensaverChoicesNow",
             "fun syncScreensaverFromSystem(", "SystemScreensaver.setDelayMinutes(app, minutes)",
             "private fun watchScreensaverDelay()", "registerContentObserver(",
             "watchScreensaverDelay()"),
        ),
        (
            j("ui/wallpaper/WallpaperSheet.kt"),
            ("AerialSettings.syncScreensaverFromSystem(context)", "AerialSettings.screensaverChoicesNow.map"),
        ),
        # 名字画在图标里要靠 PI / cos / sin：上游自己引的（PI、cos）和本脚本补的（sin）
        # 必须都在，否则是 Kotlin 编译期才报错，比这里报得晚也更难懂。
        (
            j("ui/home/AppTile.kt"),
            ("PlateTileName(", "plateName: PlateNames?", "import kotlin.math.PI",
             "import kotlin.math.cos", "import kotlin.math.sin"),
        ),
        # 认前缀、不认 COLUMNS/columns：1.65 起每行个数是变量 columns，之前是常量 COLUMNS。
        (j("ui/home/HomeScreen.kt"), ("AerialSettings.plateNames", "val onPlateRow = !inGrid && ", "showName = !onPlateRow,")),
        # 电源菜单那组：菜单一显示就上膛，随后的 QUICK 待机被改写成真关机。
        (
            j("guard/HomeGuardService.kt"),
            ("OppoPower.arm()", "powerModeObserver", 'Settings.Global.getUriFor("tv_power_mode")'),
        ),
        (j("system/OppoPower.kt"), ("setStandbyMode", "CMD_STANDBY_MODE", "fun cancel()", 'TvApi.dispatch("ModulePower"')),
        # 固件反射收在一处：背光和待机都从 TvApi 走，缺了它两个功能一起失效。
        (
            j("system/TvApi.kt"),
            ("fun dispatch(", "fun query(", "tvapisvc", "com.oneplus.tvapi.ITvApi"),
        ),
        (j("system/OppoBacklight.kt"), ('TvApi.dispatch("ModulePicture"', 'TvApi.query("ModulePicture"')),
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

    # 屏保压暗只核对「这次真加了」的那一版（1.68 起）；更早的版本上游自己压，不加也不核对。
    if SAVER_SHADE_ADDED:
        text = read(root, j("ui/home/Screensaver.kt")) or ""
        for needle in ("Images.homeShade", "val saverShade"):
            if needle not in text:
                fail("%s 里缺 %s：屏保压暗没落地" % (j("ui/home/Screensaver.kt"), needle))

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
# ---------------------------------------------------------------- main


def lint_anchors():
    """只看锚点有多「脆」，不跑补丁、不写文件（`apply.py --lint`）。

    反复被顶开的三类原因，前两类已经能从机制上免疫：
      1) 整块重新缩进        → patch() 现在会自适应（_flexible_match）
      2) import / 函数体 / 参数 → 走 insert_in_body / set_call_arg / ensure_import
      3) 锚点里抄了上游的注释 → 上游随手删两行注释就失配（1.69 的 ss_delay 就是这样）
    这里就把第 3 类全部列出来，好一次性清掉。
    """
    import ast

    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "id", "") != "patch":
            continue
        if len(node.args) < 4:
            continue
        try:
            rel = ast.literal_eval(node.args[1])
            old = ast.literal_eval(node.args[2])
            what = ast.literal_eval(node.args[3])
        except (ValueError, SyntaxError):
            continue
        if not isinstance(old, str) or not isinstance(what, str):
            continue
        comment = [l for l in old.split("\n") if l.strip().startswith(("//", "*", "/*"))]
        if comment:
            rows.append((node.lineno, short(rel), what, len(comment), comment[0].strip()))

    print("锚点里抄了上游注释的：%d 处（上游删/改注释就失配）\n" % len(rows))
    for lineno, rel, what, n, sample in rows:
        print("  L%-5d %-34s %-30s %d 行  %s" % (lineno, rel, what[:28], n, sample[:52]))
    print("\n缩进这一类的已经免疫：patch() 命中不到时会放宽首行缩进再找一次。")


def main():
    parser = argparse.ArgumentParser(description="Aerial -> OPPO ColorOS TV 定制")
    parser.add_argument("source", nargs="?", default="", help="上游源码解压后的目录")
    parser.add_argument("--version", default="", help="上游 tag 去掉 v，例如 1.46")
    parser.add_argument(
        "--lint",
        action="store_true",
        help="只检查锚点有多脆，不跑补丁、不写文件",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("AERIAL_REPO") or os.environ.get("GITHUB_REPOSITORY", ""),
        help="自己仓库的 用户名/仓库名，应用内更新从这里下载",
    )
    args = parser.parse_args()

    if args.lint:
        lint_anchors()
        return
    if not args.source:
        parser.error("要给上游源码目录（只想检查锚点就用 --lint）")

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
    patch_screensaver(root)
    patch_saver_shade(root, args.version)
    patch_names(root)
    patch_power_menu(root)
    patch_strings(root)
    patch_updates(root, repo)
    patch_update_row(root)

    print("\n[5/5] 终检")
    verify(root, repo, dropped)
    print("\n完成：%s" % root)


if __name__ == "__main__":
    main()
