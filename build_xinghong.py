"""星虹构建入口：版本号与产品名单源化。

单源规则（只改这一处，在 ux_core.py）：
  · 版本号    -> APP_VERSION
  · 产品名    -> PRODUCT_NAME（R252 起标题不带副标题）

本脚本由上面常量生成所有派生文件/名字，任何地方都不许再手写版本号/产品名：
  · version_info_xinghong.txt  PyInstaller 的 PE 版本资源（仓库根目录）
  · version.txt                安装器读取的版本号（单行）
  · product_name.txt           安装器读取的产品名（单行）
  · installer/_name.iss        ISPP 的 MyAppName（UTF-8 带 BOM）

用法：
  python build_xinghong.py             # 只重新生成版本文件 + BOM/ASCII/旧名 断言
  python build_xinghong.py --build     # 生成后再跑 PyInstaller XingHongPanel.spec（含 exe 元数据回读）
  python build_xinghong.py --portable  # 把 dist/XingHongPanel 打成便携版 zip（可单用）
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 本目录即仓库根（README.md、version_info_xinghong.txt 都放在这里）
ROOT = HERE


def _pick(pattern, path):
    text = path.read_text(encoding='utf-8')
    m = re.search(pattern, text, re.M)
    if not m:
        raise SystemExit('未在 %s 找到 %s' % (path.name, pattern))
    return m.group(1)


def _tuple4(ver):
    parts = [int(x) for x in re.findall(r'\d+', ver)][:4]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def write_version_info(ver, name, desc):
    t = _tuple4(ver)
    body = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%s,
    prodvers=%s,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'X.H'),
         StringStruct(u'FileDescription', u'%s'),
         StringStruct(u'FileVersion', u'%s'),
         StringStruct(u'LegalCopyright', u'\\xa9 2026 X.H (MIT)'),
         StringStruct(u'OriginalFilename', u'XingHongPanel.exe'),
         StringStruct(u'ProductName', u'%s'),
         StringStruct(u'ProductVersion', u'%s')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
""" % (t, t, desc, ver, name, ver)
    (ROOT / 'version_info_xinghong.txt').write_text(body, encoding='utf-8')


def _assert_version_info(ver):
    """生成后回读断言：PE 版本资源四值必须等于当前 APP_VERSION。

    R38-26 第 5 步定性：这个文件是纯生成物。此前它入库停在 1.3.0，而 1.4.2 只存在
    于工作区 —— 也就是说交付 exe 的 FileVersion 来自版本控制里不存在的文件。
    现在文件被 .gitignore 忽略，构建前必须过这条断言，防止再出现"生成口径断了"。
    """
    txt = (ROOT / 'version_info_xinghong.txt').read_text(encoding='utf-8')
    t = _tuple4(ver)
    need = ('filevers=%s' % (t,), 'prodvers=%s' % (t,),
            "StringStruct(u'FileVersion', u'%s')" % ver,
            "StringStruct(u'ProductVersion', u'%s')" % ver)
    miss = [s for s in need if s not in txt]
    if miss:
        raise SystemExit('version_info 与 APP_VERSION=%s 不一致，缺：%s' % (ver, miss))


def _assert_bom(path):
    """ISPP 对 #include 的文件按 BOM 判编码；无 BOM 的 UTF-8 会被当 ANSI → 中文乱码。
    这里生成后回读断言，避免有人在编辑器里另存掉 BOM。"""
    data = path.read_bytes()
    if not data.startswith(b'\xef\xbb\xbf'):
        raise SystemExit('%s 缺 UTF-8 BOM：ISPP 会按 ANSI 解码，中文/中点会乱码' % path)
    if data.count(b'\xef\xbb\xbf') != 1:
        raise SystemExit('%s 的 BOM 数量异常（%d）' % (path, data.count(b'\xef\xbb\xbf')))


def _assert_ascii(path):
    """version.txt / _version.iss 都是纯 ASCII 且不带 BOM：ISPP 对 #include 按 BOM 判编码，
    无 BOM 的纯 ASCII 才能被稳定解析成版本号字符串。"""
    data = path.read_bytes()
    if data.startswith(b'\xef\xbb\xbf'):
        raise SystemExit('%s 不允许带 BOM（FileRead 会把 U+FEFF 读进版本号）' % path)
    try:
        data.decode('ascii')
    except UnicodeDecodeError:
        raise SystemExit('%s 必须是纯 ASCII' % path)


def _assert_no_old_name():
    """产品面旧名断言（保留函数以便构建链路调用；当前无旧名口径）。"""
    return


def verify_exe_meta(ver, name, desc):
    """回读 dist 里 exe 的 PE 版本资源，核对 版本/产品名/描述 三方一致。"""
    exe = HERE / 'dist' / 'XingHongPanel' / 'XingHongPanel.exe'
    if not exe.is_file():
        raise SystemExit('回读失败：%s 不存在' % exe)
    import ctypes
    from ctypes import wintypes

    ver_dll = ctypes.WinDLL('version')
    size = ver_dll.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        raise SystemExit('回读失败：exe 没有版本资源')
    buf = ctypes.create_string_buffer(size)
    if not ver_dll.GetFileVersionInfoW(str(exe), 0, size, buf):
        raise SystemExit('回读失败：GetFileVersionInfoW')

    def q(path):
        p = ctypes.c_void_p()
        n = wintypes.UINT()
        if not ver_dll.VerQueryValueW(buf, path, ctypes.byref(p), ctypes.byref(n)):
            return None
        return ctypes.wstring_at(p, n.value).split('\x00')[0]

    got = {'FileVersion': q(r'\StringFileInfo\040904B0\FileVersion'),
           'ProductName': q(r'\StringFileInfo\040904B0\ProductName'),
           'FileDescription': q(r'\StringFileInfo\040904B0\FileDescription'),
           'ProductVersion': q(r'\StringFileInfo\040904B0\ProductVersion')}
    want = {'FileVersion': ver, 'ProductName': name, 'FileDescription': desc,
            'ProductVersion': ver}
    bad = {k: (got.get(k), want[k]) for k in want if got.get(k) != want[k]}
    if bad:
        raise SystemExit('exe 元数据与单源不一致：%s' % bad)
    print('verified exe meta: %s' % got)


def write_portable(ver, name):
    """便携版 zip：目录结构固定，单源版本号写进包根 version.txt。

    XingHongPanel-<ver>/
      XingHongPanel.exe
      version.txt
      _internal/...
    条目按相对路径排序、时间戳统一 1980-01-01：同一次 dist 打包出的 zip 字节可复现，
    便于用 SHA256 判断"包是不是同一批"。
    """
    src = HERE / 'dist' / 'XingHongPanel'
    if not (src / 'XingHongPanel.exe').is_file():
        raise SystemExit('缺少 dist\\XingHongPanel\\XingHongPanel.exe：先跑 python build_xinghong.py --build')
    out = HERE / 'dist' / ('%s-%s-portable.zip' % (name, ver))
    root = 'XingHongPanel-%s' % ver
    items = sorted((p for p in src.rglob('*') if p.is_file()),
                   key=lambda p: p.relative_to(src).as_posix())
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in items:
            rel = p.relative_to(src).as_posix()
            zi = zipfile.ZipInfo('%s/%s' % (root, rel), date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, p.read_bytes())
        zi = zipfile.ZipInfo('%s/version.txt' % root, date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        z.writestr(zi, (ver + '\n').encode('ascii'))
    return out


# ps1_lint 豁免表：键是脚本相对路径，值必须写清为什么可以不管
# （例如历史脚本已被新链替换、只读不改）。当前为空 —— 全部 error=0。
PS1_EXEMPT = {}
# warn 豁免：NO_PSScriptRoot 只对「以脚本自身目录为根找同级文件」的脚本有意义。
# 内容/动作脚本的路径全由计划任务参数或脚本内常量给出，硬加 $PSScriptRoot
# 会变成未使用变量，需要豁免时在这里按「相对路径: 理由」登记。
PS1_WARN_EXEMPT = {}


# ---------------------------------------------------------------- 常驻互斥（R254）
# 构建坑机制化：`numpy\..._umath_linalg.pyd` 报「拒绝访问」不是权限问题，
# 而是本机看护任务每 20 s 把面板拉回来、面板进程锁着 dist\_internal 里的 pyd。
# 因此构建前可把「看护 + 面板」两个计划任务一起停掉，构建结束（含失败）
# 在 finally 里恢复并核 pong。任务名属于本机部署细节，用环境变量显式给出；
# 不设置时不干预任何计划任务（公开仓库默认就是这种情况）。
RESIDENT_WD_TASK = os.environ.get('XH_RESIDENT_WD_TASK', '')
RESIDENT_PANEL_TASK = os.environ.get('XH_RESIDENT_PANEL_TASK', '')
RESIDENT_TASKS = tuple(t for t in (RESIDENT_WD_TASK, RESIDENT_PANEL_TASK) if t)
PANEL_PIPE = r'\\.\pipe\XingHongPanelSingleInstance_v1'


def _ps(cmd, timeout=40):
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
        return (r.stdout or '').strip()
    except Exception as e:
        print('powershell 调用失败: %r（cmd=%s）' % (e, cmd))
        return ''


def _task_state(name):
    return _ps("(Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue).State"
               % name).strip()


def _pong(timeout=1.5):
    """面板单实例管道是否应答 pong（= 面板真在跑且事件循环活着）。"""
    try:
        f = open(PANEL_PIPE, 'r+b', buffering=0)
    except OSError:
        return False
    try:
        f.write(b'ping\n')
        f.flush()
        t0 = time.time()
        buf = b''
        while time.time() - t0 < timeout:
            try:
                buf += f.read(32)
            except OSError:
                pass
            if buf.strip():
                break
            time.sleep(0.05)
        return buf.strip() == b'pong'
    except OSError:
        return False
    finally:
        try:
            f.close()
        except OSError:
            pass


def _panel_pids():
    out = _ps("(Get-Process XingHongPanel -ErrorAction SilentlyContinue | "
              "Select-Object -ExpandProperty Id) -join ','")
    return [int(x) for x in out.replace('\r', '').split(',') if x.strip().isdigit()]


def _stop_resident():
    """构建前停常驻。返回各任务构建前的状态，供 finally 原样恢复。"""
    prev = {n: _task_state(n) for n in RESIDENT_TASKS}
    print('构建前常驻状态: %s' % prev)
    # 先停看护：它每 20 s 会把面板拉回来（本次 pyd 拒绝访问的直接原因）
    for n in (RESIDENT_WD_TASK, RESIDENT_PANEL_TASK):
        if prev.get(n):
            _ps("Stop-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % n)
    # 面板任务的动作就是 exe，停任务未必结束进程：发一次优雅退出并等它退
    if _pong(0.6):
        try:
            with open(PANEL_PIPE, 'wb') as f:
                f.write(b'quit\n')
                f.flush()
        except OSError:
            pass
    t0 = time.time()
    while time.time() - t0 < 15.0 and _panel_pids():
        time.sleep(0.5)
    left = _panel_pids()
    if left:
        print('警告：面板进程仍在 %s，强制结束（构建需解锁 dist\\_internal）' % left)
        _ps("Get-Process XingHongPanel -ErrorAction SilentlyContinue | Stop-Process -Force")
        time.sleep(1.5)
    return prev


def _restore_resident(prev):
    """finally 恢复常驻：先起面板并核 pong，再起看护；失败也打印明确结论。"""
    if not prev:
        return False
    print('构建后恢复常驻: %s' % prev)
    if prev.get(RESIDENT_PANEL_TASK):
        _ps("Start-ScheduledTask -TaskName '%s'" % RESIDENT_PANEL_TASK)
    ok = False
    t0 = time.time()
    while time.time() - t0 < 40.0:
        if _pong(0.6):
            ok = True
            break
        time.sleep(1.0)
    if prev.get(RESIDENT_WD_TASK):
        _ps("Start-ScheduledTask -TaskName '%s'" % RESIDENT_WD_TASK)
    if not ok:
        # 看护起来后还会把面板拉回来，再等一轮
        t0 = time.time()
        while time.time() - t0 < 40.0:
            if _pong(0.6):
                ok = True
                break
            time.sleep(1.0)
    print('panel pong: %s' % ('ok' if ok else 'FAILED'))
    if not ok:
        print('PANEL_RESTORE_FAILED：两个计划任务已下 Start，但面板 80 s 内未回 pong')
    return ok


def _lint_ps1():
    """前置硬门：installer/*.ps1 一律过 ps1_lint（error>0 不出包）。

    复用同级的 ps1_lint.py（同一份规则，避免各处各写一套）：
      E1 BOM_MISSING / E2 DQ_SUBEXPR（双引号里的 $( 被当子表达式）/ E3 PATH_VAR_DQ
      W1 HARDCODED_ROOT / W2 NO_PSScriptRoot（warn 不阻断）
    豁免必须写理由：EXEMPT = {相对路径: '理由'}，豁免文件只打印不阻断。
    """
    import importlib.util

    tool = next((p for p in [ROOT / 'app' / 'tools' / 'ps1_lint.py']
                 + sorted(ROOT.parent.glob('*/app/tools/ps1_lint.py'))
                 if p.is_file()), None)
    if tool is None:
        print('ps1-lint: 未找到 ps1_lint.py，跳过（不影响出包）')
        return
    spec = importlib.util.spec_from_file_location('ps1_lint', tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    targets = []
    targets += sorted((HERE / 'installer').glob('*.ps1'))
    targets = [p for p in targets if p.is_file()]
    if not targets:
        print('ps1-lint: 未找到 ps1（installer/），跳过')
        return

    totals, blocked = {'error': 0, 'warn': 0}, []
    for p in targets:
        r = mod.check_file(p)
        rel = p.relative_to(ROOT).as_posix()
        w = r['warns']
        if w and rel in PS1_WARN_EXEMPT:
            print('ps1-lint: %s warn 豁免 %d 条（%s）' % (rel, w, PS1_WARN_EXEMPT[rel]))
            w = 0
        totals['warn'] += w
        if r['errors'] and rel in PS1_EXEMPT:
            print('ps1-lint: %s 豁免（%s）err=%d' % (rel, PS1_EXEMPT[rel], r['errors']))
            continue
        if r['errors']:
            blocked.append(rel)
            print('ps1-lint: %s err=%d warn=%d' % (rel, r['errors'], r['warns']))
            for i in r['issues']:
                if i['level'] == 'error':
                    print('    L%-4d %-14s %s' % (i['line'], i['code'], i['msg']))
    print('ps1-lint: %d 个脚本 error=%d warn=%d'
          % (len(targets), totals['error'], totals['warn']))
    if blocked:
        raise SystemExit('ps1-lint 未通过：%s' % ', '.join(blocked))


def write_dist_readme(ver, name):
    """dist\\README.txt：同一目录只应有一个版本的包，把版本与 sha 写在明面上。"""
    import hashlib

    def sha(p):
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()

    exe = HERE / 'dist' / 'XingHongPanel' / 'XingHongPanel.exe'
    zp = HERE / 'dist' / ('%s-%s-portable.zip' % (name, ver))
    lines = ['%s %s · dist 产物说明（本文件由 build_xinghong.py 生成，勿手改）'
             % (name, ver), '']
    lines.append('生成时间: %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    lines.append('')
    if exe.is_file():
        lines.append('单文件目录版: dist\\XingHongPanel\\XingHongPanel.exe')
        lines.append('  大小  : %d B' % exe.stat().st_size)
        lines.append('  sha256: %s' % sha(exe))
    if zp.is_file():
        lines.append('便携 zip    : dist\\%s' % zp.name)
        lines.append('  大小  : %d B' % zp.stat().st_size)
        lines.append('  sha256: %s' % sha(zp))
    lines += ['', 'dist\\ 只保留当前版本；历史版本包请自行归档，避免装错版本。']
    out = HERE / 'dist' / 'README.txt'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return out


BACKEND_FILES = ('panel-server.py', 'web-profiles.json', 'comfy-paths.yaml')


def assert_backend_src():
    """P0 前置：后端源码必须来自仓库 backend/，缺失即构建失败。

    收口前 spec 从外部聊天目录取 panel-server.py，仓库里没有任何后端源码 ——
    交付的 exe 无法从仓库重建，只能从 dist 反挖。
    现在 spec 只认 HERE/backend/，这里在动手前先判一次：缺文件/空文件一律
    BACKEND_SRC_MISSING，不静默继续（故意改名 backend/ 必须能自证失败）。
    """
    b = HERE / 'backend'
    miss = [f for f in BACKEND_FILES if not (b / f).is_file()]
    if miss:
        raise SystemExit('BACKEND_SRC_MISSING: %s 缺少 %s；后端源码必须在仓库 backend/ 内，'
                         '不允许从 dist/_internal 或外部目录取。' % (b, ', '.join(miss)))
    empty = [f for f in BACKEND_FILES if (b / f).stat().st_size <= 0]
    if empty:
        raise SystemExit('BACKEND_SRC_MISSING: %s 为空文件' % ', '.join(str(b / f) for f in empty))


# ---------------------------------------------------------------- 开源许可随包（R38-26）
# 交付物里必须有 LICENSES\：名字列表不算"许可随包"，全文才算了。
# 数据源是构建环境里各包的 dist-info（License-Expression / License 字段 + licenses 目录），
# 不是手写清单；因此清单不会与真实打包内容脱节。
LICENSE_DIR_NAME = 'LICENSES'
LICENSE_HINTS = ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT')
# 实际会进 dist 的第三方包（缺任何一个即构建失败，防止"少打一个包、清单也少一行"）
EXPECTED_THIRD_PARTY = ('PySide6', 'shiboken6', 'numpy', 'psutil', 'pyqtgraph',
                        'PyYAML', 'charset-normalizer', 'pywin32')
# PySide6 是元包，真实文件分散在 Essentials/Addons/Pdf 等子发行包里，许可全文也分散在各自
# dist-info；对外仍算"一个 Qt 组件"，许可证合并到一个目录（按内容去重，Qt 文本完全重复）。
QT_FAMILY = ('PySide6', 'PySide6_Essentials', 'PySide6_Addons',
             'PySide6_WebEngine', 'PySide6_Pdf')
# 字体不是 Python 包，没有 dist-info，单独一篇 NOTICE 说明许可与全文地址。
FONT_NOTICE = ('NotoSansSC-OFL',
               ('Noto Sans SC（字体子集，位于 _internal\\fonts\\）\n'
                '许可：SIL Open Font License 1.1\n'
                '全文：https://openfontlicense.org/open-font-license-official-text/\n'
                '发行包未随附全文，以上为官方地址。\n'))
# 手工补充的许可正文：PySide6/shiboken6 的 wheel 只带 LicenseRef-Qt-Commercial.txt，
# 按 LGPL 分发必须随包给全文（LGPLv3 自身又要求同时提供 GPLv3 全文）。
# 来源与 SHA256 写在 LICENSES/Qt-LGPL-3.0/README.md，构建时原样复制，不在这里手写正文。
_sup_candidates = [HERE / 'LICENSES' / 'Qt-LGPL-3.0']
if HERE.parent != HERE:
    _sup_candidates.append(HERE.parent / 'LICENSES' / 'Qt-LGPL-3.0')
SUPPLEMENT_LICENSE_DIR = next((p for p in _sup_candidates if p.is_dir()), _sup_candidates[0])
SUPPLEMENT_LICENSE_FILES = ('LGPL-3.0.txt', 'GPL-3.0.txt')
PROJECT_LICENSE = ROOT / 'LICENSE'


def _md_field(md, key):
    v = (md.get(key) or '').strip()
    return v if 0 < len(v) <= 200 else ''


def _dist_license(dist):
    """返回 (许可标识, [(归档名, 源文件 Path)])。标识取自元数据，不猜。"""
    import importlib.metadata as md
    m = dist.metadata
    lic = _md_field(m, 'License-Expression') or _md_field(m, 'License')
    if not lic:
        cls = [c.replace('License :: ', '') for c in (m.get_all('Classifier') or [])
               if c.startswith('License ::')]
        lic = '; '.join(cls) or '（元数据未声明）'
    files = []
    for rel in (dist.files or []):
        s = str(rel).upper()
        if not any(h in s for h in LICENSE_HINTS):
            continue
        p = dist.locate_file(rel)
        if p.is_file():
            files.append((rel.name if rel.name else str(rel).rsplit('/', 1)[-1], p))
    return lic, files


def _candidate_dists(name):
    """逻辑组件 → 可能的发行包。PySide6 一个逻辑组件对应多个子发行包。"""
    import importlib.metadata as md
    out = []
    for n in (QT_FAMILY if name == 'PySide6' else (name,)):
        try:
            out.append(md.distribution(n))
        except md.PackageNotFoundError:
            pass
    return out


def _has_files(dist, internal):
    return any((internal / str(rel).replace('/', os.sep)).exists() for rel in (dist.files or []))


def _present_dists(dist_root):
    """按"包的发行文件是否真的在 _internal 里"判定，不靠顶层模块名猜。

    packages_distributions() 会把 PySide6 映射成 Addons/Essentials/WebEngine 等多个发行包，
    而它们共用同一个 PySide6 目录，按模块名判定会多出一堆重复清单。用 dist.files 逐个探活
    更准：只要该发行包有任一文件落在 _internal 下，就认为它被打了进去。
    """
    internal = dist_root / '_internal'
    if not internal.is_dir():
        raise SystemExit('LICENSES: 找不到 %s，先跑 --build' % internal)
    return [n for n in EXPECTED_THIRD_PARTY
            if any(_has_files(d, internal) for d in _candidate_dists(n))]


def write_licenses(dist_root):
    """把第三方许可全文收进 dist\\XingHongPanel\\LICENSES\\，并写清单。返回目录。"""
    import importlib.metadata as md
    out = dist_root / LICENSE_DIR_NAME
    found = _present_dists(dist_root)
    miss = [n for n in EXPECTED_THIRD_PARTY if n not in found]
    if miss:
        raise SystemExit('LICENSES: dist 里找不到预期第三方包 %s（已识别 %s）'
                         % (', '.join(miss), ', '.join(found) or '无'))
    rows, empty = [], []
    internal = dist_root / '_internal'
    for name in found:
        group = [d for d in _candidate_dists(name) if _has_files(d, internal)] or \
                _candidate_dists(name)[:1]
        ver = group[0].version
        lic = _dist_license(group[0])[0]
        d = out / ('%s-%s' % (name, ver))
        d.mkdir(parents=True, exist_ok=True)
        used, seen, copied = set(), set(), 0
        for dist in group:
            _lic, files = _dist_license(dist)
            for base, src in files:
                blob = src.read_bytes()
                h = __import__('hashlib').sha256(blob).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                fn = base if base not in used else '%s__%s' % (src.parent.name, base)
                used.add(fn)
                (d / fn).write_bytes(blob)
                copied += 1
        if not copied:
            (d / 'NOTICE.txt').write_text(
                '发行包内未随附许可证全文。\n组件：%s %s\n许可：%s\n'
                '官方许可文本见上游项目发布页。\n' % (name, ver, lic),
                encoding='utf-8')
            empty.append(name)
        rows.append((name, ver, lic, d.name, copied))
    d = out / FONT_NOTICE[0]
    d.mkdir(parents=True, exist_ok=True)
    (d / 'NOTICE.txt').write_text(FONT_NOTICE[1], encoding='utf-8')
    rows.append(('Noto Sans SC（字体子集）', '随包', 'SIL Open Font License 1.1',
                 FONT_NOTICE[0], 1))
    # 补充正文（Qt 的 LGPL/GPL 全文）：缺文件或内容不像正文就中断，别让空壳进包。
    for fn in SUPPLEMENT_LICENSE_FILES:
        src = SUPPLEMENT_LICENSE_DIR / fn
        if not src.is_file():
            raise SystemExit('LICENSES: 缺少补充许可正文 %s（LGPL/GPL 全文，见 LICENSES/README.md）' % src)
        # LGPL-3.0.txt 官方文本约 7.6 KB，GPL-3.0.txt 约 34 KB；低于 5 KB 基本是被截断/占位。
        if src.stat().st_size < 5000:
            raise SystemExit('LICENSES: %s 只有 %d 字节，疑似截断' % (src, src.stat().st_size))
        head_txt = src.read_text(encoding='utf-8', errors='replace')[:400]
        if 'GNU' not in head_txt or 'Version 3' not in head_txt:
            raise SystemExit('LICENSES: %s 不像 GNU 许可正文（开头缺 GNU / Version 3）' % src)
    sup = out / SUPPLEMENT_LICENSE_DIR.name
    sup.mkdir(parents=True, exist_ok=True)
    for fn in SUPPLEMENT_LICENSE_FILES + ('README.md',):
        src = SUPPLEMENT_LICENSE_DIR / fn
        if not src.is_file():
            raise SystemExit('LICENSES: 缺少 %s（来源与 SHA256 说明）' % src)
        shutil.copy2(src, sup / fn)
    rows.append(('Qt 许可正文（PySide6/shiboken6 补充）', '6.12.0',
                 'LGPL-3.0 + GPL-3.0（全文已随附）', sup.name, len(SUPPLEMENT_LICENSE_FILES)))
    # 根 LICENSE（本项目 MIT）也要随包一份：安装版在 {app}\LICENSE，便携版同层。
    if not PROJECT_LICENSE.is_file() or 'MIT License' not in \
            PROJECT_LICENSE.read_text(encoding='utf-8', errors='replace')[:200]:
        raise SystemExit('LICENSES: 根 LICENSE 缺失或不是 MIT 正文：%s' % PROJECT_LICENSE)
    shutil.copy2(PROJECT_LICENSE, dist_root / 'LICENSE')
    pname = _pick(r"^PRODUCT_NAME\s*=\s*'([^']+)'", HERE / 'ux_core.py')
    head = ['%s 第三方组件与开源许可清单（本文件由 build_xinghong.py 生成，勿手改）' % pname,
            '生成时间: %s' % time.strftime('%Y-%m-%d %H:%M:%S'), '',
            '%-28s %-10s %-46s %s' % ('组件', '版本', '许可标识（取自包元数据）', '目录'),
            '-' * 108]
    body = ['%-28s %-10s %-46s %s' % (n, v, lc, dn) for n, v, lc, dn, _c in rows]
    tail = ['', '许可证全文在各目录内；未随附全文的组件有 NOTICE.txt 说明。',
            'Qt（PySide6/shiboken6）的 LGPL-3.0 与 GPL-3.0 全文已随附在 LICENSES\\Qt-LGPL-3.0\\。',
            '项目自身的 MIT 许可全文在包根 LICENSE。',
            '组件数: %d（断言：目录数 >= 实际打包的第三方包数）' % len(rows)]
    (out / 'README.txt').write_text('\n'.join(head + body + tail) + '\n', encoding='utf-8')
    dirs = [x for x in out.iterdir() if x.is_dir()]
    if len(dirs) < len(found):
        raise SystemExit('LICENSES: 目录数 %d < 识别到的第三方包数 %d' % (len(dirs), len(found)))
    for x in dirs:
        if not any(x.iterdir()):
            raise SystemExit('LICENSES: %s 为空目录' % x)
    return out, rows, empty


# ---------------------------------------------------------------- legal 正文闸门（R38-26）
LEGAL_DOCS = ('用户协议.md', '隐私说明.md', '安全与免责提示.md', '第三方组件与开源许可.md')
LEGAL_CONTACT = 'dreamarchitect1026@gmail.com'
LEGAL_LIMIT = 4000


def assert_legal_docs():
    """四份正文必须在、版本与 CONSENT_VERSION 同源、联系方式与 UI 一致、字数不超限。

    这里刻意不 import ux_core（会拉起 PySide6），用正则读常量，其余全是纯文本检查。
    """
    ver = _pick(r"^CONSENT_VERSION\s*=\s*'([^']+)'", HERE / 'ux_core.py')
    for fn in LEGAL_DOCS:
        p = HERE / 'legal' / fn
        if not p.is_file():
            raise SystemExit('LEGAL_DOC_MISSING: %s' % p)
        t = p.read_text(encoding='utf-8')
        m = re.search(r'版本：(\S+)', t)
        got = m.group(1) if m else ''
        if got != '%DOCVER%':
            raise SystemExit('LEGAL_VERSION: %s 头部版本 %r，应为占位符 %%DOCVER%%（与 CONSENT_VERSION 同源）'
                             % (fn, got))
        if LEGAL_CONTACT not in t:
            raise SystemExit('LEGAL_CONTACT: %s 缺少 %s' % (fn, LEGAL_CONTACT))
        han = len(re.findall(r'[\u4e00-\u9fa5]', t))
        body = len(re.sub(r'\s+', '', t))
        if body > LEGAL_LIMIT:
            raise SystemExit('LEGAL_TOO_LONG: %s 去空白 %d 字 > %d' % (fn, body, LEGAL_LIMIT))
        print('LEGAL ok: %-24s 汉字=%4d 去空白=%4d 版本=%s' % (fn, han, body, ver))


def _check_import_layers():
    """C1：分层门禁（ui → app → services → infra；反向 import / 私有符号 / 缺导出即中断）。"""
    script = HERE.parent / 'tools' / 'check_imports.py'
    rc = subprocess.call([sys.executable, str(script)])
    if rc != 0:
        raise SystemExit('IMPORT_LAYER_VIOLATION：分层检查未通过，见上方明细')


def _check_page_handles():
    """C1-B2：页面句柄门禁（页外引用必须解包；页面模块不许有未定义名字）。"""
    script = HERE.parent / 'tools' / 'check_page_handles.py'
    rc = subprocess.call([sys.executable, str(script), '--quiet'])
    if rc != 0:
        raise SystemExit('UNBOUND_PAGE_HANDLE：页面句柄检查未通过，见上方明细')


def _check_prefs_keys():
    """C1-B2：配置中心 QSettings 键名/默认值门禁（与 ux.DEFAULTS 不一致即中断）。"""
    script = HERE.parent / 'tools' / 'check_prefs_keys.py'
    rc = subprocess.call([sys.executable, str(script)])
    if rc != 0:
        raise SystemExit('PREFS_KEY_MISMATCH：配置中心 QSettings 键/默认值检查未通过，见上方明细')


def main():
    assert_backend_src()
    assert_legal_docs()
    ap = argparse.ArgumentParser()
    ap.add_argument('--licenses', action='store_true', help='只收第三方许可到 dist 的 LICENSES\\')
    ap.add_argument('--build', action='store_true', help='生成后运行 PyInstaller')
    ap.add_argument('--portable', action='store_true', help='打便携版 zip（dist 必须已存在）')
    a = ap.parse_args()

    ver = _pick(r"^APP_VERSION\s*=\s*'([^']+)'", HERE / 'ux_core.py')
    name = _pick(r"^PRODUCT_NAME\s*=\s*'([^']+)'", HERE / 'ux_core.py')
    title = name                      # R252：标题=产品名，不再拼副标题

    if a.licenses:
        out, rows, empty = write_licenses(HERE / 'dist' / 'XingHongPanel')
        print('LICENSES: %s' % out)
        for n, v, lc, dn, c in rows:
            print('  %-24s %-10s %-40s files=%d' % (n, v, lc[:40], c))
        if empty:
            print('NOTICE only: %s' % ', '.join(empty))
        return 0

    write_version_info(ver, name, title)
    _assert_version_info(ver)
    (HERE / 'version.txt').write_text(ver + '\n', encoding='ascii')
    (HERE / 'product_name.txt').write_text(name + '\n', encoding='utf-8')
    # 中文显示名不能走 ISPP 的 FileRead：无 BOM 的 UTF-8 会被当 ANSI 读成乱码，
    # 带 BOM 又会把 U+FEFF 带进名称。改用 utf-8-sig 生成 #include 片段，让预处理器按 BOM 解码。
    name_iss = HERE / 'installer' / '_name.iss'
    name_iss.parent.mkdir(parents=True, exist_ok=True)
    name_iss.write_text('#define MyAppName "%s"\n' % name, encoding='utf-8-sig')
    _assert_bom(name_iss)
    # ISPP 没有 FileRead（实测报 “Integer expression expected”），版本同样用生成片段注入，
    # 保证 .iss 与 version.txt / product_name.txt 同源同值。
    version_iss = HERE / 'installer' / '_version.iss'
    version_iss.write_text('#define MyAppVersion "%s"\n' % ver, encoding='ascii')
    _assert_ascii(name_iss.parent / '_version.iss')
    _assert_ascii(HERE / 'version.txt')
    _assert_no_old_name()
    _lint_ps1()          # R251：ps1 前置硬门（error>0 不出包）
    _check_import_layers()   # C1：分层门禁（IMPORT_LAYER_VIOLATION 即中断）
    _check_page_handles()    # C1-B2：页面句柄门禁（UNBOUND_PAGE_HANDLE 即中断）
    _check_prefs_keys()      # C1-B2：配置中心 QSettings 键/默认值门禁（PREFS_KEY_MISMATCH 即中断）
    print('APP_VERSION=%s' % ver)
    print('PRODUCT_NAME=%s / 窗口标题=%s' % (name, title))
    print('written: version_info_xinghong.txt, xinghong/version.txt, product_name.txt')
    print('checked: _name.iss BOM=ok, version.txt ascii=ok')

    rc = 0
    if a.build:
        # R254：构建前停看护 + 面板（否则 dist\_internal 被占用 → pyd 拒绝访问），
        # finally 里无论成败都恢复并核 pong。
        _prev = _stop_resident()
        try:
            rc = subprocess.call([sys.executable, '-m', 'PyInstaller', 'XingHongPanel.spec',
                                  '--noconfirm', '--clean'], cwd=str(HERE))
            if rc != 0:
                return rc
            verify_exe_meta(ver, name, title)
            # 许可全文必须在打便携包与安装包之前落进 dist，否则两个交付物都没有 LICENSES\。
            lo, lrows, le = write_licenses(HERE / 'dist' / 'XingHongPanel')
            print('LICENSES: %d 个组件 -> %s%s' % (len(lrows), lo,
                                                  ('（仅 NOTICE: %s）' % ', '.join(le)) if le else ''))
        finally:
            _restore_resident(_prev)
    if a.portable:
        out = write_portable(ver, name)
        print('portable: %s (%d B)' % (out, out.stat().st_size))
    rd = write_dist_readme(ver, name)
    print('dist readme: %s' % rd)
    return rc


if __name__ == '__main__':
    sys.exit(main())
