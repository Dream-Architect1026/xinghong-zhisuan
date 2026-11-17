# XingHong ZhiNao native client (PySide6, no webview/browser) — glass/star theme.
# Backend: the project's real panel-server.py loaded in-place via importlib in
# a non-daemon thread; UI talks to it over 127.0.0.1:8899 only.
# Close window -> ask: minimize to tray (service keeps running) or exit
# (stop managed services via /api/stopall, then quit).
import ctypes.wintypes  # noqa: F401  panel-server imports it; PyInstaller misses this submodule
import importlib.util
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

# R250：冷启动「进程启动 → 主窗首帧」口径起点。放在 PySide6 导入之前，
# 使 Qt 导入耗时也算进冷启动（越早越接近进程创建时刻）。
_PERF_T0 = time.perf_counter()

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject,
    QPointF, Property, QPropertyAnimation, QPoint, QRect, QRectF, QSettings, QSize,
    Qt, QTimer, QUrl, QVariantAnimation, Signal, QThread)
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient, QFont,
    QFontDatabase, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QPainterPath,
    QPalette, QPen, QPixmap, QPolygonF, QRadialGradient, QRegion, QShortcut, QKeySequence,
    QTransform)
import ux_core as ux
import xh_http
import xh_time
from ui.pages_video import build_page_video  # C1-B2：页面模块（ui 层内，不反向 import）
from ui.pages_upscale import build_page_upscale
from ui.pages_reasoning import build_page_reasoning
from ui.pages_status import build_page_status
from ui.pages_gen import build_page_gen
from ui.pages_config import build_page_config
from ui.pages_xiaoxing import build_page_xiaoxing
from ui.gallery import build_gallery_helpers
from app.timers import build_polling_timers
from app.tray import build_tray_and_close
from app.xx_workers import build_xx_workers, XX_AGENT_TOOLS, XX_AGENT_NAMES
# C1-B3：跨层共享的运行时真源收到 infra.config，页面/定时器/托盘直接 import，
# 不再经 build_ui 传参（B2 累计多出的 44 个开关出现点由此折回）。
from infra.config import (XH_ADV, XH_CADENCE, XH_COST, XH_PROBE, XH_PROFILE,
                          XH_COVER, XH_WALL_N)
from infra.config import XH_MUTE as _XH_MUTE, XH_EXIT_HOW as _XH_EXIT_HOW
from infra import store as _store
from infra import exitd as _exitd

# R20-8：流光主节拍唯一常量——DecoOverlay / 自带定时器 / 共享定时器三处都读它。
# 默认 150 ms（6.67 fps）；A/B 时可用 XH_FLOW_MS=80/300 改这一处即可。
# XH_FLOW_ACTIVE=1：只有「正在充的条」（2.5 s 内 ratio 变过）才推进流光，其余静止。
XH_FLOW_MS = int(os.environ.get('XH_FLOW_MS') or os.environ.get('XH_OV_MS') or 150)
XH_FLOW_ACTIVE = os.environ.get('XH_FLOW_ACTIVE', '') == '1'
# R02（25 轮）诊断开关：已收进 infra/config.py（取值可组合 nogauge/nochart/nodot）。
# R10（性能 A/B）：逐项开关，环境变量 XH_R10 逗号分隔。
#   pollslow2 轮询合并降频（t2 5→25 s、页面级 ×5；t1 严格按旋钮值，不再隐藏 ×2）
#   hidestop  装饰动画随页面隐藏停表（BreathDot/EnergyBox/KenBurns 隐藏即停）
#   chartslow 曲线重绘 3→9 拍一帧（仅首页可见时有意义）
#   thumb3    缩略图并发 8→3（稳态限流）
#   freeze    诊断位：进页即停掉全部轮询，用于测下限
#   plain     诊断位：关掉全部 R10 默认优化（复现基线）
# 默认（不设 XH_R10）= pollslow2 + hidestop + thumb3（loopslow 在 backend 默认开）。
_R10_ENV = set(x.strip() for x in (os.environ.get('XH_R10') or '').split(',') if x.strip())
_R10_DEF = {'pollslow2', 'hidestop', 'thumb3'}
R10 = set() if 'plain' in _R10_ENV else (_R10_ENV or set(_R10_DEF))
# ── R20-8：节拍唯一真源（对象收在 infra/config.py）──────────────────────────
# 轮询间隔只在 _xh_cadence() 里算成「有效毫秒」并写入 XH_CADENCE；
# 其它任何地方（启动 / 藏托盘 / 改旋钮 / 自检）一律读这里，禁止再出现
# ×2、×1000、_PK 之类散落换算——R20-7 的 t1「旋钮 2s、实测 4.00s」正是
# 同一设计值在两处各乘一次造成的。
_CAD_KEYS = ('t1', 't2', 't4', 't5', 't11', 't13', 't14', 't15', 't16', 't17')
_CAD_MEAS = {}           # 每个定时器最近触发时间戳（`节拍` 自检的「实测中位」列）


def _xh_cadence(hidden=False):
    """唯一真源：所有轮询定时器的有效毫秒都在这里算（整数毫秒）。"""
    try:
        _pi = max(1, min(10, int(XH_ADV.get('pollInterval') or 2)))
    except Exception:
        _pi = 2
    _pk = 5 if 'pollslow2' in R10 else (3 if 'pollslow' in R10 else 1)
    _c = {'t1': _pi * 1000, 't2': 5000 * _pk, 't4': 10000 * _pk,
          't5': 15000 * _pk, 't11': 10000 * _pk, 't13': 8000 * _pk,
          't14': 12000 * _pk, 't15': 6000 * _pk, 't16': 15000 * _pk,
          't17': 15000 * _pk, '_pk': _pk}
    if 'nometrics' in XH_PROBE:
        _c['t1'] = 60000
    if hidden:
        # R37-1：隐藏到托盘后的统一降频（唯一一处隐藏倍率，别处只许读）。
        # 依据：
        #  · t1 走 /api/metrics?since= 增量拉取，降频只是「攒一批再取」，
        #    服务端 2 s 采样点不会丢——恢复后首拍即补齐，故可放大到 ×10。
        #  · t2/t11 只刷新界面（状态芯片/能耗卡），隐藏期无人看；恢复时
        #    show_window 立即 poll_state + 当前页全量刷新，无可感知滞后。
        #  · 稳态 CPU 主要来源就是 t1（2 s 一拍 HTTP+JSON）与 t2（子进程缓存），
        #    ×10 后轮询开销约为原来的 1/10。
        #  · 单拍上限 10 分钟，避免 nometrics 等诊断位二次相乘后无限拉长。
        _HID_K = 10
        for _n in _CAD_KEYS:
            _c[_n] = min(_c[_n] * _HID_K, 600000)
        _c['_hid_k'] = _HID_K
    return _c


def _cad_hit(name):
    """定时器触发时间戳（自检「实测」列）。开销：一次 append。"""
    try:
        _a = _CAD_MEAS.setdefault(name, [])
        _a.append(time.perf_counter())
        if len(_a) > 400:
            del _a[:-400]
    except Exception:
        pass


def _cad_rows(timers=None):
    """三列：设计值（_xh_cadence）/ 实际生效值（timer.interval）/ 实测相邻中位。"""
    _c = _xh_cadence()
    _rows = {}
    for _n in _CAD_KEYS:
        _ds = _c[_n]
        _tm = (timers or {}).get(_n)
        _eff = int(_tm.interval()) if _tm is not None else None
        _arr = list(_CAD_MEAS.get(_n) or [])
        _iv = [(_arr[i] - _arr[i - 1]) * 1000.0 for i in range(1, len(_arr))]
        _med = _min = _max = None
        if len(_iv) >= 1:
            _s = sorted(_iv)
            _med = round(_s[len(_s) // 2], 1)
            _min = round(_s[0], 1)
            _max = round(_s[-1], 1)
        _ok = (_eff == _ds)
        if _med is not None:
            _ok = _ok and abs(_med - _ds) <= max(150.0, _ds * 0.02)
        _rows[_n] = {'design_ms': _ds, 'effective_ms': _eff,
                     'measured_ms': _med, 'n': len(_iv),
                     'min_ms': _min, 'max_ms': _max, 'ok': bool(_ok)}
    return _rows


XH_PAINT = os.environ.get('XH_PAINT', '')       # R12：非空则把按类 Paint 计数写该文件
# R18-3a：A/B 开关——XH_NOOPAQUE=1 时不设根控件的 WA_OpaquePaintEvent（同一构建只切这一个标记）。
XH_NOOPAQUE = os.environ.get('XH_NOOPAQUE', '') == '1'
# R14 A/B 开关（墙内条数 / 强制 cover）：已收进 infra/config.py。
XH_NOFIX = bool(os.environ.get('XH_NOFIX'))   # R14 A/B：关掉「不透明补底」修正，仅取证用
# R254：A/B 开关（同 exe）。legacy 走改前路径，on（默认）走本轮修复。
#   XH_BG_FIX   ：Backdrop 只贴脏区（尺寸/主题不变不重画星空）
#   XH_CARD_FIX ：GlassCard 缓存按尺寸失效，淡入动画期间直接用旧缓存不重建
XH_BG_FIX = os.environ.get('XH_BG_FIX', 'on').strip().lower() != 'legacy'
XH_CARD_FIX = os.environ.get('XH_CARD_FIX', 'on').strip().lower() != 'legacy'

# ── R20-5 / except 规范化：任何「被吞掉的异常」都必须留痕 ────────────────────
# 事故样本：`_band_capture` 的 `w.move(QRect)` TypeError 被 `except: pass` 吞掉，
# 让 R19/R20 两轮「深抓已实现」变成空头承诺。规则：
#   ① 全仓 `except ...: pass|continue` 一律改为先调 `_err()`（异常仍被吞，语义不变）；
#   ② 同一位置（文件:行）最多每 60s 写一条带栈日志，但次数全部累加；
#   ③ `errs` 管道命令导出「种类 / 次数 / 首次堆栈」，并与白名单比对，非白名单 = 失败。
# 白名单文件：与本文件同目录的 `errs_whitelist.json`（条目 + 理由 + 首次出现时间）。
_ERRS = {}
_ERRS_WL = {}
_ERR_LAST = {}
_ERRS_T0 = time.time()
_ERR_LOGPATH = os.environ.get('XH_ERRLOG') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '_errs_live.log')


def _errs_load_whitelist():
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'errs_whitelist.json')
        with open(_p, 'r', encoding='utf-8') as _f:
            _d = json.load(_f)
        for _k, _v in (_d.get('entries') or {}).items():
            _ERRS_WL[_k] = _v
    except Exception:
        pass


def _err():
    """被吞异常的唯一落点：调用方位置即 site（不传参，零误配）。"""
    try:
        _f = sys._getframe(1)
        _site = '%s:%d' % (os.path.basename(_f.f_code.co_filename), _f.f_lineno)
        _et, _ev, _tb = sys.exc_info()
        _d = _ERRS.get(_site)
        if _d is None:
            _ERRS[_site] = {
                'type': getattr(_et, '__name__', 'None'),
                'msg': str(_ev)[:200],
                'count': 1,
                'first_s': round(time.time() - _ERRS_T0, 3),
                'stack': ''.join(traceback.format_exception(_et, _ev, _tb))[-1200:],
            }
            _d = _ERRS[_site]
        else:
            _d['count'] += 1
        _now = time.time()
        if _now - _ERR_LAST.get(_site, 0.0) >= 60.0:
            _ERR_LAST[_site] = _now
            try:
                with open(_ERR_LOGPATH, 'a', encoding='utf-8') as _fh:
                    _fh.write('[err] %s #%d %s: %s\n%s\n'
                              % (_site, _d['count'], _d['type'], _d['msg'], _d['stack']))
            except Exception:
                pass
    except Exception:
        pass


def _errs_dump():
    _rows = []
    _bad = 0
    for _site, _d in sorted(_ERRS.items(), key=lambda kv: -kv[1]['count']):
        _wl = _ERRS_WL.get(_site)
        if not _wl:
            _bad += 1
        _rows.append({'site': _site, 'type': _d['type'], 'msg': _d['msg'],
                      'count': _d['count'], 'first_s': _d['first_s'],
                      'whitelisted': bool(_wl),
                      'reason': (_wl or {}).get('reason', ''),
                      'stack': _d['stack']})
    return {'total_sites': len(_rows), 'total_count': sum(r['count'] for r in _rows),
            'unwhitelisted': _bad, 'fail': _bad > 0, 'elapsed_s': round(time.time() - _ERRS_T0, 1),
            'entries': _rows}

def _cap_colors(img):
    """24×24 快缩采样颜色种类；<=2 视为「未绘制」（grab 出来的全透明/纯色图）。"""
    try:
        _t = img.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        _s = set()
        for _y in range(24):
            for _x in range(24):
                _s.add(_t.pixel(_x, _y))
                if len(_s) > 2:
                    return 3
        return len(_s)
    except Exception:
        return 3


def _cap_page_ready(widget, strict=True):
    """切页淡入未结束时整页还挂 QGraphicsOpacityEffect（实测抓到 0.386），
    这时的 grab 要么整页透明、要么两个不同 tab 拍成同一张。等效果移除再抓。

    R248：opacity 先到 1.0，cleanup 才在 anim.finished 里卸 effect，中间有一帧
    窗口——此时仍走 effect 合成，整页亮度差约 1 LSB（实测 88636 px、max 84）。
    strict=True 时要求 effect 已卸；strict=False 退回旧的 opacity 判据兜底。
    """
    try:
        _st = getattr(widget, '_stack', None)
        if _st is None:
            return True
        _pg = _st.widget(int(_st.currentIndex()))
        if _pg is None:
            return True
        _eff = _pg.graphicsEffect()
        if _eff is None:
            return True
        if strict:
            return False
        try:
            return float(_eff.opacity()) >= 0.999
        except Exception:
            return True
    except Exception:
        return True


def _cap_grab(widget, path, tries=30, delay=100, tag='', wait=0):
    """R247 取证修复：窗口未绘制时 grab() 得到的是全透明 QPixmap（存盘成纯黑）。

    成因（实测 _errs 同源取证）：进程冷启时主线程被启动任务占住，命令里的固定
    singleShot(900) 抓图会先于首帧 Paint 执行；Qt 对「尚未绘制」的窗口按不透明
    标记返回空 backing store，grab 结果 alpha 全 0。
    修法：只在 isVisible && isExposed 时先同步 repaint() 强制出帧，再 grab；
    结果仍是纯色就每 delay 毫秒重试，最多 tries 次。成功后落盘，
    失败落 <path>.blank（写明时机与尺寸，PNG 照存便于复现）。
    """
    _st = {'n': 0, 'w': 0}

    def _once():
        _vis = False
        _exp = False
        _pm = None
        if not _cap_page_ready(widget, strict=(_st['w'] < 20)):
            # 淡入/卸 effect 未结束：不占抓图重试次数，最多等 20 s 再落空图
            _st['w'] += 1
            if _st['w'] <= 200:
                QTimer.singleShot(delay, _once)
                return
        _st['n'] += 1
        try:
            _vis = bool(widget.isVisible())
            try:
                _wh = widget.windowHandle()
                _exp = bool(_wh is not None and _wh.isExposed())
            except Exception:
                _exp = False
            if _vis and _exp:
                widget.repaint()
            _pm = widget.grab()
        except Exception:
            _err()
        _ok = bool(_pm is not None and not _pm.isNull()
                   and _cap_colors(_pm.toImage()) > 2)
        if _ok:
            try:
                _pm.save(path)
            except Exception:
                _err()
            return
        if _st['n'] < tries:
            QTimer.singleShot(delay, _once)
            return
        try:
            with open(path + '.blank', 'w', encoding='utf-8') as _f:
                _f.write('tries=%d visible=%s exposed=%s size=%sx%s tag=%s\n'
                         % (_st['n'], _vis, _exp, widget.width(), widget.height(), tag))
            if _pm is not None and not _pm.isNull():
                _pm.save(path)
        except Exception:
            _err()

    QTimer.singleShot(wait, _once)


try:
    XH_FLOW_PHASE = (float(os.environ['XH_FLOW_PHASE'])
                     if os.environ.get('XH_FLOW_PHASE') else None)
except Exception:
    XH_FLOW_PHASE = None      # R15：固定流光相位，仅零像素差取证用
XH_NOFLOWSHARE = bool(os.environ.get('XH_NOFLOWSHARE'))   # R15 A/B：关掉共享节拍（回到每条约一个定时器）
# R14 归因计数：宫格重排 / 重渲染 / fit_pixmap 调用次数（不参与绘制，仅累加）
FIT_STAT = {'apply': 0, 'reicon': 0, 'fit': 0, 'skip': 0, 'icache': 0}
# R06：主线程帧心跳（仅 XH_PROFILE 非空时启用）——16 ms 定时器记录相邻回调间隔，
# 作为「最长帧间隔」硬指标；只做累加，不触发重绘。
_frame_probe = {'last': 0.0, 'n': 0, 'sum': 0.0, 'max': 0.0}

# R36-N1：冷启分段打点。时间基准统一为「进程被创建」（GetProcessTimes），
# 因此口径包含 PyInstaller 引导器 + 解释器启动，不是「从某一行代码开始」。
# 默认零开销：只写内存 dict，`bootinfo` 命令才落盘。
_BOOT = {'marks': {}}


def _proc_age_s():
    """当前进程从创建到此刻的秒数。取不到返回 -1。"""
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt

        class _FT(_ct.Structure):
            _fields_ = [('lo', _wt.DWORD), ('hi', _wt.DWORD)]

        _k = _ct.windll.kernel32
        _k.GetCurrentProcess.restype = _wt.HANDLE
        _k.GetProcessTimes.argtypes = [_wt.HANDLE, _ct.POINTER(_FT), _ct.POINTER(_FT),
                                       _ct.POINTER(_FT), _ct.POINTER(_FT)]
        _k.GetProcessTimes.restype = _wt.BOOL
        _c, _e, _kt, _ut = _FT(), _FT(), _FT(), _FT()
        _h = _k.GetCurrentProcess()
        if _k.GetProcessTimes(_h, _ct.byref(_c), _ct.byref(_e),
                              _ct.byref(_kt), _ct.byref(_ut)):
            _now = _wt.FILETIME()
            _k.GetSystemTimeAsFileTime(_ct.byref(_now))
            _cre = (_c.hi << 32) | _c.lo
            _cur = (_now.dwHighDateTime << 32) | _now.dwLowDateTime
            return (_cur - _cre) / 1e7
    except Exception:
        pass
    try:   # 兜底：psutil 的进程创建时间（与 GetProcessTimes 同源，差值可忽略）
        import psutil
        return time.time() - psutil.Process(os.getpid()).create_time()
    except Exception:
        return -1.0


def _boot_mark(name):
    try:
        if name not in _BOOT['marks']:
            _BOOT['marks'][name] = round(_proc_age_s(), 3)
    except Exception:
        pass


# 协议同意判定留痕：实现搬到 infra/store.py（C1-B3），这里沿用原名给 main()/ensure_consent 调用。
_consent_log = _store.consent_log


_boot_mark('import_done')

# R250：帧节拍实测探针。默认零开销——只有 env XH_PERF_LOG 非空时，_perf_start()
# 才建 16 ms QTimer 与首帧 Paint 过滤器；不设 env 时连定时器都不创建。
# 口径：t0 取模块级 perf_counter（≈ 解释器开始执行本文件），首帧 = 主窗第一次
# QEvent.Paint；ticks 每项 [t_ms, dt_ms]，dt 即「事件循环被上一拍占用多久」。
_PERF = {'on': False, 't0': _PERF_T0, 'last': 0.0,
         'ticks': [], 'marks': [], 'first_paint_ms': None,
         'timer': None, 'filter': None}
# R253：build_ui 里的页面构建打点发生在 _perf_start 之前，这里让打点从导入期就生效
# （只影响 marks；16 ms 帧节拍定时器与首帧过滤器仍只在 _perf_start 里创建）。
_PERF['on'] = bool((os.environ.get('XH_PERF_LOG') or '').strip())


def _perf_start(win):
    """在 w.show() 之前安装帧节拍探针（GUI 线程 16 ms 精度定时器）。"""
    if not (os.environ.get('XH_PERF_LOG') or '').strip():
        return                                  # 零开销路径：不建 timer、不装 filter
    _PERF['on'] = True

    class _PerfPaint(QObject):
        def eventFilter(self, obj, ev):
            if _PERF.get('in_prewarm'):
                return False          # XH_PREWARM 的离屏光栅化不算首帧
            if _PERF['first_paint_ms'] is None and ev.type() == QEvent.Paint:
                try:
                    if obj.window() is not win:
                        return False
                except Exception:
                    return False
                _PERF['first_paint_ms'] = round(
                    (time.perf_counter() - _PERF['t0']) * 1000.0, 1)
                # R251：把「首帧 Paint 本身」与「Paint 之后同一轮事件里的尾活」分开——
                # singleShot(0) 在当前事件处理完之后才跑，所以它的时刻≈Paint 结束点。
                QTimer.singleShot(0, lambda: _perf_mark('paint_done'))
                try:
                    QApplication.instance().removeEventFilter(self)
                except Exception:
                    pass
            return False

    _pf = _PerfPaint()
    _appi = QApplication.instance()
    if _appi is not None:
        _appi.installEventFilter(_pf)

    def _tick():
        _now = time.perf_counter()
        _dt = (_now - _PERF['last']) * 1000.0 if _PERF['last'] else 0.0
        _PERF['last'] = _now
        if _dt > 0:
            _PERF['ticks'].append([round((_now - _PERF['t0']) * 1000.0, 1),
                                   round(_dt, 2)])

    _tm = QTimer()
    _tm.setTimerType(Qt.PreciseTimer)
    _tm.setInterval(16)
    _tm.timeout.connect(_tick)
    _tm.start()
    _PERF['timer'] = _tm        # 防 GC
    _PERF['filter'] = _pf


def _perf_mark(label):
    """在当前 tick 流里打分段标记（无探针时 no-op）。"""
    try:
        if not _PERF['on']:
            return
        _PERF['marks'].append({
            'label': label,
            't': round((time.perf_counter() - _PERF['t0']) * 1000.0, 1),
            'tick': len(_PERF['ticks'])})
    except Exception:
        pass


def _perf_dump(path):
    try:
        with open(path, 'w', encoding='utf-8') as _f:
            json.dump({'first_paint_ms': _PERF['first_paint_ms'],
                       'ticks': _PERF['ticks'],
                       'marks': _PERF['marks'],
                       'attr': _attr_dump()}, _f, ensure_ascii=False)
    except Exception:
        _err()


# R254：首帧后长帧归因（仅 env XH_ATTR 非空时装 patch，默认零开销）。
# 记账口径：Backdrop.paintEvent / GlassCard._render_cache / QWidget.update
# 各自「次数 + 耗时 + 触发者（控件名 + 原因 + 直接调用点）」。
# 原因分类：resize / move / show / anim:fade / style / value / explicit / qt:expose。
# 窗口：从模块 t0 起 XH_ATTR_MS（默认 3000 ms），分析时再按首帧切片。
_ATTR = {'on': bool((os.environ.get('XH_ATTR') or '').strip()),
         't0': _PERF_T0,
         'end_ms': float(os.environ.get('XH_ATTR_MS', '3000') or 3000),
         'ev': [], 'upd': {}, 'drop': 0, 'upd_cost_ms': 0.0,
         'installed': False}


def _attr_now():
    return (time.perf_counter() - _ATTR['t0']) * 1000.0


def _attr_live():
    return _ATTR['on'] and _attr_now() <= _ATTR['end_ms']


def _attr_classify(fr, depth=12):
    """从调用栈判「为什么 update」：返回 (原因, 直接调用点)。"""
    site = ''
    reason = 'explicit'
    for i in range(depth):
        if fr is None:
            break
        code = fr.f_code
        nm = code.co_name
        if i == 0:
            site = '%s:%s:%d' % (os.path.basename(code.co_filename), nm, fr.f_lineno)
        low = nm.lower()
        if 'resizeevent' in low:
            reason = 'resize'
            break
        if 'moveevent' in low:
            reason = 'move'
            break
        if 'showevent' in low:
            reason = 'show'
            break
        if 'fade' in low:
            reason = 'anim:fade'
            break
        if 'timer' in low or low in ('_tick', '_step', 'timeout') or 'anim' in low:
            reason = 'anim'
            break
        if 'theme' in low or 'style' in low or low.startswith('apply_'):
            reason = 'style'
            break
        if low in ('setvalue', 'settext', 'setcurrentindex', 'set_formatter'):
            reason = 'value'
            break
        fr = fr.f_back
    return reason, site


def _attr_who(widget):
    """这次 paint 是谁引起的：优先「正在淡入」，其次本控件/祖先最近一次 update。"""
    x = widget
    for _ in range(6):
        if x is None:
            break
        a = getattr(x, '_fade_anim', None)
        try:
            if a is not None and a.state() == QAbstractAnimation.Running:
                return 'anim:fade', (x.objectName() or type(x).__name__)
        except Exception:
            pass
        x = x.parentWidget()
    u = getattr(widget, '_attr_upd', None)
    if u and _attr_now() - u[0] < 300:
        return u[2], u[1]
    x = widget.parentWidget()
    for _ in range(6):
        if x is None:
            break
        u = getattr(x, '_attr_upd', None)
        if u and _attr_now() - u[0] < 300:
            return u[2] + '@parent', u[1]
        x = x.parentWidget()
    return 'qt:expose', ''


def _attr_rec(kind, widget, t0, extra=None):
    try:
        if not _ATTR['on']:
            return
        t = _attr_now()
        if t > _ATTR['end_ms']:
            return
        if len(_ATTR['ev']) >= 20000:
            _ATTR['drop'] += 1
            return
        reason, src = _attr_who(widget)
        _ATTR['ev'].append([round(t, 1), kind,
                            (widget.objectName() or type(widget).__name__),
                            round((time.perf_counter() - t0) * 1000.0, 2),
                            reason, src, extra])
    except Exception:
        _err()


def _attr_mark_evt(widget, reason):
    """resize/move/show 事件本身也记一笔，供 paint 归因与账目对齐。"""
    try:
        if not _ATTR['on']:
            return
        t = _attr_now()
        if t > _ATTR['end_ms']:
            return
        _ATTR['ev'].append([round(t, 1), 'evt',
                            (widget.objectName() or type(widget).__name__),
                            0.0, reason, type(widget).__name__, None])
    except Exception:
        _err()


def _attr_dump():
    if not _ATTR['on']:
        return None
    return {'window_ms': _ATTR['end_ms'],
            'upd_cost_ms': round(_ATTR['upd_cost_ms'], 2),
            'drop': _ATTR['drop'],
            'ev': _ATTR['ev'],
            'upd': _ATTR['upd']}


# R36-N2：逐帧取证状态（仅 `ftrace` 管道命令期间开启；默认关闭 = 无定时器、无过滤器）。
_FTRACE = {'on': False, 't0': 0.0}


def _ftrace_nav(idx, phase):
    if _FTRACE['on']:
        try:
            _FTRACE['nav'].append([round((time.perf_counter() - _FTRACE['t0']) * 1000, 1),
                                   int(idx), phase])
        except Exception:
            pass


def _ftrace_start(win, path, sec):
    """采样 sec 秒：16 ms 心跳间隔（事件循环是否被阻塞）+ 主窗 Paint 时间戳 + 切页标记。"""
    _FTRACE.update({'on': True, 't0': time.perf_counter(), 'nav': [], 'hb': [], 'paint': []})

    class _FP(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Paint:
                try:
                    # 顶层窗常被 central widget 全覆盖，自身 Paint 不来；
                    # 按「属于本窗的任意控件」计数，才等价于用户看到的帧。
                    if obj.window() is not win:
                        return False
                except Exception:
                    return False
                _FTRACE['paint'].append(
                    round((time.perf_counter() - _FTRACE['t0']) * 1000, 1))
            return False

    _fp = _FP()

    def _hb():
        if _FTRACE['on']:
            _FTRACE['hb'].append(round((time.perf_counter() - _FTRACE['t0']) * 1000, 1))

    _ft = QTimer()
    _ft.setInterval(16)
    _ft.timeout.connect(_hb)
    _tm = QTimer()
    _tm.setSingleShot(True)

    def _stop():
        _FTRACE['on'] = False
        _ft.stop()
        try:
            QApplication.instance().removeEventFilter(_fp)
        except Exception:
            pass
        _hb_ts = _FTRACE['hb']
        _iv = [round(_hb_ts[i] - _hb_ts[i - 1], 1) for i in range(1, len(_hb_ts))]
        _pv_ts = _FTRACE['paint']
        _piv = [round(_pv_ts[i] - _pv_ts[i - 1], 1) for i in range(1, len(_pv_ts))]
        _srt = sorted(_iv)
        _out = {
            'sec': sec,
            'hb_n': len(_iv),
            'hb_long_100': sum(1 for x in _iv if x > 100),
            'hb_long_250': sum(1 for x in _iv if x > 250),
            'hb_max': max(_iv) if _iv else 0,
            'hb_p95': _srt[max(0, int(len(_srt) * 0.95) - 1)] if _srt else 0,
            'paint_n': len(_piv),
            'paint_long_100': sum(1 for x in _piv if x > 100),
            'paint_long_250': sum(1 for x in _piv if x > 250),
            'paint_max': max(_piv) if _piv else 0,
            'hb': _hb_ts,
            'paint': _pv_ts,
            'nav': _FTRACE['nav'],
        }
        try:
            with open(path, 'w', encoding='utf-8') as _f:
                json.dump(_out, _f, ensure_ascii=False)
        except Exception:
            _err()

    _tm.timeout.connect(_stop)
    QApplication.instance().installEventFilter(_fp)
    _ft.start()
    _tm.start(int(float(sec) * 1000))
    _FTRACE['keep'] = (_fp, _ft, _tm)   # 防 GC：过滤器/定时器必须活到 _stop


# R36-N3：托盘「静音通知」门——三处完成通知统一受它拦截，默认不静音。
# 开关对象收在 infra/config.py，这里沿用模块内原名 _XH_MUTE。


def _frame_tick_probe():
    _now = time.perf_counter()
    _dt = (_now - _frame_probe['last']) * 1000 if _frame_probe['last'] else 0.0
    _frame_probe['last'] = _now
    if _dt <= 0:
        return
    _frame_probe['n'] += 1
    _frame_probe['sum'] += _dt
    _frame_probe['max'] = max(_frame_probe['max'], _dt)
    if XH_PROFILE and _frame_probe['n'] % 60 == 0:
        try:
            with open(XH_PROFILE, 'a', encoding='utf-8') as _fh:
                _fh.write('frame n=%d avg=%.1fms max=%.1fms\n'
                          % (_frame_probe['n'],
                             _frame_probe['sum'] / _frame_probe['n'],
                             _frame_probe['max']))
        except Exception:
            _err()

# R12（XH-081）：真实重绘归因。XH_PROFILE 的 frame 心跳本身是 16ms 定时器，
# 只能说明事件循环转速，不等于「谁在画」。这里用事件过滤器统计 QEvent.Paint
# 的按类次数（只计数，不改绘制），每 10s 落一行累计值；不设 XH_PAINT 时零开销。
def _install_paint_spy(app, path):
    try:
        from collections import Counter
        cnt = Counter()
        area = {}
        who = {}        # R20：桶 → 首次见到的「父链」快照（定位无 objectName 的 QWidget）

        class _PaintSpy(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Paint:
                    try:
                        # R15：按「类名#objectName」分桶——R12/R14 只按类名，无法区分
                        # 同类的不同控件（如 4 个 FlowProgressBar、多个 QLabel）。
                        k = type(obj).__name__
                        try:
                            _on = obj.objectName() or getattr(obj, '_xname', '') or ''
                        except Exception:
                            _on = ''
                        if _on:
                            k = k + '#' + _on
                        # R20：_band_capture 用 QWidget.render() 把子控件画进离屏 pixmap，
                        # 这里的 Paint 也来自离屏——单独记 R: 前缀，免得把它算成屏上重绘。
                        if _BAND_BUSY[0]:
                            k = 'R:' + k
                        # R20-1（通行证）：在「真画的时候」读回两个标记，按 True/False 分桶。
                        # 若某级显示 o=0，就是标记在 StyleChange/主题切换后被打掉，
                        # 而不是「标记无效」。只读属性，不改绘制。
                        try:
                            k = '%s|o%d|n%d' % (k,
                                               1 if obj.testAttribute(Qt.WA_OpaquePaintEvent) else 0,
                                               1 if obj.testAttribute(Qt.WA_NoSystemBackground) else 0)
                        except Exception:
                            _err()
                        cnt[k] += 1
                        if k not in who:
                            try:
                                _c = []
                                _x = obj
                                for _ in range(5):
                                    if _x is None:
                                        break
                                    _nm = ''
                                    try:
                                        _nm = _x.objectName()
                                    except Exception:
                                        _err()
                                    _c.append('%s#%s@%dx%d'
                                              % (type(_x).__name__, _nm,
                                                 _x.width(), _x.height()))
                                    _x = _x.parentWidget()
                                who[k] = ' <- '.join(_c)
                            except Exception:
                                who[k] = '?'
                        reg = ev.region()
                        s = 0.0
                        mx = 0.0
                        n = 0
                        for r in reg:
                            a = r.width() * r.height()
                            s += a
                            mx = max(mx, a)
                            n += 1
                        _s = area.setdefault(k, [0.0, 0.0, 0.0])
                        _s[0] += s
                        _s[1] = max(_s[1], mx)
                        _s[2] += n
                    except Exception:
                        _err()
                return False

        spy = _PaintSpy(app)
        app.installEventFilter(spy)
        app._paint_spy = spy  # 防 GC

        _pt = QTimer(app)
        # R20-9：dump 间隔可调（默认 10 s；不可见三态用 1 s 取得相位对齐的 paint 增量）
        _pt.setInterval(int(os.environ.get('XH_PAINT_MS', '10000') or 10000))

        def _dump():
            try:
                d = dict(cnt)
                d['__ts__'] = time.time()
                try:
                    d['__tid__'] = int(ctypes.windll.kernel32.GetCurrentThreadId())
                except Exception:
                    _err()
                try:
                    d['__area__'] = {k: [int(v[0]), int(v[1]), int(v[2])]
                                     for k, v in area.items()}
                except Exception:
                    _err()
                try:
                    d['__deco__'] = _deco_diag()
                except Exception as e:
                    d['__deco__'] = {'err': str(e)}
                try:
                    d['__who__'] = dict(who)
                except Exception:
                    _err()
                with open(path, 'a', encoding='utf-8') as f:
                    f.write('paint %s\n' % json.dumps(d, ensure_ascii=False))
            except Exception:
                _err()

        _pt.timeout.connect(_dump)
        _pt.start()
        app._paint_timer = _pt
    except Exception:
        _err()


# R16 归因（仅 XH_TSTAT / XH_STACK 非空时启用，出货零开销）：
#   XH_TSTAT=<log>  每 10 s 枚举全部 QTimer 子对象：interval / active / 触发次数
#   XH_STACK=<log>  主线程栈采样（默认 120 Hz），按最内 5 帧聚合，看 CPU 落在哪个回调
XH_TSTAT = os.environ.get('XH_TSTAT', '')
XH_STACK = os.environ.get('XH_STACK', '')
# 成本日志开关：已收进 infra/config.py（记录每次轮询的工作线程/UI 回调耗时）。


def _cost(tag, t0):
    """R20：同时记「墙钟」与「本线程 CPU」+ 时间戳。
    t0 = (perf_counter, thread_time)；R17 只记墙钟，会把「等 socket」误当成本，
    这里加 CPU 列才能回答「每项 X 次 × 单次 CPU 贡献」。"""
    if not XH_COST:
        return
    try:
        _w = (time.perf_counter() - t0[0]) * 1000.0
        _c = (time.thread_time() - t0[1]) * 1000.0
        with open(XH_COST, 'a', encoding='utf-8') as f:
            f.write('%s\t%.2f\t%.2f\t%.2f\n' % (tag, _w, _c, time.time()))
    except Exception:
        _err()


def _install_timer_stat(app, path):
    """XH_TSTAT：定时器分布取证。paint 已接近 0 时，用它找「谁在被反复唤醒」。"""
    try:
        rec = {}

        def _bump(k):
            e = rec.get(k)
            if e is not None:
                e['fires'] += 1

        def _scan():
            now = time.perf_counter()
            for w in [app] + list(app.allWidgets()):
                try:
                    tms = w.findChildren(QTimer)
                except Exception:
                    _err()
                    continue
                for tm in tms:
                    k = id(tm)
                    e = rec.get(k)
                    if e is None:
                        try:
                            own = type(tm.parent()).__name__ if tm.parent() else '?'
                        except Exception:
                            own = '?'
                        e = {'fires': 0, 'owner': own, 'last': 0}
                        rec[k] = e
                        try:
                            tm.timeout.connect(lambda _k=k: _bump(_k))
                        except Exception:
                            _err()
                    try:
                        e['interval'] = tm.interval()
                        e['active'] = tm.isActive()
                        e['single'] = tm.isSingleShot()
                    except Exception:
                        _err()
            rows = []
            for k, e in rec.items():
                d = e['fires'] - e['last']
                e['last'] = e['fires']
                rows.append({'owner': e['owner'], 'interval': e.get('interval'),
                             'active': e.get('active'), 'single': e.get('single'),
                             'fires_10s': d, 'per_s': round(d / 10.0, 2)})
            rows.sort(key=lambda r: -r['fires_10s'])
            try:
                with open(path, 'a', encoding='utf-8') as f:
                    f.write('tstat %s\n' % json.dumps(
                        {'n': len(rec), 'rows': rows[:30]}, ensure_ascii=False))
            except Exception:
                _err()

        _tt = QTimer(app)
        _tt.setInterval(10000)
        _tt.timeout.connect(_scan)
        _tt.start()
        app._tstat_timer = _tt
    except Exception:
        _err()


def _install_stack_sampler(app, path, hz=120):
    """XH_STACK：主线程 Python 栈采样。Qt 内部时间会落在外层 exec_，Python 回调会露出名字。"""
    try:
        import collections
        cnt = collections.Counter()
        main_id = threading.get_ident()

        def _loop():
            iv = 1.0 / hz
            while True:
                f = sys._current_frames().get(main_id)
                if f is not None:
                    names = []
                    fr = f
                    while fr is not None and len(names) < 5:
                        co = fr.f_code
                        names.append('%s@%s:%d' % (co.co_name,
                                                   os.path.basename(co.co_filename),
                                                   fr.f_lineno))
                        fr = fr.f_back
                    cnt[' < '.join(names)] += 1
                time.sleep(iv)

        threading.Thread(target=_loop, daemon=True).start()

        def _dump():
            tot = sum(cnt.values()) or 1
            try:
                with open(path, 'a', encoding='utf-8') as fh:
                    fh.write('stack %s\n' % json.dumps(
                        {'samples': tot,
                         'top': [{'k': k, 'n': v, 'pct': round(v * 100.0 / tot, 1)}
                                 for k, v in cnt.most_common(20)]},
                        ensure_ascii=False))
            except Exception:
                _err()
            cnt.clear()

        _st = QTimer(app)
        _st.setInterval(10000)
        _st.timeout.connect(_dump)
        _st.start()
        app._stack_timer = _st
    except Exception:
        _err()


from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsItem,
    QGraphicsOpacityEffect, QGraphicsPixmapItem, QGraphicsScene, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QStyle, QSystemTrayIcon, QTabWidget, QTableWidget,
    QHeaderView,
    QStyledItemDelegate, QListView, QTextBrowser, QToolTip, QVBoxLayout,
    QWidget)


# R15-S03：跨线程 GUI 桥——工作线程不得直调 UI/托盘（Qt 跨线程未定义行为），
# 一律经主线程事件队列执行；替换散落的裸 setText/setPixmap/setEnabled 调用。
class _ThreadBridge(QObject):
    run = Signal(object)


_bridge = _ThreadBridge()
_bridge.run.connect(lambda f: f())

HOST = '127.0.0.1'
PANEL_PORT = 8899
PANEL_TITLE = ux.PRODUCT_NAME
# X18：CANONICAL_DIR 先按源码相对位置推导（本文件所在目录及其 backend 子目录），
# 都不含 panel-server.py 时退回本文件所在目录；XH_PANEL_ROOT 可显式指定。
APP_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_canonical_candidates = (
    os.environ.get('XH_PANEL_ROOT') or '',
    APP_SRC_DIR,
    os.path.join(APP_SRC_DIR, 'backend'),
)
CANONICAL_DIR = next(
    (c for c in _canonical_candidates
     if c and os.path.exists(os.path.join(c, 'panel-server.py'))),
    APP_SRC_DIR)
import pathcfg as _pathcfg  # noqa: E402  本机路径配置（见 pathcfg.py）

# R38-2 S1：UI 颜色令牌（29 个）。值 = 当前渲染值，本步只改名不改色。
# 65 色 → 29 令牌的完整映射（含 36 个待并色）。
# R38-9：S2 把 INK_3 收到 #64748B 后，在卡片实测底 #22334F 上只有 2.67:1（12px 需 4.5、
# 18px 需 3.0），按「不达标退回上一档」退回并色前代表色 #7F93AD（12px 4.82、18px 4.04）。
UI_C = {
    'INK_0': '#E8F4FF', 'INK_1': '#CFE0F2', 'INK_2': '#9FB6CC', 'INK_3': '#7F93AD',
    'INK_CODE': '#B8C8B0',
    'ACCENT': '#38E2FF', 'ACCENT_2': '#9B6BFF', 'ACCENT_SOFT': '#9FE8FF',
    'ACCENT_INK': '#04121F', 'ACCENT_3': '#FF6BC4',
    'OK': '#9CAF88', 'OK_DIM': '#8AA078', 'WARN': '#D4B896', 'WARN_2': '#C9A87C',
    'ERR': '#C29696', 'DANGER': '#EF6A5D', 'INFO': '#7FA7D8', 'IDLE': '#8A99A8',
    'BG_ROOT': '#06090D', 'BG_RAISED': '#202834', 'BG_POPUP': '#222C3A',
    'STAGE_1': '#0A1424', 'STAGE_2': '#0C1A30', 'STAGE_3': '#0A1226',
    'SERIES_MEM': '#9B88AF', 'SERIES_CPU': '#8FB0B0',
    'THEME_HC_BG': '#0C0C0C', 'THEME_EYE_BG': '#141414', 'THEME_EYE_FG': '#DFE7D8',
}


def _t(s):
    """样式串里的 @TOKEN@ → 实际色值（只做等值替换，不做 % 格式化）。"""
    if '@' not in s:
        return s
    for _k, _v in UI_C.items():
        s = s.replace('@' + _k + '@', _v)
    return s


# R247：主题从「两条 QLabel 规则」改为真正的五类令牌（背景/卡片/导航/页脚/正文）。
# 原版深色不在这张表里——None 即不做任何覆盖，保持既有渲染逐像素不变。
THEME_KIT = {
    '高对比': {
        'BG': '#000000', 'INK': '#FFFFFF', 'INK_1': '#F5F5F5', 'INK_2': '#E8E8E8',
        'INK_3': '#CFCFCF', 'SUB': '#BDBDBD',
        'ACCENT': '#FFD400', 'ACCENT_INK': '#000000',
        'CARD_TOP': (8, 8, 8, 250), 'CARD_BOT': (0, 0, 0, 252),
        'CARD_INK': '#FFFFFF', 'CARD_BD': (255, 255, 255, 130),
        'NAV_BG': 'rgba(0,0,0,0.92)', 'NAV_BD': 'rgba(255,255,255,0.60)',
        'FOOT_BG': '#1B1B1B', 'FOOT_BD': 'rgba(255,212,0,0.85)',
        'PANE': '#050505', 'PANE_BD': 'rgba(255,255,255,0.38)',
        'INPUT': '#0A0A0A', 'INPUT_BD': 'rgba(255,255,255,0.55)',
        'BTN': '#141414', 'BTN_H': '#2C2C2C', 'BTN_BD': 'rgba(255,255,255,0.60)',
        'TAB': '#0A0A0A', 'TAB_ON': '#242424',
        'TABLE': '#080808', 'HEAD': '#1C1C1C',
        'SB': 'rgba(255,255,255,0.42)', 'POPUP': '#0F0F0F',
    },
    '暗夜护眼': {
        'BG': '#0B120E', 'INK': '#DFE7D8', 'INK_1': '#D2DCC9', 'INK_2': '#B7C3AC',
        'INK_3': '#9AA791', 'SUB': '#9AA791',
        'ACCENT': '#9CAF88', 'ACCENT_INK': '#0B120E',
        'CARD_TOP': (20, 30, 23, 240), 'CARD_BOT': (10, 17, 13, 244),
        'CARD_INK': '#E4EBDD', 'CARD_BD': (150, 170, 140, 96),
        'NAV_BG': 'rgba(12,20,15,0.92)', 'NAV_BD': 'rgba(160,180,150,0.32)',
        'FOOT_BG': '#1A2A20', 'FOOT_BD': 'rgba(156,175,136,0.78)',
        'PANE': '#0C1410', 'PANE_BD': 'rgba(160,180,150,0.24)',
        'INPUT': '#0E1712', 'INPUT_BD': 'rgba(160,180,150,0.38)',
        'BTN': '#16211A', 'BTN_H': '#1F2D23', 'BTN_BD': 'rgba(160,180,150,0.38)',
        'TAB': '#0E1712', 'TAB_ON': '#1C2820',
        'TABLE': '#0A110D', 'HEAD': '#16211A',
        'SB': 'rgba(160,180,150,0.38)', 'POPUP': '#101A14',
    },
}

_THEME_QSS = '''
QWidget#root { background: %(BG)s; }
QLabel { color: %(INK)s; }
QLabel#sub { color: %(SUB)s; }
QLabel#navbrand { color: %(INK)s; }
QFrame#navrail { background: %(NAV_BG)s; border: 1px solid %(NAV_BD)s; border-radius: 14px; }
QPushButton#navitem { color: %(INK_2)s; }
QPushButton#navitem:hover { background: %(BTN_H)s; color: %(INK)s; }
QPushButton#navitem:checked { background: %(ACCENT)s; color: %(ACCENT_INK)s; }
QFrame#xhfooter { background: %(FOOT_BG)s; border: 1px solid %(FOOT_BD)s; border-radius: 14px; }
QGroupBox { color: %(INK)s; }
QGroupBox::title { color: %(INK)s; }
QPushButton { background: %(BTN)s; color: %(INK)s; border: 1px solid %(BTN_BD)s; border-radius: 8px; }
QPushButton:hover { background: %(BTN_H)s; }
QPushButton:checked { background: %(ACCENT)s; color: %(ACCENT_INK)s; }
QPushButton:disabled { color: %(INK_3)s; background: %(PANE)s; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox,
QDoubleSpinBox, QDateEdit, QTimeEdit {
    background: %(INPUT)s; color: %(INK)s; border: 1px solid %(INPUT_BD)s; border-radius: 8px;
}
QComboBox QAbstractItemView { background: %(POPUP)s; color: %(INK)s; border: 1px solid %(INPUT_BD)s; }
QTabWidget::pane { background: %(PANE)s; border: 1px solid %(PANE_BD)s; border-radius: 14px; }
QTableWidget, QTableView, QListWidget, QTreeWidget { background: %(TABLE)s; color: %(INK)s;
    border: 1px solid %(PANE_BD)s; }
QHeaderView::section { background: %(HEAD)s; color: %(INK)s; border: none; }
QCheckBox, QRadioButton { color: %(INK)s; }
QCheckBox::indicator, QRadioButton::indicator { background: %(INPUT)s; border: 1px solid %(INPUT_BD)s; }
QScrollBar:vertical, QScrollBar:horizontal { background: %(PANE)s; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: %(SB)s; border-radius: 4px; }
QMenu { background: %(POPUP)s; color: %(INK)s; border: 1px solid %(PANE_BD)s; }
QToolTip { background: %(POPUP)s; color: %(INK)s; border: 1px solid %(PANE_BD)s; }
QStatusBar { background: %(FOOT_BG)s; color: %(INK_2)s; }
'''


def _theme_qss(tk):
    # R248：原版深色不再“清空窗口 QSS”，而是把应用级 DARK_QSS 原样下到窗口。
    # 清空会让 Qt 保留主题期的 tab 度量（首项 63→62、整条右移/左移 1 px），
    # 往返 diff_px≠0；显式下发同一份样式表后，两态走同一条解析路径。
    return (_THEME_QSS % tk) if tk else DARK_QSS


def _tab_qss(tk):
    """R248：Tab 一律用「显式 widget 级 QSS」上色，两种状态都下发同一条排版声明，
    只换颜色。此前 tab 走窗口级 QSS，切回原版时 Qt 缓存了主题期的 tab 度量，
    首项宽度 63→62，整条 tab 文字左移 1 px，往返 diff_px≠0。"""
    if tk:
        return ('QTabBar::tab { background: %s; color: %s; padding: 7px 16px;'
                ' margin-right: 4px; border-radius: 8px; border-bottom: 2px solid transparent;'
                ' font-size: 12px; font-weight: 500; min-width: 27px; }'
                'QTabBar::tab:selected { background: %s; color: %s;'
                ' border-bottom: 2px solid %s; font-weight: 600; }'
                % (tk['TAB'], tk['INK_2'], tk['TAB_ON'], tk['INK'], tk['ACCENT']))
    return ('QTabBar::tab { background: transparent; color: %s; padding: 7px 16px;'
            ' margin-right: 4px; border-radius: 8px; border-bottom: 2px solid transparent;'
            ' font-size: 12px; font-weight: 500; min-width: 27px; }'
            'QTabBar::tab:hover { color: %s; background: rgba(120,190,255,0.10); }'
            'QTabBar::tab:selected { color: %s; background: rgba(56,226,255,0.12);'
            ' border-bottom: 2px solid %s; font-weight: 600; }'
            % (UI_C['INK_2'], UI_C['INK_1'], UI_C['ACCENT_SOFT'], UI_C['ACCENT']))


def _xh_apply_theme(win, theme):
    """R247：把主题令牌真正落到窗口——QSS 覆盖背景/正文/导航/页脚/输入，
    再让两处自绘层（Backdrop 烘焙图、GlassCard 缓存）失效重绘。
    原版深色 → 空 QSS + 恢复自带样式，逐像素回到既有渲染。"""
    tk = THEME_KIT.get(theme)
    XH_ADV['theme'] = theme
    try:
        win.setStyleSheet(_theme_qss(tk))
    except Exception:
        _err()
    try:
        _ft = win.findChild(QFrame, 'xhfooter')
        if _ft is not None:
            _ft.setStyleSheet(
                'QFrame#xhfooter{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,'
                'stop:0 rgba(30,46,66,140),stop:1 rgba(40,34,58,128));'
                'border:1px solid rgba(255,255,255,0.12); border-radius:14px;}'
                if tk is None else
                'QFrame#xhfooter{background:%s;border:1px solid %s;border-radius:14px;}'
                % (tk['FOOT_BG'], tk['FOOT_BD']))
    except Exception:
        _err()
    try:
        # 页脚行是裸 QWidget（无背景），主题态下补一层真实底色
        for _pf in win.findChildren(QWidget, 'pagefoot'):
            _pf.setAttribute(Qt.WA_StyledBackground, True)
            _pf.setStyleSheet('' if tk is None else
                              'background:%s; border-top:1px solid %s;'
                              % (tk['FOOT_BG'], tk['FOOT_BD']))
    except Exception:
        _err()
    try:
        # R248：Tab 用 widget 级 QSS 上色（两种状态都下发同一份排版声明），
        # 避免窗口级 QSS 切换后 Qt 缓存 tab 度量导致往返偏 1 px。
        for _tw in win.findChildren(QTabWidget):
            _tb = _tw.tabBar()
            if _tb is not None:
                _tb.setStyleSheet('')          # 先清，强制丢掉主题期缓存的 tab 度量
                _tb.setStyleSheet(_tab_qss(tk))
                _tb.style().unpolish(_tb)
                _tb.style().polish(_tb)
                _tb.updateGeometry()
    except Exception:
        _err()
    try:
        if BACKDROP is not None:
            # 不清烘焙图：星虹图是随机星点，重烘会让「切回原版」与原版不是同一张。
            # 主题态 paintEvent 直接铺底色，不读这张图；切回原版时原图还在。
            BACKDROP.update()
        for _gc in win.findChildren(GlassCard):
            _gc._invalidate()
        win.update()
    except Exception:
        _err()
    return theme

# R024-A5（领导）：液态玻璃对齐 DS-Whalegirl——青/紫极光 + 深海军蓝玻璃
ACCENT = UI_C['ACCENT']   # Whaleglass 青（主强调）
ACCENT2 = UI_C['ACCENT_2']  # Whaleglass 紫（次强调）
TXT = UI_C['INK_0']      # 玻璃墨色
SUB = UI_C['INK_2']      # 次级墨
# R19：子孙页统一令牌——圆角/卡片底色/标题色只从这里取，禁止各页自写 10/14/18
R_CARD = 14          # 外卡片（与 QTabWidget::pane 同源；R22 统一 12/18→14）
R_INNER = 8          # 卡片内子块
TXT3 = UI_C['INK_2']     # 卡片标题
TXT2 = UI_C['INK_1']     # 正文强调
TXT4 = UI_C['INK_3']     # 弱提示
CARD_BG = 'rgba(255,255,255,0.045)'
CARD_BD = 'rgba(255,255,255,0.08)'
STAGE_BG = UI_C['BG_ROOT']  # 杰作舞台底
MORANDI_GREEN = UI_C['OK']  # 成功态绿（保留语义色）
MORANDI_PINK = UI_C['ACCENT_3']   # Whaleglass 粉

# R38-16 S6：滚动条策略收口——全工程只允许引用这三个常量，禁止再散写 set*ScrollBarPolicy
#   SCROLL_V  ：日志/表格，内容溢出才出竖条（QSS 已给 8 px 细条）
#   SCROLL_H  ：横向一律关，避免破坏列对齐
#   SCROLL_OFF：自绘网格（双列墙 L2756 / 模型墙 L5865）两向都关，是设计选择不是漏开
SCROLL_V = Qt.ScrollBarAsNeeded
SCROLL_H = Qt.ScrollBarAlwaysOff
SCROLL_OFF = Qt.ScrollBarAlwaysOff

# KVMem 模型卡标签配色（对齐极光双青紫）
KV_TAG_COLOR = {'flash64': UI_C['ACCENT'], 'qwen9bq4': UI_C['OK']}

# 全局背景实例（GlassCard 从此取模糊背景，实现真正的 backdrop-filter）
BACKDROP = None

# G2 统一动画节奏 token（round_013）：数字 520ms、卡片 320ms、呼吸 2400ms
DUR_FAST = 160
DUR_MED = 320
DUR_NUM = 520
DUR_SLOW = 2400


def aligned_button(text, kind='primary', min_w=96, h=34, parent=None):
    """G1 统一按钮工厂：同组等高、同最小宽、圆角9、字号13、左右内边距14。

    kind: primary(主题渐变)/ghost(透明描边)/danger(低饱和红)。
    """
    b = QPushButton(text, parent)
    b.setFixedHeight(h)
    b.setMinimumWidth(min_w)
    base = ('QPushButton{font-size:12px; padding:0 14px; border-radius:8px;'
            'font-weight:500; text-align:center;}')
    if kind == 'ghost':
        base += (_t('QPushButton{background:rgba(255,255,255,0.06); color:@INK_0@;'
                 'border:1px solid rgba(255,255,255,0.14);}'
                 'QPushButton:hover{background:rgba(255,255,255,0.12);'
                 'border-color:rgba(56,226,255,0.45); color:@INK_1@;}'))
    elif kind == 'danger':
        base += (_t('QPushButton{background:rgba(219,68,55,0.15); color:@DANGER@;'
                 'border:1px solid rgba(219,68,55,0.30);}'
                 'QPushButton:hover{background:rgba(219,68,55,0.27);}'))
    else:
        base += (_t('QPushButton{background:rgba(56,226,255,0.15); color:@INK_1@;'
                 'border:1px solid rgba(56,226,255,0.30);}'
                 'QPushButton:hover{background:rgba(56,226,255,0.26);'
                 'border-color:rgba(170,190,220,0.55); color:@INK_1@;}'))
    b.setStyleSheet(base)
    return b


class _DaemonPool(ThreadPoolExecutor):
    """线程池但工作线程为 daemon：避免 App 退出后非守护线程挂住进程。"""

    def _adjust_thread_count(self):
        import concurrent.futures.thread as _cft
        import weakref
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num = len(self._threads)
        if num < self._max_workers:
            name = '%s_%d' % (self._thread_name_prefix or self, num)
            t = threading.Thread(
                name=name, target=_cft._worker,
                args=(weakref.ref(self, weakref_cb),
                      self._create_worker_context(),
                      self._work_queue))
            t.daemon = True
            t.start()
            self._threads.add(t)
            _cft._threads_queues[t] = self._work_queue


# 共享后台线程池：所有轮询/缩略图/首帧都走这里，避免每任务新建线程的开销风暴
# R10（thumb3）：稳态下把并发限到 3，避免 24 张墙图同时解码抢主线程/IO
WORKER_POOL = _DaemonPool(max_workers=3 if 'thumb3' in R10 else 8,
                          thread_name_prefix='xh')
# R10（XH-067）：用户动作（删除）用独立小池，避免排在缩略图解码队尾（thumb3 下实测会延迟）
ACTION_POOL = _DaemonPool(max_workers=2, thread_name_prefix='xha')


# ============================================================
# 动画基础设施（对齐网页版 keyframes：fadeUp / breath / modalIn /
# ebNum / shimmer / tnIn）。所有动画对象都挂在控件自身上防止 GC。
# ============================================================
def _drop_shadow(widget, color=QColor(80, 120, 255, 60)):
    sh = QGraphicsDropShadowEffect(widget)
    sh.setBlurRadius(24)
    sh.setOffset(0, 5)
    sh.setColor(color)
    widget.setGraphicsEffect(sh)
    return sh


def fit_pixmap(pm, size):
    """R13（P1-6）：按原比例留白（letterbox）——不裁用户构图，方格几何不变。"""
    FIT_STAT['fit'] += 1
    src = pm
    if src.isNull():
        return src
    w, h = src.width(), src.height()
    if w <= 0 or h <= 0:
        return src
    if w == h:
        return src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if w > h:
        nw = size
        nh = max(1, int(round(h * size / float(w))))
    else:
        nh = size
        nw = max(1, int(round(w * size / float(h))))
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawPixmap(int((size - nw) / 2), int((size - nh) / 2),
                 src.scaled(nw, nh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    p.end()
    return out


def cover_pixmap(pm, size):
    """等比放大并居中裁剪到 size×size（CSS object-fit:cover），
    固定方格缩略图，避免 KeepAspectRatio 留空/错位。"""
    try:
        if pm is None or pm.isNull() or size <= 0:
            return QPixmap()
        scaled = pm.scaled(int(size), int(size),
                           Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
        x = max(0, (scaled.width() - int(size)) // 2)
        y = max(0, (scaled.height() - int(size)) // 2)
        return scaled.copy(x, y, int(size), int(size))
    except Exception:
        return QPixmap()


# R14：缩放结果缓存。同一 (文件名, mtime, 方格边长, 模式) 只做一次平滑缩放；
# 命中与否像素完全一致（同一函数、同一输入），尺寸变化时 key 里的 size 自动失效。
_ICON_CACHE = {}
_ICON_KEYS = []


def wall_icon(name, mtime, src, size, letterbox):
    key = (name, mtime, int(size), 1 if letterbox else 0)
    pm = _ICON_CACHE.get(key)
    if pm is not None:
        FIT_STAT['icache'] += 1
        return pm
    pm = (fit_pixmap(QPixmap.fromImage(src), int(size))
          if letterbox else cover_pixmap(QPixmap.fromImage(src), int(size)))
    _ICON_CACHE[key] = pm
    _ICON_KEYS.append(key)
    while len(_ICON_KEYS) > 160:
        _ICON_CACHE.pop(_ICON_KEYS.pop(0), None)
    return pm


def _cover_canvas(size, radius=12):
    """R22：三种占位封面统一底——海军蓝玻璃渐变 + 青色细描边 + 12px 圆角。"""
    pm = QPixmap(int(size), int(size))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(0.5, 0.5, int(size) - 1, int(size) - 1)
    g = QLinearGradient(0, 0, int(size), int(size))
    g.setColorAt(0.0, QColor(16, 28, 54, 215))
    g.setColorAt(1.0, QColor(8, 16, 36, 225))
    p.setBrush(g)
    p.setPen(QPen(QColor(120, 190, 255, 70), 1.0))
    p.drawRoundedRect(r, radius, radius)
    return pm, p, r


def make_missing_cover(size):
    pm, p, r = _cover_canvas(size)
    p.setPen(QColor(148, 163, 184, 190))
    f = p.font()
    f.setPixelSize(max(11, int(size * 0.15)))
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter, '海报缺失')
    p.end()
    return pm


# R12（XH-082）：超大图不再整图解码（102MB PNG 解码峰值约 500MB，会长期停在
# 「加载中」）。给明确占位说明，用户仍可双击/打开目录查看原图。
def make_oversize_cover(size, mb):
    pm, p, r = _cover_canvas(size)
    p.setPen(QColor(185, 203, 224, 210))
    f = p.font()
    f.setPixelSize(max(9, int(size * 0.095)))
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter,
               '图片过大\n约 %d MB\n暂不生成预览\n可打开目录查看' % int(mb))
    p.end()
    return pm


# X16：视频再失败用「文件名首字 + 渐变封面」标注「无预览」
def make_no_preview_cover(text, size):
    pm, p, r = _cover_canvas(size)
    ch = (str(text) or '?')[:1]
    p.setPen(QColor(219, 231, 245, 220))
    f = p.font()
    f.setPixelSize(int(size * 0.42))
    f.setBold(True)
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter, ch)
    f2 = p.font()
    f2.setPixelSize(max(9, int(size * 0.14)))
    f2.setBold(False)
    p.setFont(f2)
    p.setPen(QColor(143, 169, 204, 220))
    p.drawText(QRectF(0, int(size * 0.70), int(size), int(size * 0.26)),
               Qt.AlignCenter, '无预览')
    p.end()
    return pm


def _attach_empty_state(lst, text='暂无作品\n生成完成后会出现在这里'):
    """R22（缺图 A2）：宫格 0 条时显示统一空态；有数据时自动隐藏。"""
    _empty = QLabel(text, lst.viewport())
    _empty.setObjectName('sub')
    _empty.setAlignment(Qt.AlignCenter)
    _empty.setStyleSheet(_t('color:@INK_3@; font-size:12px; background:transparent;'))
    _empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    _empty.hide()

    def _sync_empty(*_a):
        _empty.setGeometry(lst.viewport().rect())
        _empty.setVisible(lst.count() == 0)

    class _EmptySync(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() in (QEvent.Resize, QEvent.Show):
                _sync_empty()
            return False

    _es = _EmptySync(lst)
    lst.viewport().installEventFilter(_es)
    lst.model().rowsInserted.connect(_sync_empty)
    lst.model().rowsRemoved.connect(_sync_empty)
    lst._empty_es = _es
    lst._empty_lbl = _empty
    _sync_empty()
    return _empty


def make_xh_footer(app_name, version):
    """X.H 平台声明条（导航底部空白区域），参考 DS-Whalegirl guide-footer。"""
    _perf_mark('hf_b')
    fw = QFrame()
    fw.setObjectName('xhfooter')
    fw.setStyleSheet(
        'QFrame#xhfooter{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,'
        'stop:0 rgba(30,46,66,140),stop:1 rgba(40,34,58,128));'
        'border:1px solid rgba(255,255,255,0.12); border-radius:14px;}')
    _perf_mark('hf_ss_e')
    fv = QVBoxLayout(fw)
    fv.setContentsMargins(8, 8, 8, 8)
    fv.setSpacing(4)
    l1 = QLabel()
    l1.setTextFormat(Qt.RichText)
    l1.setText(_t('<span style="color:@INK_1@;">Developed by </span>'
               '<span style="color:@INK_2@; font-weight:700;">X.H</span>'))
    _perf_mark('hf_l1_e')
    l1.setAlignment(Qt.AlignCenter)
    l1.setStyleSheet('font-size:12px; background:transparent;')   # R25-W5：11→12
    l2 = QLabel('%s v%s · MIT License' % (app_name, version))
    l2.setStyleSheet('color:%s; font-size:11px; background:transparent;' % TXT3)  # R25-W5：10→11 + token
    l2.setAlignment(Qt.AlignCenter)
    l2.setWordWrap(True)
    fv.addWidget(l1)
    fv.addWidget(l2)
    _perf_mark('hf_e')
    return fw


def fade_in(widget, duration=260, delay=0, start_opacity=0.0,
            restore_shadow=False):
    """控件淡入（对应网页 .fadein / fadeUp）。结束后移除透明度特效。
    R007/R008 动效纪律：不再为卡片挂常驻 QGraphicsDropShadowEffect——
    卡内任一子控件重绘都会令整卡重进 FBO 重跑模糊（restore_shadow 参数保留兼容旧调用，已失效）。"""
    old = getattr(widget, '_fade_anim', None)
    if old is not None:
        try:
            old.stop()
        except Exception:
            _err()
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(start_opacity)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b'opacity', widget)
    anim.setStartValue(start_opacity)
    anim.setEndValue(1.0)
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def cleanup():
        if widget.graphicsEffect() is eff:
            widget.setGraphicsEffect(None)
        # R254：只有动画期间真的用旧缓存拉伸过，才在结束后重画一帧；
        # 没拉伸过就不失效，避免每次淡入结尾多一次整卡重建。
        if getattr(widget, '_anim_stretched', False):
            widget._anim_stretched = False
            _inv = getattr(widget, '_invalidate', None)
            if _inv is not None:
                try:
                    _inv()
                except Exception:
                    _err()

    anim.finished.connect(cleanup)
    if delay:
        QTimer.singleShot(delay, anim.start)
    else:
        anim.start()
    widget._fade_anim = anim
    widget._fade_eff = eff
    return anim


def pop_in(widget, duration=300, delay=0):
    """弹窗/对话框弹入（对应网页 modalIn：scale+opacity）。结束后移除透明度特效。"""
    old = getattr(widget, '_pop_anim', None)
    if old is not None:
        try:
            old.stop()
        except Exception:
            _err()
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b'opacity', widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.OutBack)

    def cleanup():
        if widget.graphicsEffect() is eff:
            widget.setGraphicsEffect(None)

    anim.finished.connect(cleanup)
    if delay:
        QTimer.singleShot(delay, anim.start)
    else:
        anim.start()
    widget._pop_anim = anim
    widget._pop_eff = eff
    return anim


class AnimatedNumberLabel(QLabel):
    """数值平滑变化（对应网页 ebNum：数字跳动）。

    set_target(value, fmt) 会从当前显示值平滑插值到 value。
    """
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self._shown = text
        self._ghost = ''
        self._cur = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)      # R02：520→300 ms，降低每秒重绘占空
        # R20-2：QVariantAnimation 由 16ms 动画驱动 → 一次 300ms 缓动 ~19 帧
        # （bucket 8.45/s）。改为下面 `_num_tick` 的共享 150ms 定时器驱动：
        # 一次缓动只出 2 帧，速率 ≤6.7/s·条，且与流光/呼吸点同一拍。
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._fmt = lambda v: '%.1f' % v
        self._pulse = False
        self._pulse_color = QColor(206, 220, 240)
        self._pulse_on = False
        self._pulse_anim = None
        # R23-B3：首帧（未拿到数据）按空值权重呈现，不再冒充一个 30px 的真实读数
        self._empty = True
        self._compact = False
        self._apply_num_style()
        self.setText('暂无数据')

    def set_formatter(self, fmt):
        self._fmt = fmt

    def _apply_num_style(self):
        """R23-B3：KPI 空值不与正常值同权重。
        R31：原位内联样式在窗口级 QSS 生效后会被 #big（30px 青）压回去，
        实测首屏四个「暂无数据」仍是 30px 青色。改为 objectName + compact 属性，
        由 QSS 选择器决定字号/颜色，切换时 unpolish/polish 强制重算。"""
        _c = bool(getattr(self, '_compact', False))
        _empty = bool(getattr(self, '_empty', False))
        self.setProperty('compact', _c)
        self.setObjectName('big_empty' if _empty else 'big')
        self.setStyleSheet('')
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_target(self, value, pulse=False):
        if value is None:
            _num_stop(self)
            self._cur = None
            if not getattr(self, '_empty', False):
                self._empty = True
                self._apply_num_style()
            self.setText('暂无数据')
            self.setToolTip('面板服务未上报该指标，恢复后自动刷新')
            return
        if getattr(self, '_empty', False):
            self._empty = False
            self._apply_num_style()
            self.setToolTip('')
        value = float(value)
        # R36-F5a：从空值态（或首帧）恢复时直接落值，不再排队缓动。原实现让
        # v0==v1 的动画进入共享节拍；若该节拍此刻处于暂停态（窗口构建/被遮挡），
        # 动画不 tick，而下一拍「等值直接刷新」分支会把它 _num_stop 摘掉，文本就
        # 永远停在「暂无数据」——显存 3.1%、GPU 0.0% 这类稳定值必中。
        if self._cur is None:
            _num_stop(self)
            self._cur = value
            self.setText(self._fmt(value))
            return
        # R15：探针开关 nonumanim —— 仅 A/B 归因（数字直接落值不插值）
        if 'nonumanim' in XH_PROBE:
            _num_stop(self)
            self._anim.stop()
            self._cur = value
            self.setText(self._fmt(value))
            return
        if self._cur is not None and abs(value - self._cur) < 1e-6:
            _num_stop(self)
            self._cur = value
            return
        # R03：小幅波动（<1% 且 <0.5）直接刷新文本，不吊 300 ms 插值动画
        if self._cur is not None and abs(value - self._cur) < max(0.5, abs(self._cur) * 0.01):
            _num_stop(self)
            self._anim.stop()
            self._cur = value
            self.setText(self._fmt(value))
            return
        start = self._cur if self._cur is not None else value
        self._anim.stop()
        self._cur = value
        if _NUM_FRAMES == 19:
            # R20-3 取证分支：还原旧 QVariantAnimation（16ms 驱动 ≈19 帧），出货不走这里
            _num_stop(self)
            self._anim.setStartValue(float(start))
            self._anim.setEndValue(float(value))
            self._anim.start()
        else:
            _num_start(self, start, value)     # R20-2：共享 150ms 节拍推进缓动
        if pulse:
            self._do_pulse()

    def _on_value(self, v):
        try:
            self.setText(self._fmt(float(v)))
        except Exception:
            _err()

    # G3：数字脉冲改颜色/透明度过渡（280ms 淡入淡出，莫兰迪强调色）
    # R20-3：不再用 setStyleSheet 逐帧换色（那会触发 StyleChange → 整页上溯）；
    # 颜色只存属性、由 self._draw_num 自绘使用。
    def _get_pulse_color(self):
        return self._pulse_color

    def _set_pulse_color(self, c):
        self._pulse_color = QColor(c)
        self._pulse_on = True
        self.update()

    pulse_color = Property(QColor, _get_pulse_color, _set_pulse_color)

    def _do_pulse(self):
        if self._pulse_anim is not None \
                and self._pulse_anim.state() != QAbstractAnimation.State.Stopped:
            return
        self._pulse_on = True
        self._pulse_color = QColor(206, 220, 240)
        self._pulse_anim = QPropertyAnimation(self, b'pulse_color', self)
        self._pulse_anim.setDuration(280)
        self._pulse_anim.setStartValue(QColor(206, 220, 240))
        self._pulse_anim.setEndValue(QColor(147, 169, 209))
        self._pulse_anim.setEasingCurve(QEasingCurve.OutCubic)

        def _pulse_done():
            self._pulse_on = False
            self.update()
        self._pulse_anim.finished.connect(_pulse_done)
        self._pulse_anim.start()

    # ── R20 / 甲 + 3c：数字自绘（不再走 QLabel::setText）────────────────────
    # setText 只改我们自己的显示串并 update()，不触发 QLabel 的文字布局/绘制；
    # 绘制时先贴「同帧底」（取不到就摘 opaque，让 Qt 补底），再按原字体/颜色/对齐画字。
    def setText(self, s):
        # R20-2：值未变不 update（缓动每拍都会回调，多数拍文字串相同）
        if s == getattr(self, '_shown', ''):
            return
        _SETTEXT_N[0] += 1
        if XH_OB_GHOST and getattr(self, '_shown', ''):
            self._ghost = self._shown
            QTimer.singleShot(XH_OB_GHOST, self._clear_ghost)
        self._shown = s
        # R36-F1：数字走自绘后不再调用 QLabel::setText，QLabel 内部文本永远停在
        # 构造值「—」，Windows UIA（读屏/自动化）读到的就是那个「—」。这里把显示串
        # 同步到无障碍名（UIA Name 取 accessibleName 优先），不改 QLabel 文本，
        # 因而不触发文字布局/重绘，R20 的自绘性能路径不受影响。
        if self.accessibleName() != s:
            self.setAccessibleName(s)
        self.update()

    def _clear_ghost(self):
        self._ghost = ''
        self.update()

    def paintEvent(self, ev):
        pm = _ob_base(self)
        if pm is None:
            if getattr(self, '_xh_ob', False):
                _ob_disarm(self)          # 失败回退：不贴旧底
        else:
            if not getattr(self, '_xh_ob', False):
                _ob_arm(self)
            _p = QPainter(self)
            _p.drawPixmap(0, 0, pm)
            _p.end()
        self._draw_num()

    def _draw_num(self):
        s = getattr(self, '_shown', '')
        if not s:
            return
        p = QPainter(self)
        p.setFont(self.font())
        try:
            if getattr(self, '_pulse_on', False):
                p.setPen(self._pulse_color)
            else:
                p.setPen(self.palette().color(self.foregroundRole()))
        except Exception:
            _err()
        p.drawText(self.rect(), int(self.alignment()) | int(Qt.TextSingleLine), s)
        if getattr(self, '_ghost', ''):
            p.drawText(self.rect(), int(self.alignment()) | int(Qt.TextSingleLine),
                       self._ghost)
        p.end()


# R16（Overlay）：把装饰动效（流光扫描 / 呼吸点）从「各自控件 update」改成
# 「一个覆盖整页的透明叠加层统一绘制」。目的是砍掉父链补底：改前每次 update 都会
# 带着 自身→卡片→网格→页→背景 整条祖先链重画（112–130 次/s），改后每拍只画
# 叠加层 + 其下被脏区覆盖的祖先各一次（150 ms 一拍 ≈ 6.7 次/s）。
#   XH_NOOVERLAY=1 回退到 R15 的「各自绘制」路径，用于同构建逐像素 A/B 取证。
XH_NOOVERLAY = bool(os.environ.get('XH_NOOVERLAY'))
XH_DOT_PHASE_V = None
try:
    _dp = os.environ.get('XH_DOT_PHASE')
    if _dp:
        XH_DOT_PHASE_V = float(_dp) % 1.0
except Exception:
    XH_DOT_PHASE_V = None
_DECO = {'ov': None, 'items': []}
XH_DECO_RECT = os.environ.get('XH_DECO_RECT', '')
if not XH_DECO_RECT and os.environ.get('XH_PAINT'):
    XH_DECO_RECT = os.environ['XH_PAINT'] + '.deco'   # 取证兜底：与 paint 日志同目录


def _deco_diag():
    """R16 取证：叠加层与装饰的真实状态（items/可见性/矩形/z 序/绘制错误）。
    R20-9 补 paused/why：判定「装饰是停着还是跑着」，避免停表状态被误当稳态。"""
    ov = _DECO.get('ov')
    out = {'ov': ov is not None, 'nooverlay': bool(XH_NOOVERLAY),
           'paused': bool(_DECO_PAUSED[0]), 'why': _DECO_WHY[0],
           'tm': len(_DECO_TM),
           'tm_active': sum(1 for _x in _DECO_TM if _x.isActive())}
    if ov is None:
        return out
    try:
        out['ov_vis'] = bool(ov.isVisible())
        g = ov.geometry()
        out['ov_geo'] = [g.x(), g.y(), g.width(), g.height()]
        out['ov_n'] = ov._n
    except Exception as e:
        out['ov_err'] = str(e)
    try:
        host = ov.parentWidget()
        items = []
        for w, kind in list(_DECO.get('items') or []):
            try:
                vis = bool(w.isVisible())
                r = QRect(w.mapTo(ov, QPoint(0, 0)), w.size())
                row = [type(w).__name__, kind, int(vis), r.x(), r.y(),
                       r.width(), r.height()]
                if vis and host is not None:
                    t = host.childAt(r.center())
                    row.append(type(t).__name__ if t is not None else None)
                items.append(row)
            except Exception as e:
                items.append(['?', kind, -1, str(e)])
        out['items'] = items
    except Exception as e:
        out['items_err'] = str(e)
    out['draw'] = _DECO.get('draw')
    out['draw_err'] = _DECO.get('err')
    return out


def _deco_on():
    return _DECO['ov'] is not None and not XH_NOOVERLAY


def _deco_add(w, kind):
    try:
        _DECO['items'].append((w, kind))
        w._deco_active = True
        ov = _DECO['ov']
        if ov is not None:
            ov.reload_items()
    except Exception:
        _err()


def _deco_dirty(w):
    """某个装饰的状态变了（进度/颜色/开关）→ 只把它的矩形并进叠加层脏区。"""
    ov = _DECO['ov']
    if ov is None or XH_NOOVERLAY:
        try:
            w.update()
        except Exception:
            _err()
        return
    ov.dirty_widget(w)


# ── R19 / 方案乙·带内版：装饰带级不透明缓存 ────────────────────────────────
# 为什么不做「整窗不透明叠加层」：WA_OpaquePaintEvent 是控件级属性，叠加层一旦不透明，
# 任何 expose（首次显示/还原/resize）都必须由它自己覆盖整窗，于是必须常驻一份整窗像素；
# 而窗内文本每 2s 就变，整窗缓存必然系统性陈旧 → 鬼影面会波及全页。
# 因此改为「每条装饰带一个几何最小、且自身不透明的控件」，只缓存它自己那一条的底：
#   点 16×16、4 条流光各 140×10（合计 5856 px，不是 2744000 px）。
# 底从「父级在该矩形内的渲染」取（装饰控件本体临时移出画面，避免把旧装饰烘进底里）。
_BAND_ITEMS = []
_BAND_FORCE_DEEP = os.environ.get('XH_BAND_DEEP') == '1'
_BANDS = {}
_BAND_BUSY = [False]
_BAND_STATS = {'rejected': 0, 'captured': 0}   # R21：白底拒绝计数（自检/审计用）

# R20-10：paint 实耗累计（XH_PCOST=1 时才累加；perf_counter 双读 ≈0.2µs/次）
_PCOST = {}


def _pc_bump(cls_name, on, us):
    try:
        if on.startswith('@'):
            k = cls_name + on
        else:
            k = cls_name + ('#' + on if on else '')
        if _BAND_BUSY[0]:
            k = 'R:' + k
        e = _PCOST.get(k)
        if e is None:
            _PCOST[k] = [1, us]
        else:
            e[0] += 1
            e[1] += us
    except Exception:
        pass

# ── R20-9：不可见三态「停装饰」，不是降频 ──────────────────────────────────
# 最小化（IsIconic）/ 遮挡（中心点 WindowFromPoint + IsWindowVisible）/
# 切页离开（当前页没有任何装饰消费者）三种状态一律 stop() 全部装饰定时器；
# 恢复时 start() 并立即补画一帧。注册表只收「装饰节拍」：Flow 共享/私有、
# BreathDot 私有、数字缓动共享、DecoOverlay 私有——轮询定时器不在此列。
_DECO_PAUSED = [False]
_DECO_WHY = ['']
_DECO_TM = []


def _deco_tm_add(tm, tag=''):
    try:
        if tm is not None and tm not in _DECO_TM:
            if tag:
                tm.setObjectName(tag)
            _DECO_TM.append(tm)
    except Exception:
        _err()


def _deco_set_paused(on, why=''):
    """on=True：全部装饰定时器 stop()；on=False：逐个 start()（已停的）。"""
    _prev = _DECO_PAUSED[0]
    _pw = _DECO_WHY[0]
    _DECO_PAUSED[0] = bool(on)
    # R20-10：why 只表示「当前暂停原因」；恢复后必须清空，否则残留 'min'/'cover'
    # 会让有效性校验误判（外观看着像还停着，实际装饰已在跑）。
    _DECO_WHY[0] = why if on else ''
    if XH_COST and _prev != bool(on):
        try:
            _cost('deco:%s:%s' % ('pause' if on else 'resume',
                                  why or _pw or '?'),
                  (time.perf_counter(), time.thread_time()))
        except Exception:
            _err()
    for tm in list(_DECO_TM):
        try:
            if on:
                tm.stop()
            elif not tm.isActive():
                tm.start()
        except RuntimeError:
            try:
                _DECO_TM.remove(tm)
            except Exception:
                pass
        except Exception:
            _err()


def _deco_deco_widgets():
    """所有装饰控件（叠加层路径 + 带内路径 + 流光条/条带），用于判定「当前页有无装饰消费者」。"""
    out = []
    for w, _k in list(_DECO.get('items') or []):
        out.append(w)
    out.extend(list(_BAND_ITEMS))
    # R20-4 的条带会把条从 _BAND_ITEMS 摘走；判页面归属必须把条/条带也算进来
    try:
        out.extend(list(_FLOW_STRIPS))
        out.extend(list(_FLOW_BARS))
    except Exception:
        _err()
    return out


def _deco_resume_frame():
    """恢复瞬间补画一帧：不依赖下一拍，相位/数字均推进一次并 update。"""
    if _DECO_PAUSED[0]:
        return
    ov = _DECO.get('ov')
    if ov is not None:
        try:
            ov._tick()
        except Exception:
            _err()
    for w in list(_BAND_ITEMS):
        try:
            if not w.isVisible():
                continue
            cn = type(w).__name__
            if cn == 'FlowProgressBar':
                w._flow_step()
            elif cn == 'BreathDot':
                w._tick()
        except Exception:
            _err()
    try:
        if _NUM_ANIM:
            _num_tick()
    except Exception:
        _err()


def _band_unpainted(pm):
    """R21：判定取到的「底」是不是还没画过的底。

    历史证据（round_221 归因）：
      1) 条带默认几何 100×30 → 整块纯黑 (0,0,0)，占屏 0.44%；
      2) 卡片 fade_in（QGraphicsOpacityEffect）期间取底 → 卡片区发白、卡缝纯黑，
         画出来就是一条白/黑相间的横带（实测占屏 0.93%）。
    真实底（卡片描边 + 深色渐变）同一条带内有 ≥3 级色差，且不含大面积纯白/纯黑。
    """
    try:
        img = pm.toImage()
        if img.isNull() or img.width() < 4 or img.height() < 4:
            return False
        step = max(1, min(img.width(), img.height()) // 12)
        mn = [255, 255, 255]
        mx = [0, 0, 0]
        tot = nb = nw = 0
        for y in range(1, img.height(), step):
            for x in range(1, img.width(), step):
                c = img.pixelColor(x, y)
                tot += 1
                r, g, b = c.red(), c.green(), c.blue()
                if r <= 6 and g <= 6 and b <= 6:
                    nb += 1
                elif r >= 248 and g >= 248 and b >= 248:
                    nw += 1
                for i, v in enumerate((r, g, b)):
                    if v < mn[i]:
                        mn[i] = v
                    if v > mx[i]:
                        mx[i] = v
        if not tot:
            return False
        if nb / float(tot) > 0.10 or nw / float(tot) > 0.10:
            return True
        flat = all(mx[i] - mn[i] <= 2 for i in range(3))
        if not flat:
            return False
        return all(v <= 6 for v in mx) or all(v >= 248 for v in mn)
    except Exception:
        return False


def _band_capture(w):
    # R21：条带可以在「自身隐藏、窗口已显示」时取底——首帧若等条带可见再取，
    # 窗口合成还没铺到该区域，拿到的是白底；隐藏时取底反而是干净的。
    if _BAND_BUSY[0] or w.width() <= 0 or w.height() <= 0:
        return
    _win = w.window()
    if _win is None or not _win.isVisible():
        return
    _BAND_BUSY[0] = True
    try:
        src = w.parentWidget()
        pm = None
        # 快路径：父级（GlassCard）自带背景 pixmap → 直接切片，不触发任何重绘、不移动控件。
        # R20-6：XH_BAND_DEEP=1 强制跳过快路径，用来单独验证深路径（取证开关，默认无影响）。
        if (not getattr(w, '_band_deep', False) and not _BAND_FORCE_DEEP
                and src is not None):
            c = getattr(src, '_cache', None)
            if c is not None and not c.isNull():
                loc = w.mapTo(src, QPoint(0, 0))
                if c.width() >= loc.x() + w.width() and c.height() >= loc.y() + w.height():
                    pm = c.copy(QRect(loc, w.size()))
        if pm is None or pm.isNull():
            pm = QPixmap(w.size())
            pm.fill(Qt.transparent)
            if src is not None:
                if getattr(w, '_band_src_window', False):
                    # R20-4：条带跨多张卡片，父「页」本身是透明的——改从顶层窗口取底，
                    # 才能把 Backdrop/多卡片/页间距一次合成对。
                    # R21：QWidget.render() 带 sourceRegion 在窗口上实测「什么都不画」
                    #（_r21_render_probe.py：三种 flag 组合都得到全透明），改为
                    # QWidget.grab(rect)，它返回 DPR 倍尺寸位图，需归一 dpr=1.0。
                    src = w.window()
                    loc = w.mapTo(src, QPoint(0, 0))
                    _g = src.grab(QRect(loc, w.size()))
                    if not _g.isNull():
                        _g.setDevicePixelRatio(1.0)
                        if _g.size() != w.size():
                            _g = _g.scaled(w.size(), Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation)
                            _g.setDevicePixelRatio(1.0)
                        pp = QPainter(pm)
                        pp.drawPixmap(0, 0, _g)
                        pp.end()
                else:
                    loc = w.mapTo(src, QPoint(0, 0))
                    g = w.geometry().topLeft()   # R20-4：move() 只收 QPoint/(x,y)，原来的 QRect 一直 TypeError
                    w.move(-40000, -40000)       # 暂离画面：不得把旧装饰烘进底
                    try:
                        _g = src.grab(QRect(loc, w.size()))
                        if not _g.isNull():
                            _g.setDevicePixelRatio(1.0)
                            if _g.size() != w.size():
                                _g = _g.scaled(w.size(), Qt.IgnoreAspectRatio,
                                               Qt.SmoothTransformation)
                                _g.setDevicePixelRatio(1.0)
                            pp = QPainter(pm)
                            pp.drawPixmap(0, 0, _g)
                            pp.end()
                    finally:
                        w.move(g)
        w._band_deep = False
        # R21：16×16 呼吸点是装饰小件，取到的深色底天然接近纯黑，无法与「未绘制」区分；
        # 只在「长边 < 24」时豁免（注意条带是 716×10，长边够大，必须照查——
        # 曾因误用 min() 尺寸而漏判，fade_in 期间的白卡片底被缓存成白/黑横带）。
        if max(pm.width(), pm.height()) >= 24 and _band_unpainted(pm):
            # R21：首帧未绘制——不缓存纯色底，重绘一帧后重取。
            # 重试有上限：否则 paintEvent → 取底失败 → 再 update 会滚成死循环
            # （实测 16×16 呼吸点曾每秒重试上千次，日志 1.4 MB/20 s）。
            _BAND_STATS['rejected'] += 1
            w._band_rej = getattr(w, '_band_rej', 0) + 1
            if os.environ.get('XH_STRIP_DBG'):
                print('[band] unpainted rejected size=%s n=%d' % (pm.size(), w._band_rej),
                      flush=True)
            _dump = os.environ.get('XH_BAND_DUMP')
            if _dump:
                try:
                    _n = len(os.listdir(_dump))
                    pm.save(os.path.join(_dump, 'band_%dx%d_%03d.png'
                                         % (pm.width(), pm.height(), _n)))
                except Exception:
                    pass
            if w._band_rej <= 6:
                QTimer.singleShot(32, w.update)
            return
        w._band_rej = 0
        _BAND_STATS['captured'] += 1
        _BANDS[id(w)] = pm
    except Exception as _e:
        if os.environ.get('XH_STRIP_DBG'):
            import traceback
            print('[band] capture EXC %r\n%s' % (_e, traceback.format_exc()), flush=True)
    finally:
        _BAND_BUSY[0] = False


def _band_blit(p, w):
    pm = _BANDS.get(id(w))
    if (pm is None or pm.isNull() or pm.size() != w.size()) \
            and getattr(w, '_band_rej', 0) <= 6:
        _band_capture(w)
        pm = _BANDS.get(id(w))
    if pm is not None and not pm.isNull():
        p.drawPixmap(0, 0, pm)


def _band_widgets():
    return [w for w in _BAND_ITEMS if getattr(w, '_band', False)]


class _BandWatcher(QObject):
    """压在装饰带上的任何控件重绘 → 该带失效，下一拍重建（≤1 帧延迟，杜绝鬼影）。"""

    def eventFilter(self, obj, ev):
        if _BAND_BUSY[0] or ev.type() != QEvent.Paint:
            return False
        cn = type(obj).__name__
        if cn not in ('QLabel', 'AnimatedNumberLabel', 'QLCDNumber', 'QProgressBar'):
            return False
        try:
            rg = QRect(obj.mapToGlobal(QPoint(0, 0)), obj.size())
        except Exception:
            return False
        for w in _band_widgets():
            try:
                if w is obj or not w.isVisible():
                    continue
                wr = QRect(w.mapToGlobal(QPoint(0, 0)), w.size())
                if wr.intersects(rg):
                    w._band_need = True
                    w._band_deep = True   # 带下有文本 → 必须走「含子控件」的深抓，杜绝鬼影
            except Exception:
                _err()
                continue
        return False


_BAND_WATCHER = [None]


def _band_watch_install():
    if _BAND_WATCHER[0] is not None:
        return
    try:
        _BAND_WATCHER[0] = _BandWatcher()
        QApplication.instance().installEventFilter(_BAND_WATCHER[0])
    except Exception:
        _err()


def _band_invalidate_all():
    for w in _band_widgets():
        w._band_need = True
    for s in _FLOW_STRIPS:
        try:
            s._band_need = True
            _BANDS.pop(id(s), None)
        except Exception:
            _err()


# ── R20 / 方案甲·子控件级：压玻璃的文本控件「自铺底 + opaque」 ──────────────
# 判定表（XH-123）：唯一能切断「子 update → 父补底」的变量是子控件
# WA_OpaquePaintEvent=1；而含 background 的 QSS 会在 polish 时把它清掉（XH-124）。
# 因此这里两条硬规则：
#   ① 只在「此刻能取到同帧底」时才装 opaque；取不到立刻摘掉走普通绘制——
#      宁可让 Qt 补底，也不许贴旧底（旧底=鬼影）。
#   ② 每次 StyleChange 之后由 _OpaqueGuard 统一补设（QSS 重放会清标记）。
# 底 = GlassCard._cache（半透明卡面）叠在 Backdrop._baked（不透明烘焙帧）之上；
# 两者都取自「当前这一刻」的现有 pixmap，不触发任何 render()，也不跨帧。
_OPAQUE_BASE = []
_OB_BUSY = [False]
_SETTEXT_N = [0]

# R20-2：数字缓动共享 150ms 节拍（与装饰同拍）。只推进「正在缓动」的标签，
# 每拍调一次 `_on_value`；`setText` 自带 dedup，文字串没变就不 update。
# R20-3：XH_NUM_FRAMES 仅取证——19=旧 QVariantAnimation(16ms×19帧)、2=300ms/2帧、
#        3=450ms/3帧（仍 6.67/s·条）。R20 逐帧判定后出货默认 3。
_NUM_FRAMES = int(os.environ.get('XH_NUM_FRAMES', '3') or 3)
_NUM_DUR = [0.450 if _NUM_FRAMES == 3 else 0.300]
_NUM_ANIM = []
_NUM_TIMER = [None]


def _num_tick():
    _ct0 = time.perf_counter() if XH_COST else 0.0
    _tt0 = time.thread_time() if XH_COST else 0.0
    if XH_COST:
        _cost('cb:num', (time.perf_counter(), time.thread_time()))
    now = time.perf_counter()
    live = []
    for it in _NUM_ANIM:
        w, t0, v0, v1 = it
        t = (now - t0) / _NUM_DUR[0]
        # R20-9：隐藏页的数字缓动不许占节拍——直接落终值并摘掉
        if t >= 1.0 or not w.isVisible():
            try:
                w._on_value(v1)
            except Exception:
                _err()
            continue
        e = 1.0 - (1.0 - t) ** 3          # OutCubic
        try:
            w._on_value(v0 + (v1 - v0) * e)
        except Exception:
            _err()
        live.append(it)
    _NUM_ANIM[:] = live
    # R20-9：没有正在缓动的数字就停表；_num_start 负责再起
    if not live and _NUM_TIMER[0] is not None and _NUM_TIMER[0].isActive():
        _NUM_TIMER[0].stop()
    if XH_COST:
        _cost('num:tick', (_ct0, _tt0))


def _num_stop(w):
    """把控件从共享缓动列表里摘掉——直接落值的分支必须先摘，
    否则上一段缓动还会把旧值推回来（R20 量化边界实测踩中）。"""
    _NUM_ANIM[:] = [x for x in _NUM_ANIM if x[0] is not w]


def _num_start(w, v0, v1):
    if XH_COST:
        _cost('num:start', (time.perf_counter(), time.thread_time()))
    if _NUM_TIMER[0] is None:
        _NUM_TIMER[0] = QTimer()
        _NUM_TIMER[0].setInterval(150)
        _NUM_TIMER[0].timeout.connect(_num_tick)
        _deco_tm_add(_NUM_TIMER[0], 'num')
    if not _DECO_PAUSED[0] and not _NUM_TIMER[0].isActive():
        _NUM_TIMER[0].start()
    _NUM_ANIM[:] = [x for x in _NUM_ANIM if x[0] is not w]
    _NUM_ANIM.append((w, time.perf_counter(), float(v0), float(v1)))
# R20-反例（仅测量）：让旧数字晚 XH_OB_GHOST 毫秒才消失，用来验证「鬼影检测手段」有效。
XH_OB_GHOST = int(os.environ.get('XH_OB_GHOST', '0') or 0)


def _ob_card(w):
    src = w.parentWidget()
    while src is not None and not isinstance(src, GlassCard):
        src = src.parentWidget()
    return src


def _ob_base(w):
    """屏幕真实像素切片；任何前提不满足即 None（调用方据此摘掉 opaque）。"""
    if _OB_BUSY[0]:
        return None
    try:
        card = _ob_card(w)
        if card is None:
            return None
        c = getattr(card, '_cache', None)
        bd = BACKDROP
        bk = getattr(bd, '_baked', None) if bd is not None else None
        if c is None or c.isNull() or not bk or bk[1].isNull():
            return None
        baked = bk[1]
        # R20-钩子①（resize）：旧帧在 resize 之后仍然是「非空」的，但尺寸已经对不上；
        # 不校验尺寸就会把旧帧当底贴上去（实测会出现白块）。尺寸不符一律回退。
        if c.size() != card.size() or baked.size() != bd.size():
            return None
        r = QRect(w.mapTo(card, QPoint(0, 0)), w.size())
        if r.width() <= 0 or r.height() <= 0 or not c.rect().contains(r):
            return None
        g = QPoint(w.mapTo(bd, QPoint(0, 0)))
        if not baked.rect().contains(QRect(g, w.size())):
            return None
        _OB_BUSY[0] = True
        pm = QPixmap(w.size())
        p = QPainter(pm)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawPixmap(0, 0, baked, g.x(), g.y(), w.width(), w.height())
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.drawPixmap(0, 0, c, r.x(), r.y(), r.width(), r.height())
        p.end()
        return pm
    except Exception:
        return None
    finally:
        _OB_BUSY[0] = False


def _ob_ok(w):
    """R20：_ob_base 的「只判定不取底」版本——不建 QPixmap、不画，供 StyleChange 后
    逐项复核用。判定条件与 _ob_base 完全一致，避免 repair 时 N 次 pixmap 分配。"""
    if _OB_BUSY[0]:
        return False
    try:
        card = _ob_card(w)
        if card is None:
            return False
        c = getattr(card, '_cache', None)
        bd = BACKDROP
        bk = getattr(bd, '_baked', None) if bd is not None else None
        if c is None or c.isNull() or not bk or bk[1].isNull():
            return False
        baked = bk[1]
        if c.size() != card.size() or baked.size() != bd.size():
            return False
        r = QRect(w.mapTo(card, QPoint(0, 0)), w.size())
        if r.width() <= 0 or r.height() <= 0 or not c.rect().contains(r):
            return False
        g = QPoint(w.mapTo(bd, QPoint(0, 0)))
        return baked.rect().contains(QRect(g, w.size()))
    except Exception:
        return False


def _ob_arm(w):
    if getattr(w, '_xh_ob', False):
        return
    w._xh_ob = True
    try:
        w.setAttribute(Qt.WA_OpaquePaintEvent, True)
        w.setAttribute(Qt.WA_NoSystemBackground, True)
    except Exception:
        _err()
    _OPAQUE_BASE.append(w)


def _ob_disarm(w):
    if not getattr(w, '_xh_ob', False):
        return
    w._xh_ob = False
    try:
        w.setAttribute(Qt.WA_OpaquePaintEvent, False)
    except Exception:
        _err()
    try:
        _OPAQUE_BASE.remove(w)
    except ValueError:
        _err()


def _ob_scan():
    """压在 GlassCard 上的「文本 / 仪表 / 曲线」直接子控件统一自铺底 + opaque。

    只认父控件就是卡片本身的控件：它的底=卡片缓存（不含兄弟控件内容），
    铺在它下面的底必然正确。压在别的兄弟控件上的控件（例如 Gauge 内部的
    数值 QLabel、列表里的行）一律不碰，否则会把兄弟内容擦掉。
    """
    try:
        app = QApplication.instance()
        if app is None:
            return
        _extra = os.environ.get('XH_OB_EXTRA', '')
        for l in app.allWidgets():
            par = l.parentWidget()
            if par is None or not isinstance(par, GlassCard):
                continue
            _cn = type(l).__name__
            if type(l) is QLabel:
                pass
            elif _cn == 'Gauge' and 'gauge' in _extra:
                pass
            elif _cn == 'PlotWidget' and 'plot' in _extra:
                pass
            else:
                continue
            if getattr(l, '_xh_ob', False) or not l.isVisible():
                continue
            if _ob_base(l) is not None:
                _ob_arm(l)
    except Exception:
        _err()


def _ob_repair():
    """StyleChange / show 之后补设（幂等）；取不到底的顺手摘掉。
    R20：判定改用 _ob_ok（不建 pixmap）——StyleChange 风暴时不会再为每个控件分配底片。"""
    for w in list(_OPAQUE_BASE):
        try:
            if not w.isVisible() or not _ob_ok(w):
                _ob_disarm(w)
                continue
            w.setAttribute(Qt.WA_OpaquePaintEvent, True)
            w.setAttribute(Qt.WA_NoSystemBackground, True)
        except Exception:
            _err()
    _ob_scan()


class _OpaqueBaseFilter(QObject):
    """纯 QLabel#sub 没有自己的 paintEvent：在它的 Paint 之前把底铺上。

    铺的就是 _ob_base 在同一时刻算出的屏幕真实像素，随后 QLabel 只画文字；
    取不到底就当场摘掉 opaque，让 Qt 正常补底（不贴旧底）。
    """

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Paint or not getattr(obj, '_xh_ob', False):
            return False
        pm = _ob_base(obj)
        if pm is None:
            _ob_disarm(obj)
            return False
        try:
            p = QPainter(obj)
            p.drawPixmap(0, 0, pm)
            p.end()
        except Exception:
            _ob_disarm(obj)
        return False


_OB_FILTER = [None]


def _ob_install():
    try:
        if _OB_FILTER[0] is not None:
            return
        _OB_FILTER[0] = _OpaqueBaseFilter()
        QApplication.instance().installEventFilter(_OB_FILTER[0])
    except Exception:
        _err()



def _draw_flow(p, rect, w):
    """流光进度条绘制（坐标从控件局部换到叠加层局部，绘制序列与 R15 完全一致）。"""
    p.setPen(Qt.NoPen)
    r = rect.adjusted(0, 2, 0, -2)
    p.setBrush(QColor(255, 255, 255, 22))
    p.drawRoundedRect(r, 4, 4)
    if w._ratio <= 0:
        return
    fill = QRectF(r.x(), r.y(), r.width() * w._ratio, r.height())
    _ik = (int(fill.width()), int(fill.height()))
    if getattr(w, '_gc_key', None) != _ik:
        g = QLinearGradient(fill.x(), 0, fill.right(), 0)
        g.setColorAt(0, w._c1)
        g.setColorAt(1, w._c2)
        w._gc = g
        w._gc_key = _ik
    p.setBrush(w._gc)
    p.drawRoundedRect(fill, 4, 4)
    phase = (w._t / 3.2) % 1.0
    band_w = fill.width() * 0.35
    bx = fill.x() - band_w + (fill.width() + band_w) * phase
    band = QRectF(bx, fill.y(), band_w, fill.height())
    p.setBrush(QColor(255, 255, 255, 70))
    p.drawRoundedRect(band, 4, 4)


def _draw_dot(p, rect, w):
    """呼吸点绘制（0..1..0；与 BreathDot.paintEvent 同一算式与顺序）。"""
    if not getattr(w, '_on', True):
        return
    t = w._phase
    s = t if t < 0.5 else 1.0 - t
    scale = 0.82 + 0.32 * (s * 2.0)
    alpha = int(120 + 135 * (s * 2.0))
    d = w._size * scale
    x = (rect.width() - d) / 2
    y = (rect.height() - d) / 2
    halo = QColor(w._color)
    halo.setAlpha(int(alpha * 0.5))
    p.setPen(Qt.NoPen)
    p.setBrush(halo)
    hd = d + 7
    p.drawEllipse(QRectF((rect.width() - hd) / 2, (rect.height() - hd) / 2, hd, hd))
    core = QColor(w._color)
    core.setAlpha(alpha)
    p.setBrush(core)
    p.drawEllipse(QRectF(x, y, d, d))


class DecoOverlay(QWidget):
    """整页透明叠加层：所有装饰动效在这里一次画完，一个节拍一次 update。"""

    def __init__(self, host):
        super().__init__(host)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._dt = XH_FLOW_MS
        self._n = 0
        self._tm = QTimer(self)
        self._tm.setInterval(self._dt)
        self._tm.timeout.connect(self._tick)
        _deco_tm_add(self._tm, 'flow')
        if not _DECO_PAUSED[0]:
            self._tm.start()
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self._need_raise = False
        self.reload_items()
        self.raise_()
        self._need_raise = False

    def reload_items(self):
        try:
            old = len(getattr(self, '_items', []))
            self._items = list(_DECO['items'])
            if len(self._items) != old:
                self._need_raise = True   # 只登记一次置顶，节拍内不再反复 raise_（否则每拍整窗重绘）
        except Exception:
            self._items = []

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
            self.raise_()
        return False

    def _rect_of(self, w):
        try:
            if not w.isVisible():
                return None
            return QRect(w.mapTo(self, QPoint(0, 0)), w.size())
        except Exception:
            return None

    def dirty_widget(self, w):
        r = self._rect_of(w)
        if r is not None:
            self.update(r)

    def _ensure_top(self):
        # R16 修：原实现用 children()[-1] 判断置顶，而 children() 是插入序、不是 z 序，
        # 于是每一拍都 raise_() → 每拍一次整窗 damage（实测 Backdrop 单次 paints 686000=整窗）。
        # 改为「有新增装饰时才提一次」，节拍内不再动 z 序。
        if getattr(self, '_need_raise', False):
            self._need_raise = False
            try:
                self.raise_()
            except Exception:
                _err()

    def _tick(self):
        self._ensure_top()
        # 单变量：一拍照样驱动相位，但整拍只 update 一次（脏区=所有可见装饰的并集）
        reg = QRegion()
        rects = []
        any_flow = False
        for w, kind in self._items:
            try:
                if not w.isVisible():
                    continue
                if kind == 'flow':
                    w._flow_step()
                    any_flow = True
                else:
                    w._advance()
                r = QRect(w.mapTo(self, QPoint(0, 0)), w.size())
                reg = reg.united(QRegion(r))
                rects.append(r)
            except RuntimeError:
                _err()
                continue
            except Exception:
                _err()
                continue
        if reg.isEmpty():
            return
        # R18-3b 结论（实测）：把散开的多块脏区合并成 **一个外接矩形** 会让总 paints 从
        # 112.3 涨到 203.3/s（bbox 923×69 同时覆盖 4 张卡片 + 导航列，被连带重绘的控件
        # 反而更多）。所以 R16 的「按上下两块」不是 bug 而是必要的面积裁剪，已回滚。
        # 真正解法见 R18-3d：叠加层改不透明 + 贴父级静态 pixmap（方案乙），
        # 让祖先链与非装饰卡片的 paint 增量归 0。
        n_r = len(rects)
        if n_r > 1:
            _sum = sum(max(1, r.width() * r.height()) for r in rects)
            _bb = reg.boundingRect()
            if _bb.width() * _bb.height() <= _sum * 1.8:
                reg = QRegion(_bb)
            else:
                _mid = sum(r.center().y() for r in rects) / float(n_r)
                _top = QRegion()
                _bot = QRegion()
                for r in rects:
                    if r.center().y() <= _mid:
                        _top = _top.united(QRegion(r))
                    else:
                        _bot = _bot.united(QRegion(r))
                reg = _top.united(_bot)
        self.update(reg)
        self._n += 1
        if XH_DECO_RECT and (self._n == 1 or self._n % 20 == 0):
            try:
                rows = []
                for r in rects:
                    rows.append([r.x(), r.y(), r.width(), r.height()])
                with open(XH_DECO_RECT, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'n': self._n, 'rects': rows,
                                        'reg': [[q.x(), q.y(), q.width(), q.height()]
                                                for q in reg]}) + '\n')
            except Exception as e:
                try:
                    with open(XH_DECO_RECT + '.err', 'a', encoding='utf-8') as f:
                        f.write('%s\n' % e)
                except Exception:
                    _err()

    def paintEvent(self, ev):
        vis = []
        for w, kind in self._items:
            try:
                if w.isVisible():
                    vis.append((w, kind, QRect(w.mapTo(self, QPoint(0, 0)), w.size())))
            except RuntimeError:
                _err()
                continue
            except Exception as e:
                _DECO['err'] = 'vis %s: %s' % (type(e).__name__, e)
        if not vis:
            return
        _DECO['draw'] = len(vis)
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            for w, kind, r in vis:
                if kind == 'flow':
                    _draw_flow(p, r, w)
                else:
                    _draw_dot(p, r, w)
            p.end()
        except Exception as e:
            _DECO['err'] = '%s: %s' % (type(e).__name__, e)


_DOT_PM_CACHE = {}


def _dot_layer_pm(color, w, h, d, dpr, ring):
    """R20-10c：呼吸点的单层（光晕 ring / 核心）预渲染。

    不能把「光晕+核心」合成一张再用 setOpacity：原实现是两层各自带 alpha 叠加，
    核心半透明时会透出下面的光晕；合成一张会让核心变成全不透明再整体降不透明度，
    半透明档位差异很大（实测 0.35 档中心点差 ~16/255）。故拆两层分别 blit。
    """
    key = (w, h, round(d, 3), color.rgba(), round(dpr, 2), bool(ring))
    pm = _DOT_PM_CACHE.get(key)
    if pm is not None:
        return pm
    img = QImage(max(1, int(round(w * dpr))), max(1, int(round(h * dpr))),
                 QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    ip = QPainter(img)
    ip.setRenderHint(QPainter.Antialiasing)
    ip.setPen(Qt.NoPen)
    c = QColor(color)
    c.setAlpha(255)
    ip.setBrush(c)
    ip.drawEllipse(QRectF((w - d) / 2 * dpr, (h - d) / 2 * dpr,
                          d * dpr, d * dpr))
    ip.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    if len(_DOT_PM_CACHE) > 128:
        _DOT_PM_CACHE.clear()
    _DOT_PM_CACHE[key] = pm
    return pm


class BreathDot(QWidget):
    """呼吸状态圆点（对应网页 breath / fwpulse）。"""
    def __init__(self, color=UI_C['OK'], size=10, period=DUR_SLOW, parent=None):  # R15-X20：token 统一
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size + 6, size + 6)
        self._phase = 0.0
        self._timer = QTimer(self)
        # R12（XH-084 层 B）：90→150 ms（11→6.7 fps）。呼吸周期不变、相位算法不变，
        # 只是每帧的透明度步进从 ~2.9% 变 ~4.5%；对 10px 小点肉眼无法分辨。
        # 可用 XH_DOT_MS=90 回退做 A/B；XH_DOT_PHASE=<0..1> 固定相位用于像素对比取证。
        self._dot_ms = int(os.environ.get('XH_DOT_MS', '150') or 150)
        self._timer.setInterval(self._dot_ms)
        self._timer.timeout.connect(self._tick)
        _deco_tm_add(self._timer, 'dot')
        self._period = period
        self._on = True
        # R19：方案乙·带内版（点 16×16 也自带不透明底）
        self._band = os.environ.get('XH_BAND', '1') == '1'
        if self._band:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            _BAND_ITEMS.append(self)
        _ph = os.environ.get('XH_DOT_PHASE')
        if _ph:
            try:
                self._phase = float(_ph) % 1.0
            except Exception:
                _err()
        elif 'nodot' in XH_PROBE or 'noanim' in R10 or 'freeze' in R10:
            pass
        elif _deco_on() or _DECO['ov'] is not None:
            # R16：交给叠加层统一节拍
            if self._band:
                self._timer.start()
            else:
                _deco_add(self, 'dot')
        else:
            self._timer.start()

    def showEvent(self, ev):
        # R10（hidesstop）：回到可见页再续跑，避免隐藏页空转重绘
        # R16：已交给叠加层时不重启私有定时器（否则每拍多一次「空 paint + 父链补底」）
        if getattr(self, '_deco_active', False):
            _deco_dirty(self)
        elif ('hidestop' in R10 and 'nodot' not in XH_PROBE and 'noanim' not in R10
                and not os.environ.get('XH_DOT_PHASE') and not _DECO_PAUSED[0]):
            if not self._timer.isActive():
                self._timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        if 'hidestop' in R10:
            self._timer.stop()
        super().hideEvent(ev)

    def set_color(self, color):
        self._color = QColor(color)
        _deco_dirty(self)

    def set_on(self, on):
        self._on = on
        _deco_dirty(self)

    def _advance(self):
        # R16：叠加层节拍只推进相位，绘制由 DecoOverlay 统一完成
        if XH_DOT_PHASE_V is not None:
            return
        self._phase = (self._phase + float(self._dot_ms) / self._period) % 1.0

    def _tick(self):
        if XH_COST:
            _cost('cb:dot', (time.perf_counter(), time.thread_time()))
        self._advance()
        if self._band and getattr(self, '_band_need', True):
            _band_capture(self)
            self._band_need = False
        self.update()

    def paintEvent(self, ev):
        if _deco_on() and getattr(self, '_deco_active', False):
            return          # R16：改由叠加层绘制（像素等价，见 _draw_dot）
        p = QPainter(self)
        if self._band:
            _band_blit(p, self)
        if not self._on:
            return
        p.setRenderHint(QPainter.Antialiasing)
        # 0..1..0 呼吸
        t = self._phase
        s = t if t < 0.5 else 1.0 - t
        scale = 0.82 + 0.32 * (s * 2.0)
        alpha = int(120 + 135 * (s * 2.0))
        d = self._size * scale
        hd = d + 7
        _dpr = float(p.device().devicePixelRatioF()) if p.device() is not None else 1.0
        p.setOpacity(int(alpha * 0.5) / 255.0)   # 光晕 = 原 int(alpha*0.5)
        p.drawPixmap(0, 0, _dot_layer_pm(self._color, self.width(), self.height(),
                                         hd, _dpr, True))
        p.setOpacity(alpha / 255.0)
        p.drawPixmap(0, 0, _dot_layer_pm(self._color, self.width(), self.height(),
                                         d, _dpr, False))
        p.setOpacity(1.0)


class ToastManager(QObject):
    """右上角通知条（对应网页 .tnote 滑入/滑出）。"""
    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self._stack = []
        self._items = []

    def show(self, text, kind='info', duration=2600):
        # 「系统通知 / 免打扰」设置真实生效（此前只写入偏好、从未被读取）
        _np = getattr(self._win, '_notify_prefs', None)
        if _np:
            if not _np.get('enabled', True):
                return
            if _np.get('dnd') and kind not in ('err', 'warn', 'warning'):
                return
        lbl = QLabel('', self._win)
        lbl.setWordWrap(True)
        colors = {'info': UI_C['INK_2'], 'ok': UI_C['OK'],
                  'warn': UI_C['WARN'], 'err': UI_C['ERR']}
        c = colors.get(kind, UI_C['INK_2'])
        lbl.setText(text)
        lbl.setStyleSheet(
            _t('background: rgba(11,20,34,0.92); color:@INK_0@; border:1px solid %s; '
            'border-radius:14px; padding:10px 16px; font-size:12px; '
            'border-left:3px solid %s;') % (c, c))
        lbl.setMinimumWidth(260)
        lbl.setMaximumWidth(380)
        lbl.adjustSize()
        lbl.show()
        self._items.append(lbl)
        self._reposition()
        # 滑入
        end = self._target_pos(len(self._items) - 1)
        anim = QPropertyAnimation(lbl, b'pos', lbl)
        anim.setStartValue(QPoint(self._win.width() + 20, end.y()))
        anim.setEndValue(end)
        anim.setDuration(380)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        lbl._toast_anim = anim
        QTimer.singleShot(duration, lambda: self._dismiss(lbl))

    def _target_pos(self, i):
        if not self._items:
            return QPoint(0, 0)
        lbl = self._items[i]
        x = self._win.width() - lbl.width() - 24
        y = 18
        for k in range(i):
            y += self._items[k].height() + 10
        return QPoint(x, y)

    def _reposition(self):
        for i, lbl in enumerate(self._items):
            pos = self._target_pos(i)
            lbl.move(pos)

    def _dismiss(self, lbl):
        if lbl not in self._items:
            return
        anim = QPropertyAnimation(lbl, b'pos', lbl)
        anim.setStartValue(lbl.pos())
        anim.setEndValue(QPoint(self._win.width() + 20, lbl.pos().y()))
        anim.setDuration(340)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.start()
        lbl._dismiss_anim = anim

        def done():
            if lbl in self._items:
                self._items.remove(lbl)
            lbl.hide()
            lbl.deleteLater()  # R020/XH-M6：滑出后销毁，长会话不再累积 QLabel
            self._reposition()
        anim.finished.connect(done)


# R15-A05 归档：Spinner 类（未被引用，保留源码待清理）
class Spinner(QWidget):
    """加载旋转圈（对应网页 .spin / spin keyframes）。"""

    def __init__(self, size=18, color=UI_C['INK_2'], parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(color)
        self.setFixedSize(size + 8, size + 8)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._size / 2
        rect = QRectF((self.width() - self._size) / 2,
                      (self.height() - self._size) / 2, self._size, self._size)
        p.translate(rect.center())
        p.rotate(self._angle)
        p.translate(-rect.center())
        track = QPen(QColor(self._color.red(), self._color.green(),
                            self._color.blue(), 50), 2.6)
        track.setCapStyle(Qt.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        arc = QPen(self._color, 2.6)
        arc.setCapStyle(Qt.RoundCap)
        p.setPen(arc)
        p.drawArc(rect, 0, 100 * 16)


_FLOW_BARS = []
_FLOW_STRIPS = []
_FLOW_PAINT_OFF = [False]
_FLOW_STRIP_ON = os.environ.get('XH_FLOWSTRIP', '1') != '0'
_FLOW_TICK_N = [0]
_FLOW_TIMER = {'t': None}


def _flow_attach(bar):
    """R15：把所有流光条挂到同一个 150 ms 节拍（4 个私有定时器 → 1 个共享）。"""
    if 'noflow' in XH_PROBE:
        return
    if XH_NOFLOWSHARE:
        # A/B 取证路径：还原 R14 的「每条一个定时器」行为
        if getattr(bar, '_own_timer', None) is None:
            tm = QTimer(bar)
            tm.setInterval(XH_FLOW_MS)
            tm.timeout.connect(bar._flow_step)
            bar._own_timer = tm
            _deco_tm_add(tm, 'flow-own')
        if not _DECO_PAUSED[0] and not bar._own_timer.isActive():
            bar._own_timer.start()
        return
    if bar not in _FLOW_BARS:
        _FLOW_BARS.append(bar)
    if _FLOW_TIMER['t'] is None:
        tm = QTimer()
        tm.setInterval(XH_FLOW_MS)
        tm.timeout.connect(_flow_tick_all)
        _FLOW_TIMER['t'] = tm
        _deco_tm_add(tm, 'flow-shared')
    # R20-9：被「无可见条」停表后，条重新可见（showEvent 会再挂一次）必须能复跑
    if not _DECO_PAUSED[0] and not _FLOW_TIMER['t'].isActive():
        _FLOW_TIMER['t'].start()


def _flow_tick_all():
    if XH_COST:
        _cost('cb:flow', (time.perf_counter(), time.thread_time()))
    # R20-9：切页离开后所有流光条都不可见 → 本拍停表（showEvent 会经 _flow_attach 复跑）
    try:
        if not any(b.isVisible() for b in _FLOW_BARS):
            if _FLOW_TIMER['t'] is not None:
                _FLOW_TIMER['t'].stop()
            return
    except Exception:
        _err()
    _dbg = os.environ.get('XH_STRIP_DBG')
    if _dbg:
        _FLOW_TICK_N[0] += 1
        if _FLOW_TICK_N[0] % 20 == 1:
            print('[strip] tick#%d bars=%d strips=%d need=%s' % (
                _FLOW_TICK_N[0], len(_FLOW_BARS), len(_FLOW_STRIPS),
                [(s._tick_need, s.isVisible(), len(s._bars)) for s in _FLOW_STRIPS]), flush=True)
    for b in list(_FLOW_BARS):
        try:
            b._flow_step()
        except RuntimeError:
            _err()
        except Exception as _e:
            if _dbg:
                print('[strip] step EXC %r' % _e, flush=True)
    # R20-4：整拍只允许条带 update 一次——4 条并成 1 个脏区（一拍一区）
    if _dbg and _FLOW_TICK_N[0] % 20 == 0:
        print('[strip] afterstep bars=%d need=%s' % (
            len(_FLOW_BARS), [(s._tick_need, s.isVisible(), len(s._bars)) for s in _FLOW_STRIPS]),
              flush=True)
    for s in list(_FLOW_STRIPS):
        try:
            if getattr(s, '_tick_need', False) and s._sync_geo():
                s._tick_need = False
                s.update()
        except Exception as _e:
            if _dbg:
                print('[strip] sync EXC %r' % _e, flush=True)


def _deco_adopt(app):
    """R16：把「在叠加层创建之前就已建好」的装饰控件接管过来。

    页面构建早于叠加层的装饰控件会走 R15 的各自计时器路径（自带 update），
    这里统一停掉私有定时器并改成注册到叠加层，避免两套绘制并存。
    """
    for w in app.allWidgets():
        try:
            if getattr(w, '_deco_active', False):
                continue
            if getattr(w, '_band', False):
                continue        # R19：带内不透明模式自己画自己，不交给叠加层
            cn = type(w).__name__
            if cn == 'FlowProgressBar':
                kind = 'flow'
            elif cn == 'BreathDot':
                kind = 'dot'
            else:
                continue
            if kind == 'flow' and 'noflow' in XH_PROBE:
                continue
            if kind == 'dot' and ('nodot' in XH_PROBE or 'noanim' in R10 or 'freeze' in R10):
                continue
            if kind == 'dot':
                try:
                    w._timer.stop()
                except Exception:
                    _err()
            _deco_add(w, kind)
        except Exception:
            _err()
            continue          # 单个控件出错不得中断整个接管（否则叠加层永远拿不到装饰）
    try:
        if _FLOW_TIMER['t'] is not None:
            _FLOW_TIMER['t'].stop()
        _FLOW_BARS[:] = []
    except Exception:
        _err()


_FLOW_FILL_CACHE = {}


def _flow_grad_pm(w, h, c1, c2, dpr):
    """R20-10c：把「c1→c2 横向渐变」按尺寸预渲染成 QPixmap（不含圆角形状）。

    只在尺寸 / 颜色 / dpr 变化时重建；绘制时当 texture brush 铺进原来的圆角矩形，
    形状（含抗锯齿边）仍走原来的 drawRoundedRect 路径，因此逐像素等价（实测 max_delta≤1）。
    """
    key = (round(w, 2), round(h, 2), c1.rgba(), c2.rgba(), round(dpr, 2))
    pm = _FLOW_FILL_CACHE.get(key)
    if pm is not None:
        return pm
    wd = max(1, int(math.ceil(w * dpr)))
    hd = max(1, int(round(h * dpr)))
    img = QImage(wd, hd, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    ip = QPainter(img)
    ip.setPen(Qt.NoPen)
    g = QLinearGradient(0, 0, wd, 0)
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    ip.setBrush(g)
    ip.drawRect(QRectF(0, 0, wd, hd))
    ip.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    if len(_FLOW_FILL_CACHE) > 96:
        _FLOW_FILL_CACHE.clear()
    _FLOW_FILL_CACHE[key] = pm
    return pm


def _draw_flow_bar(p, r, ratio, t, c1, c2):
    """R20-4：条本身 / 条带共用同一套像素画法（保证两条路径像素等价）。"""
    p.setRenderHint(QPainter.Antialiasing)
    rr = r.adjusted(0, 2, 0, -2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 22))
    p.drawRoundedRect(rr, 4, 4)
    if ratio > 0:
        fill = QRectF(rr.x(), rr.y(), rr.width() * ratio, rr.height())
        _dpr = float(p.device().devicePixelRatioF()) if p.device() is not None else 1.0
        br = QBrush(_flow_grad_pm(fill.width(), fill.height(), c1, c2, _dpr))
        br.setTransform(QTransform().translate(fill.x(), fill.y()))
        p.setBrush(br)
        p.drawRoundedRect(fill, 4, 4)
        import math
        phase = (t / 3.2) % 1.0
        band_w = fill.width() * 0.35
        bx = fill.x() - band_w + (fill.width() + band_w) * phase
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawRoundedRect(QRectF(bx, fill.y(), band_w, fill.height()), 4, 4)


class FlowStrip(QWidget):
    """R20-4：把同一行的多条流光并成「一个不透明条带、一拍一个脏区」。

    - 条带只覆盖该行各条 bar 的并集（不含卡片正文，避免把数字烘进底）；
    - 底走页级 deep capture（取底期间 bar 自身不画，见 _FLOW_PAINT_OFF）；
    - 4 条 bar 不再各自 update，改由 _flow_tick_all 每拍只 update 条带一次。
    """

    def __init__(self, parent, bars):
        super().__init__(parent)
        self._bars = list(bars)
        self._geo = None
        self._rects = []          # R20-4：每条 bar 在「页坐标」下的矩形（绘制时等价平移，绕开 mapTo(self)）
        self._row_y = None
        self._band_need = True
        self._band_src_window = True
        self._tick_need = False
        self._band = True
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # R21：新建子控件默认几何是 100×30 @(0,0)，正好压在首张 KPI 卡标题上；
        # 页面在它之后才 show 时会把它顺带显示出来，opaque + 取底期间 return
        # 就留下那块「首帧黑块」。未同步几何前必须保持隐藏。
        self.hide()
        for b in self._bars:
            b._strip = self
            b._band = False
            b.setAttribute(Qt.WA_OpaquePaintEvent, False)
            b.setAttribute(Qt.WA_NoSystemBackground, False)
            # R01：b 可能不在列表里（切页重建的常规路径），先判存在，避免把预期分支记成异常
            if b in _BAND_ITEMS:
                _BAND_ITEMS.remove(b)
        _FLOW_STRIPS.append(self)

    def _sync_geo(self):
        pr = self.parentWidget()
        if pr is None:
            return False
        u = None
        rects = []
        for b in self._bars:
            try:
                if not b.isVisible():
                    continue
                r = QRect(b.mapTo(pr, QPoint(0, 0)), b.size())
            except Exception:
                _err()
                continue
            rects.append((b, r))
            u = r if u is None else u.united(r)
        if u is None:
            return False
        # R21：布局未定稿时 bar 还是子控件默认几何 100×30，union 高 30——
        # 此时 setGeometry 就是把条带摆到首卡标题上（首帧黑块的落点）。
        # 流光条固定高 10，union 高于 16 一律视为未定稿，本拍不显示。
        if u.height() > 16:
            return False
        # R20-4：条带是 bar 的兄弟（不是祖先），QWidget.mapTo(self) 无定义——
        # 实测返回垃圾坐标（273,131）而条带其实就在 (20,80)。统一缓存「页坐标矩形」，
        # 绘制时减去条带自身左上角即可。
        self._rects = rects
        if self._geo != u:
            self._geo = QRect(u)
            _BANDS.pop(id(self), None)
            self._band_need = True
            self.setGeometry(u)
            if os.environ.get('XH_STRIP_DBG'):
                print('[strip] geo -> %s' % (u,), flush=True)
        # R21：底未就绪（首帧/刚被判白底）时条带保持隐藏，等下一拍补帧；
        # 隐藏的条带不会画白块，用户看到的是卡片自身背景。
        if _BANDS.get(id(self)) is None:
            self._hide_wait = getattr(self, '_hide_wait', 0) + 1
            if self.isVisible():
                self.hide()
            _band_capture(self)
            if _BANDS.get(id(self)) is None:
                if self._hide_wait <= 120:
                    QTimer.singleShot(16, self._deferred_show)
                else:
                    # 兜底：长时间取不到底 → 条带下架，让 bar 自己画（旧行为）
                    for b in self._bars:
                        try:
                            b._band = True
                            if b not in _BAND_ITEMS:
                                _BAND_ITEMS.append(b)
                        except Exception:
                            _err()
                return True
        self._hide_wait = 0
        if not self.isVisible():
            self.show()
            self.raise_()
        elif getattr(self, '_need_raise', True):
            self.raise_()       # 重建/切页后卡片可能压在条带上
        self._need_raise = False
        return True

    def _deferred_show(self):
        """R21：补一帧后重新取底并显示（解决 150% 状态页首帧白块）。"""
        if _BANDS.get(id(self)) is None:
            _band_capture(self)
        if _BANDS.get(id(self)) is not None:
            try:
                self.show()
                self.raise_()
                self._hide_wait = 0
                self.update()
            except Exception:
                _err()
            return
        self._sync_geo()

    def paintEvent(self, ev):
        if self._geo is None or self.geometry().isEmpty():
            # R21：几何未同步（默认 100×30）→ 不参与绘制，也不许停在可见态。
            self.hide()
            return
        if _FLOW_PAINT_OFF[0]:
            # R20-4：取底期间条带这一层不画，窗口合成留下的就是真实底。
            # R21：但本控件是 WA_OpaquePaintEvent，直接 return 会在backing store
            # 留下未绘制黑矩形；有缓存底就照常铺底（纯 blit，不会再触发取底）。
            _pm0 = _BANDS.get(id(self))
            if _pm0 is not None and not _pm0.isNull() and _pm0.size() == self.size():
                _p0 = QPainter(self)
                _p0.drawPixmap(0, 0, _pm0)
                _p0.end()
            return
        if self._band_need or _BANDS.get(id(self)) is None:
            _FLOW_PAINT_OFF[0] = True
            try:
                _band_capture(self)
            finally:
                _FLOW_PAINT_OFF[0] = False
            self._band_need = False
            if os.environ.get('XH_STRIP_DBG'):
                _pm = _BANDS.get(id(self))
                print('[strip] paint geo=%s band=%s size=%s win=%s' % (
                    self.geometry(), _pm is not None,
                    (None if _pm is None else (_pm.width(), _pm.height())),
                    self.mapTo(self.window(), QPoint(0, 0))), flush=True)
                _rs = []
                for _b, _r in self._rects:
                    _q = QRect(_b.mapTo(self.window(), QPoint(0, 0)), _b.size())
                    _rs.append((_q.x(), _q.y(), _q.width(), _q.height()))
                print('[strip] rects(win)=%s' % (_rs,), flush=True)
        p = QPainter(self)
        _band_blit(p, self)
        if self._geo is not None:
            ox, oy = self._geo.x(), self._geo.y()
            for b, r in self._rects:
                try:
                    if not b.isVisible():
                        continue
                    _draw_flow_bar(p, r.translated(-ox, -oy), b._ratio, b._t, b._c1, b._c2)
                except Exception as _e:
                    if os.environ.get('XH_STRIP_DBG'):
                        print('[strip] bar EXC %r' % _e, flush=True)
        p.end()


class _TwoColDelegate(QStyledItemDelegate):
    """R20-10c：模型列表两列网格的自绘代理（左对齐文字 + 选中/悬停底）。"""

    def __init__(self, listw, parent=None):
        super().__init__(parent)
        self._listw = listw

    def sizeHint(self, opt, idx):
        # 强制按网格单元尺寸布局：否则长模型名在 150% 下会把 IconMode 挤成单列
        gs = self._listw.gridSize()
        return gs if gs.isValid() else super().sizeHint(opt, idx)

    def paint(self, p, opt, idx):
        p.save()
        r = opt.rect.adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        if opt.state & QStyle.State_Selected:
            p.setBrush(QColor(255, 255, 255, 42))
            p.drawRoundedRect(r, 4, 4)
        elif opt.state & QStyle.State_MouseOver:
            p.setBrush(QColor(255, 255, 255, 18))
            p.drawRoundedRect(r, 4, 4)
        p.setPen(opt.palette.color(QPalette.Text))
        p.setFont(opt.font)
        txt = opt.fontMetrics.elidedText(str(idx.data(Qt.DisplayRole) or ''),
                                         Qt.ElideRight, r.width() - 14)
        p.drawText(r.adjusted(7, 0, -7, 0), Qt.AlignLeft | Qt.AlignVCenter, txt)
        p.restore()


class _TwoColList(QListWidget):
    """R20-10c：6 条模型 2×3 一屏全显示。

    原单列 82px 视口只能放约 3 行，6 条时内部出现纵向滚动条（vmax=2）；
    改 IconMode + 两列网格后 3 行即放下，不需要内部滚动。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setWordWrap(False)
        self.setIconSize(QSize(0, 0))
        self.setSpacing(4)
        # 6 条 2×3 恰好一屏（vmax=0），滚动条没有存在意义：
        # 实测 IconMode 在 range=0 时仍会留出一条可见的空竖条（688→670 少 18px），故显式关闭。
        self.setVerticalScrollBarPolicy(SCROLL_OFF)
        self.setHorizontalScrollBarPolicy(SCROLL_H)
        self.setItemDelegate(_TwoColDelegate(self, self))
        self._row_h = 22
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.viewport():
            # Resize 事件里 viewport().width() 还是旧值，必须用 ev.size()
            if ev.type() == QEvent.Resize:
                self._reflow(ev.size().width())
            elif ev.type() == QEvent.Show:
                self._reflow()
        return super().eventFilter(obj, ev)

    def _reflow(self, vw=None):
        if vw is None:
            vw = self.viewport().width()
        if vw > 40:
            self.setGridSize(QSize(max(120, (vw - 8) // 2), self._row_h))


def _flow_page(w):
    """条带的父级必须是「页」本身（st），不能是更外层的 QStackedWidget——
    挂到栈上会被当成未托管的子控件，既不参与页的合成也不随页隐藏。"""
    p = w.parentWidget()
    last = p
    while p is not None and not isinstance(p, QStackedWidget):
        last = p
        p = p.parentWidget()
    return last if p is not None else None


_STRIP_RB_TIMER = [None]


def _flow_strip_rebuild_soon(ms=120):
    """R20-10：把一串 resize 事件合并成一次重建。
    原来每个 Resize 事件都排一个 singleShot(0)，150% 下窗口被系统反复钳制时
    会滚成几十次「先拆全部条带再建」；改为共用单发定时器去抖。"""
    t = _STRIP_RB_TIMER[0]
    if t is None:
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(_flow_strip_rebuild)
        _STRIP_RB_TIMER[0] = t
    t.stop()
    t.start(max(0, int(ms)))


def _flow_strip_rebuild():
    """按「同一行」给流光条建条带；重排/切页后重建（旧条带先下架）。"""
    if not _FLOW_STRIP_ON:
        return
    if os.environ.get('XH_STRIP_DBG'):
        print('[strip] rebuild bars=%d' % len(_FLOW_BARS), flush=True)
    for s in list(_FLOW_STRIPS):
        try:
            _FLOW_STRIPS.remove(s)
            _BANDS.pop(id(s), None)
            for b in s._bars:
                try:
                    b._strip = None
                except Exception:
                    _err()
            s.hide()
            s.deleteLater()
        except Exception:
            _err()
    for bar in list(_FLOW_BARS):
        try:
            pr = _flow_page(bar)
            if pr is None:
                continue
            pt = bar.mapTo(pr, QPoint(0, 0))
            grp = None
            for s in _FLOW_STRIPS:
                if s.parentWidget() is pr and s._row_y is not None \
                        and abs(s._row_y - pt.y()) <= 6:
                    grp = s
                    break
            if grp is None:
                s = FlowStrip(pr, [bar])
                s._row_y = pt.y()
                if os.environ.get('XH_STRIP_DBG'):
                    _ch = []
                    _q = bar
                    while _q is not None:
                        _ch.append('%s(%s)' % (type(_q).__name__, _q.objectName() or '-'))
                        _q = _q.parentWidget()
                    print('[strip] new strip parent=%s row_y=%d chain=%s'
                          % (type(pr).__name__, pt.y(), '>'.join(_ch)), flush=True)
            else:
                grp._bars.append(bar)
                bar._strip = grp
                bar._band = False
                bar.setAttribute(Qt.WA_OpaquePaintEvent, False)
                bar.setAttribute(Qt.WA_NoSystemBackground, False)
                # R01：同上，常规重建路径不应产生"异常计数"
                if bar in _BAND_ITEMS:
                    _BAND_ITEMS.remove(bar)
                grp._geo = None
                _BANDS.pop(id(grp), None)
                grp._band_need = True
        except Exception:
            _err()
            continue
class FlowProgressBar(QWidget):
    """带流光的进度条（对应网页 capbar / capflow：进度 + 循环流光）。"""

    def __init__(self, color=UI_C['INK_2'], color2=UI_C['SERIES_MEM'], parent=None):
        super().__init__(parent)
        self._c1 = QColor(color)
        self._c2 = QColor(color2)
        self._ratio = 0.0
        self._t = 0.0
        self._mov_t = time.perf_counter()   # R20-8：最近一次 ratio 变化（仅活跃条流光用）
        self.setFixedHeight(10)
        # R15：四条流光原来各自一个 150 ms 定时器，相位漂移后同一帧里会分别触发
        # 一次「自身 → 卡片 → 网格 → 页 → 背景」的父链补底。改为一个共享节拍统一
        # 驱动（相位与速度不变，像素输出等价），父链每拍只补一次。
        # R16：再进一步——相位仍由共享节拍推进，但绘制搬到整页叠加层，
        # 一次拍只画叠加层一次（脏区=全部装饰并集）。
        # R19：方案乙·带内版——默认每条流光自带不透明底（XH_BAND=0 可回退到叠加层）。
        self._band = os.environ.get('XH_BAND', '1') == '1'
        if self._band:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            _BAND_ITEMS.append(self)
        if _DECO['ov'] is not None and 'noflow' not in XH_PROBE and not self._band:
            _deco_add(self, 'flow')
        elif _DECO['ov'] is None or self._band:
            _flow_attach(self)

    def _flow_step(self):
        # X14：ratio<=0 暂停空转；R03：隐藏页只跳过节拍不丢相位
        if self._ratio <= 0 or not self.isVisible():
            return
        # R20-8：只让「正在充的那一条」流光（2.5 s 内 ratio 变过），其余静止省 CPU。
        if XH_FLOW_ACTIVE and (time.perf_counter() - self._mov_t) > 2.5:
            return
        if XH_FLOW_PHASE is not None:
            self._t = XH_FLOW_PHASE      # R15：零像素差取证用（固定流光相位）
        else:
            self._t += XH_FLOW_MS / 1000.0   # R20-8：随主节拍补偿，速度与 150 ms 基准一致
        # R20-4：已并入条带 → 本拍只记「需要重画」，由 _flow_tick_all 统一 update 一次
        _st = getattr(self, '_strip', None)
        if _st is not None:
            _st._tick_need = True
            return
        if _deco_on() and not self._band:
            return                       # R16：整拍由 DecoOverlay 统一 update
        if self._band and getattr(self, '_band_need', True):
            _band_capture(self)          # 一拍内至多重建一次（失效后才建）
            self._band_need = False
        self.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        if _DECO['ov'] is not None:
            _deco_dirty(self)
        else:
            _flow_attach(self)

    def set_ratio(self, ratio):
        _old = self._ratio
        self._ratio = max(0.0, min(1.0, float(ratio)))
        if abs(self._ratio - _old) > 1e-6:
            self._mov_t = time.perf_counter()   # R20-8：正在充 → 允许该条流光
        # R17-2：单一路径——已并入叠加层时只改数据，不直接标脏；
        # 绘制由 DecoOverlay._tick 每拍统一提交一次（原来 set_ratio 的脏区与拍子的并集重叠，
        # 同帧画两次并叠一层 alpha，是流光条 diff 残差 +18/255 的来源）。
        _st = getattr(self, '_strip', None)
        if _st is not None:
            # R20-4：0 → 非 0 的跃迁必须立即出一帧（不许等满 150ms），
            # 其余照旧等拍子；条带一拍只有一区。
            if _old <= 0 < self._ratio:
                _st._tick_need = True
                if _st._sync_geo():
                    _st.update()
            return
        if not getattr(self, '_deco_active', False):
            self.update()

    def setValue(self, value):
        # 兼容 QProgressBar 调用；0-100 映射为 0-1
        self.set_ratio(float(value) / 100.0)

    def paintEvent(self, ev):
        if _FLOW_PAINT_OFF[0]:
            # R21：条带取底期间条本身不画（否则把旧流光烘进底）；但自带底
            # （_band=True，opaque）时必须照常铺底，否则 backing store 留下未绘制黑条。
            if self._band:
                _pm0 = _BANDS.get(id(self))
                if _pm0 is not None and not _pm0.isNull() and _pm0.size() == self.size():
                    _p0 = QPainter(self)
                    _p0.drawPixmap(0, 0, _pm0)
                    _p0.end()
            return
        if getattr(self, '_strip', None) is not None:
            return          # R20-4：已并入条带，由条带统一绘制
        if _deco_on() and getattr(self, '_deco_active', False):
            return          # R16：改由叠加层绘制（像素等价，见 _draw_flow）
        p = QPainter(self)
        if self._band:
            _band_blit(p, self)      # 先贴该条正下方的底（含压在其上的文本），再不透明地画流光
        _draw_flow_bar(p, self.rect(), self._ratio, self._t, self._c1, self._c2)
        p.end()


class ShimmerCover(QWidget):
    """骨架屏微光覆盖层（对应网页 shimmer：微光在控件上循环扫过）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        # R20-3：不在 __init__ 起表——创建后立刻 hide() 的控件不会收到 hideEvent，
        # 33ms 表会一直空转（实测 21.3 次/s、零 paint）。改由 showEvent 起。  

    def _tick(self):
        self._t += 0.033
        self.update()

    # G5：隐藏页骨架屏动画按可见性启停
    def showEvent(self, ev):
        if 'noanim' not in R10 and 'freeze' not in R10 and not self._timer.isActive():
            self._timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        phase = (self._t / 1.6) % 1.0
        band_w = self.width() * 0.4
        x = -band_w + (self.width() + band_w * 2) * phase
        g = QLinearGradient(x, 0, x + band_w, 0)
        g.setColorAt(0, QColor(255, 255, 255, 0))
        g.setColorAt(0.5, QColor(255, 255, 255, 42))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), g)


class ChipGroup(QWidget):
    """可勾选的 chip 按钮组（XH-10）。

    multi=True 时为多选（返回所有选中值）；multi=False 时为单选（互斥）。
    选中态用主色填充 + 辉光，未选中为玻璃描边。
    """

    toggled = Signal()

    def __init__(self, options, multi=False, default=None, cols=None, parent=None):
        super().__init__(parent)
        self.multi = multi
        self._values = []
        self._buttons = []
        # R014（领导）：cols=N 时网格换行（窄参数卡内 chips 不再截断）
        if cols:
            lay = QGridLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setHorizontalSpacing(8)
            lay.setVerticalSpacing(8)
        else:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
        for opt in options:
            if isinstance(opt, (tuple, list)):
                val, label = opt[0], opt[1]
            else:
                val, label = opt, opt
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty('chip', True)
            b.setMinimumHeight(30)
            # R020：按文字实测给最小宽，窄参数卡内 2x/10x 等不被裁切
            try:
                _fm = b.fontMetrics()
                b.setMinimumWidth(_fm.horizontalAdvance(label) + 24)
            except Exception:
                _err()
            b.clicked.connect(lambda _c, x=val: self._on_click(x))
            self._buttons.append((val, b))
            if cols:
                lay.addWidget(b, (len(self._buttons) - 1) // cols,
                              (len(self._buttons) - 1) % cols)
            else:
                lay.addWidget(b)
            if default is not None and (val == default or (multi and val in (default or []))):
                b.setChecked(True)
                self._values.append(val)
        if cols:
            lay.setColumnStretch(cols, 1)  # 网格分支：QGridLayout 无 addStretch
        else:
            lay.addStretch(1)

    def _on_click(self, val):
        if self.multi:
            checked = [v for v, b in self._buttons if b.isChecked()]
            self._values = checked
        else:
            for v, b in self._buttons:
                if v == val:
                    b.setChecked(True)
                    self._values = [v]
                else:
                    b.setChecked(False)
        self.toggled.emit()

    def value(self):
        if self.multi:
            return list(self._values)
        return self._values[0] if self._values else None

    def set_value(self, val):
        if self.multi:
            vals = val or []
            self._values = list(vals)
            for v, b in self._buttons:
                b.setChecked(v in vals)
        else:
            self._values = [val] if val is not None else []
            for v, b in self._buttons:
                b.setChecked(v == val)

    def clear(self):
        """清空所有选中（XH-1 双向联动用）。"""
        self.set_value([] if self.multi else None)


# R15-A05 归档：EnergyBox 类（未被引用，保留源码待清理）
class EnergyBox(QFrame):
    """能量盒：入场滑入 + 持续辉光循环（对应网页 ebIn / ebGlow）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('energybox')
        self.setStyleSheet(
            'QFrame#energybox{background:rgba(14,30,52,0.72);'
            'border:1px solid rgba(122,190,255,0.28);border-radius:14px;}')
        self._glow = 0.0
        # R007（XH-07）：辉光呼吸 50ms 过密，降为 200ms（步长×4 保持周期）
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        if 'noanim' not in R10 and 'freeze' not in R10:
            self._timer.start()

    def showEvent(self, ev):
        if 'hidestop' in R10 and 'noanim' not in R10 and not self._timer.isActive():
            self._timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        if 'hidestop' in R10:
            self._timer.stop()
        super().hideEvent(ev)

    def play_enter(self, delay=0):
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b'opacity', self)
        anim.setStartValue(0)
        anim.setEndValue(1)
        anim.setDuration(600)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        if delay:
            QTimer.singleShot(delay, anim.start)
        else:
            anim.start()
        self._enter_anim = anim
        self._enter_eff = eff

    def _tick(self):
        import math
        self._glow += 0.2
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        import math
        g = 0.5 + 0.5 * math.sin(self._glow * (2 * math.pi / 4.5))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(self._glow_color()[0], self._glow_color()[1],
                              self._glow_color()[2], int(20 + 40 * g)), 1.4))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)

    def _glow_color(self):
        return (122, 190, 255)


DARK_QSS = _t("""
QMainWindow { background: transparent; }
QWidget#root { background: transparent; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QGraphicsView { background: transparent; border: none; }
QGraphicsView QWidget { background: transparent; border: none; }
QLabel { color: %(TXT)s; background: transparent; }
QLabel#h1 { font-size: 18px; font-weight: 600; color: @INK_0@; }
QLabel#sub { color: %(SUB)s; font-size: 12px; }
QLabel#big { font-size: 30px; font-weight: 700; color: %(ACCENT)s; }
QLabel#big[compact="true"] { font-size: 15px; }
QLabel#big_empty { font-size: 18px; font-weight: 600; color: @INK_3@; }
QLabel#big_empty[compact="true"] { font-size: 12px; }
QGroupBox {
    background: transparent;
    border: none;
    border-radius: 14px; margin-top: 30px; padding: 0 6px 6px 6px;
    font-weight: 600; color: transparent;
}
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px;
    color: transparent; background: transparent; }
QPushButton {
    background: rgba(56,226,255,0.13);
    color: @INK_1@; border: 1px solid rgba(56,226,255,0.30);
    border-radius: 8px; padding: 7px 16px; font-weight: 500;
}
QPushButton:hover { background: rgba(56,226,255,0.24); border-color: rgba(150,210,255,0.55); color: @INK_0@; }
QPushButton:pressed { background: rgba(40,150,190,0.30); }
QPushButton:disabled { color: @INK_3@; background: rgba(70,80,98,0.55); }
QPushButton#danger { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 rgba(178,138,138,0.82), stop:1 rgba(145,108,112,0.85)); border-color: rgba(205,175,175,0.35); }
QPushButton#danger:hover { background: rgba(192,152,152,0.9); }
QPushButton#ghost {
    background: rgba(56,226,255,0.14); color: @INK_1@;
    border: 1px solid rgba(120,190,255,0.35); padding: 6px 12px; }
QPushButton#ghost:hover { background: rgba(56,226,255,0.26); color: @INK_0@; }
QPushButton[chip="true"] {
    background: rgba(255,255,255,0.05); color: @INK_1@;
    border: 1px solid rgba(120,190,255,0.25); border-radius: 14px;
    padding: 5px 14px; font-weight: 500; }
QPushButton[chip="true"]:hover { background: rgba(56,226,255,0.18); color: @INK_0@; }
QPushButton[chip="true"]:checked {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(56,226,255,0.92), stop:1 rgba(155,107,255,0.92));
    color: @INK_0@; border: 1px solid rgba(120,190,255,0.75);
    font-weight: 700; }
QProgressBar { background: rgba(255,255,255,0.07); border: 1px solid rgba(120,190,255,0.22);
    border-radius: 6px; text-align: center; color: @INK_1@; max-height: 15px; font-size: 11px; }
QProgressBar::chunk { border-radius: 6px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %(ACCENT)s, stop:1 %(ACCENT2)s); }
QTabWidget::pane { border: 1px solid rgba(120,190,255,0.16); border-radius: 14px;
    background: rgba(28,36,48,0.40); top: -1px; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab { background: transparent; color: @INK_2@; padding: 7px 16px;
    margin-right: 4px; border-radius: 8px; border-bottom: 2px solid transparent;
    font-size: 12px; font-weight: 500; letter-spacing: 0.8px; }
QTabBar::tab:hover { color: @INK_1@; background: rgba(120,190,255,0.10); }
/* R23-C5：Tab 与卡片标题（11pt≈14.7px 加粗大写）此前同为 13px 只靠渐变区分 →
   改为 12px 次级导航态 + 下缘 2px 青线；层级：页头 19px > 卡片标题 > Tab */
QTabBar::tab:selected { color: @ACCENT_SOFT@;
    background: rgba(56,226,255,0.12); border-bottom: 2px solid %(ACCENT)s;
    font-weight: 600; }
QComboBox, QSpinBox, QDoubleSpinBox {
    background: rgba(255,255,255,0.06); color: @INK_0@;
    border: 1px solid rgba(120,190,255,0.61); border-radius: 8px; padding: 5px 8px;
    min-height: 24px;
}
QComboBox:hover, QSpinBox:hover { border-color: rgba(195,205,222,0.85); }
QComboBox QAbstractItemView { background: @BG_POPUP@; color: @INK_0@;
    selection-background-color: %(ACCENT2)s; border: 1px solid rgba(120,190,255,0.3);
    outline: none; padding: 4px; }
QComboBox QAbstractItemView::item { padding: 6px 10px; min-height: 22px; border-radius: 6px; }
QComboBox QAbstractItemView::item:hover { background: rgba(120,190,255,0.18); }
QComboBox QAbstractItemView::item:selected { background: rgba(155,107,255,0.55); color: @INK_0@; }
QPlainTextEdit, QLineEdit {
    background: rgba(255,255,255,0.06); color: @INK_0@;
    border: 1px solid rgba(120,190,255,0.61); border-radius: 8px; padding: 6px;
    selection-background-color: %(ACCENT2)s; }
QPlainTextEdit[readonly="true"] { color: @INK_CODE@; font-family: Consolas, monospace; font-size: 12px; }
QListWidget { background: rgba(20,28,40,0.55); color: %(TXT)s;
    border: 1px solid rgba(120,190,255,0.20); border-radius: 14px; padding: 4px; }
QListWidget::item { padding: 2px; border-radius: 8px; }
QListWidget::item:hover { background: rgba(120,190,255,0.10); }
QListWidget::item:selected { background: rgba(155,136,175,0.55); color: #fff; }
QTableWidget { background: rgba(20,28,40,0.55); color: %(TXT)s;
    border: 1px solid rgba(120,190,255,0.20); border-radius: 14px; gridline-color: rgba(120,190,255,0.15); }
QTableWidget::item:selected { background: rgba(155,136,175,0.55); color: #fff; }
QHeaderView::section { background: rgba(48,58,74,0.9); color: @INK_1@;
    border: none; padding: 5px; font-weight: 600; }
QMenu { background: @BG_POPUP@; color: @INK_0@; border: 1px solid rgba(120,190,255,0.3); border-radius: 8px; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background: rgba(155,107,255,0.55); }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(120,190,255,0.52); border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: rgba(120,190,255,0.68); }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: rgba(120,190,255,0.52); border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: rgba(120,190,255,0.68); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QTabWidget#cfgInner::pane { background: rgba(34,44,58,0.55);
    border: 1px solid rgba(120,190,255,0.22); border-radius: 14px; }
QTabWidget#cfgInner QTabBar::tab { padding: 7px 16px; }
QDialog { background: @BG_RAISED@; }
/* ===== 左侧导航栏 NavRail ===== */
QFrame#navrail {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 rgba(34,42,56,0.82), stop:1 rgba(26,32,44,0.78));
    border: 1px solid rgba(120,190,255,0.18); border-radius: 14px;
}
QLabel#navbrand { font-size: 15px; font-weight: 700; color: @INK_0@; }
QLabel#navsub { font-size: 11px; color: %(SUB)s; }
QPushButton#navitem {
    background: transparent; color: @INK_2@; border: 1px solid transparent;
    border-radius: 8px; padding: 10px 13px; text-align: left;
    font-size: 14px; font-weight: 500;
}
QPushButton#navitem:hover { background: rgba(56,226,255,0.14); color: @INK_1@; }
QPushButton#navitem:checked {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(56,226,255,0.92), stop:1 rgba(155,107,255,0.92));
    color: @ACCENT_INK@; border: 1px solid rgba(120,220,255,0.75); font-weight: 600;
}
/* ===== R023-C 质感精修：焦点态/悬浮反馈/工具提示/消息框/微控件 ===== */
QToolTip { background: @BG_RAISED@; color: @INK_0@;
    border: 1px solid rgba(56,226,255,0.5); border-radius: 6px;
    padding: 5px 8px; font-size: 12px; }
QMessageBox { background: @BG_RAISED@; }
QMessageBox QLabel { color: @INK_0@; font-size: 12px; background: transparent; }
QMessageBox QPushButton { min-width: 88px; min-height: 30px; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QLineEdit:focus, QTimeEdit:focus {
    border: 1px solid rgba(56,226,255,0.85); background: rgba(255,255,255,0.10); }
QCheckBox { color: %(TXT)s; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid rgba(120,190,255,0.35); background: rgba(255,255,255,0.06); }
QCheckBox::indicator:checked { background: %(ACCENT)s;
    border: 1px solid rgba(56,226,255,0.85); }
QCheckBox::indicator:disabled { border-color: rgba(120,190,255,0.18); }
QTableWidget::item { padding: 0 8px; }
QHeaderView::section { padding: 4px 8px; }
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {
    background: rgba(56,226,255,0.55); }
QComboBox QAbstractItemView { selection-background-color: rgba(56,226,255,0.45); }
""") % {'TXT': TXT, 'SUB': SUB, 'ACCENT': ACCENT, 'ACCENT2': ACCENT2,
       'MORANDI_GREEN': MORANDI_GREEN}


class Backdrop(QWidget):
    """星空背景：真实星虹图（bg-starbow.jpg）+ 多层径向辉光 + 缓慢脉冲，
    并维护一份缓存的模糊图供 GlassCard 实现真正的 backdrop-filter。"""

    BLUR_SCALE = 0.5

    @classmethod
    def _blur_cache_file(cls, w, h):
        """R251：模糊底图按「尺寸 + 缩放 + 主题 + 版本」落盘缓存。
        模糊结果只跟这四项有关，跨进程复用后首帧那一帧只剩读 PNG（~2 ms）。"""
        base = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
                            'XingHongPanel', 'cache')
        return os.path.join(base, 'blur_%dx%d_s%s_%s_v%s.png'
                            % (w, h, cls.BLUR_SCALE,
                               (XH_ADV.get('theme') or 'star'), ux.APP_VERSION))

    def __init__(self):
        global BACKDROP
        super().__init__()
        self.setObjectName('root')
        # R12（XH-083 层 A）：标记为不透明——paintEvent 已用烘焙图铺满整窗，
        # 不给 Qt「全窗都要重画」的理由。呼吸点每次 update 只应重绘自身 16×16，
        # 而不是把整个 980×700 背景+导航一起重画（像素输出不变）。
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._t = 0.0
        # 加载星虹图（与网页端同一张；frozen 走随包 assets）
        self._img = QPixmap()
        img_path = resource_path(os.path.join('assets', 'bg-starbow.jpg'))
        if os.path.exists(img_path):
            self._img.load(img_path)
        # 生成固定星点（位置/相位/周期），叠加在图上的轻微闪烁
        self._stars = []
        x = 7
        for i in range(70):
            x = (x * 137 + 61) % 1000003
            phase = (x % 1000) / 1000.0
            period = 1.8 + (x % 400) / 100.0
            base_a = 14 + (x % 50)
            self._stars.append((phase, period, base_a))
        self._blur_pix = None
        self._blur_dirty = True
        # R007（D-04/XH-07）：辉光呼吸与星点闪烁已烘焙进静态帧（取呼吸中值相位），
        # 停用 10fps 全窗重绘定时器——空闲时整窗零重绘。
        # 模糊+饱和图只在启动一次与窗口尺寸变化时重建（背景主体是静态图）
        QTimer.singleShot(250, self._build_blur)
        BACKDROP = self

    def _static_bg(self, w, h):
        """缓存缩放后的静态背景图（仅尺寸变化时重建），避免每帧重缩放大图。"""
        if getattr(self, '_bg_cache', None) is None or self._bg_cache[0] != (w, h):
            pm = QPixmap(w, h)
            pp = QPainter(pm)
            pp.setRenderHint(QPainter.SmoothPixmapTransform)
            if not self._img.isNull():
                iw, ih = self._img.width(), self._img.height()
                sc = max(w / iw, h / ih)
                dw, dh = iw * sc, ih * sc
                pp.drawPixmap(QRectF((w - dw) / 2, (h - dh) / 2, dw, dh),
                              self._img, QRectF(0, 0, iw, ih))
            else:
                base = QLinearGradient(0, 0, w, h)
                base.setColorAt(0.0, QColor(UI_C['STAGE_1']))
                base.setColorAt(0.5, QColor(UI_C['STAGE_2']))
                base.setColorAt(1.0, QColor(UI_C['STAGE_3']))
                pp.fillRect(QRectF(0, 0, w, h), base)
            pp.end()
            self._bg_cache = ((w, h), pm)
        return self._bg_cache[1]

    def _draw_scene(self, p, w, h, t, darken=True):
        import math
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self._static_bg(w, h))
        # 多层径向辉光（对应网页 body 的三层 radial-gradient）
        glow = 0.5 + 0.5 * math.sin(t * (2.0 * math.pi / 11.0))
        for cx, cy, r, col, base_alpha, gamp in (
                (w * 0.85, h * -0.05, w * 0.55, QColor(147, 169, 209), 22, 1.0),
                (w * 0.05, h * 0.0, w * 0.5, QColor(155, 136, 175), 19, 0.85),
                (w * 0.5, h * 1.1, w * 0.7, QColor(148, 163, 184), 14, 0.7)):
            a = int(base_alpha + 26 * glow * gamp)
            c = QColor(col); c.setAlpha(a)
            g = QRadialGradient(cx, cy, r)
            g.setColorAt(0.0, c)
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(0, 0, w, h), g)
        # 整体压暗（对应网页 rgba(7,10,15,.56)→rgba(5,8,12,.70)）
        # 卡片模糊图不压暗（由卡片渐变统一压暗），避免双重压暗。
        if darken:
            dark = QLinearGradient(0, 0, 0, h)
            dark.setColorAt(0.0, QColor(7, 10, 15, 143))
            dark.setColorAt(1.0, QColor(5, 8, 12, 179))
            p.fillRect(QRectF(0, 0, w, h), dark)
        # 轻微闪烁星点
        p.setPen(Qt.NoPen)
        x = 7
        for i, (phase, period, base_a) in enumerate(self._stars):
            x = (x * 137 + 61) % 1000003
            px = x % max(1, int(w))
            py = ((x * 31 + 17) % (int(h) * 2)) % max(1, int(h))
            tw = 0.5 + 0.5 * math.sin(t * (2.0 * math.pi / period) + phase * 6.28)
            a = int(base_a * (0.35 + 0.65 * tw))
            p.setBrush(QColor(210, 232, 255, a))
            p.drawEllipse(QRectF(px, py, 1.5, 1.5))

    def paintEvent(self, ev):
        # R247：主题态下背景层换绘制参数——不再铺星虹图/辉光，直接用主题底色，
        # 保证「背景」这一类真的换色（而不是只改 QLabel 文字）。
        _tk = THEME_KIT.get(XH_ADV.get('theme'))
        if _tk is not None:
            p = QPainter(self)
            p.fillRect(ev.rect(), QColor(_tk['BG']))
            p.end()
            return
        w, h = self.width(), self.height()
        key = (w, h)
        _perf_mark('bg_b')
        if getattr(self, '_baked', None) is None or self._baked[0] != key:
            pm = QPixmap(w, h)
            pp = QPainter(pm)
            self._draw_scene(pp, w, h, 2.75, darken=True)  # 11s 呼吸的中值相位
            pp.end()
            self._baked = (key, pm)
        p = QPainter(self)
        _r = ev.rect()
        if not XH_BG_FIX or _r.width() >= w or _r.height() >= h:
            p.drawPixmap(0, 0, self._baked[1])
        else:
            # R254：只贴脏区——卡片/文字局部重绘不再整窗重贴 1000×731 烘焙图。
            # 星空本身仍按 (w,h) 缓存：尺寸+主题不变就不重画。
            p.drawPixmap(_r, self._baked[1], _r)
        p.end()
        _perf_mark('bg_e')

    def _build_blur(self):
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        _perf_mark('blur_b')
        cf = self._blur_cache_file(w, h)
        if os.path.isfile(cf):
            pm = QPixmap(cf)
            if not pm.isNull() and pm.width() == max(1, int(w * self.BLUR_SCALE)):
                self._blur_pix = pm
                _perf_mark('blur_cache_hit')
                _perf_mark('blur_e')
                return
        _perf_mark('blur_cache_miss')
        # R251：直接按 BLUR_SCALE 渲染场景，不再「全尺寸渲染→缩小」，
        # 省掉一张 1000×731 的 QPixmap 分配与平滑缩放。
        s = self.BLUR_SCALE
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        small = QPixmap(sw, sh)
        small.fill(Qt.transparent)
        pp = QPainter(small)
        pp.setRenderHint(QPainter.Antialiasing)
        pp.scale(s, s)
        self._draw_scene(pp, w, h, self._t, darken=False)
        pp.end()
        _perf_mark('blur_draw')
        scene = QGraphicsScene()
        item = scene.addPixmap(small)
        eff = QGraphicsBlurEffect()
        eff.setBlurRadius(12)
        item.setGraphicsEffect(eff)
        out = QPixmap(small.size())
        out.fill(Qt.transparent)
        rp = QPainter(out)
        scene.render(rp)
        rp.end()
        scene.removeItem(item)
        _perf_mark('blur_fx')
        # 莫兰迪化：降低饱和度（sat<1），不再额外加蓝，呈现灰调磨砂
        out = self._enhance(out, 0.72, 1.06, 0)
        self._blur_pix = out
        # R251：落盘放到 1.5 s 后，别让写盘（含安全软件扫描）挤进启动那几帧
        QTimer.singleShot(1500, lambda: self._save_blur_cache(cf, out))
        _perf_mark('blur_e')

    @staticmethod
    def _save_blur_cache(cf, pm):
        try:
            os.makedirs(os.path.dirname(cf), exist_ok=True)
            pm.save(cf, 'PNG')
        except Exception:
            pass

    @staticmethod
    def _enhance(pix, sat, bright, blue_lift=12):
        """对应 backdrop-filter 的 saturate，并补一点亮度与蓝，使磨砂玻璃
        在宽窗口下仍能呈现网页版那种青蓝辉光。"""
        try:
            import numpy as np
        except Exception:
            return pix
        img = pix.toImage().convertToFormat(QImage.Format_RGBA8888)
        ptr = img.bits()
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(img.height(),
                                                        img.width(), 4).copy()
        rgb = arr[:, :, :3].astype(np.float32)
        lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
        lum = lum[:, :, None]
        out = lum + (rgb - lum) * sat
        out = out * bright
        out[:, :, 2] += blue_lift
        out[:, :, 1] += blue_lift * 0.4
        arr[:, :, :3] = np.clip(out, 0, 255)
        out_img = QImage(arr.tobytes(), img.width(), img.height(),
                         img.bytesPerLine(), QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(out_img)

    def blur_pixmap(self):
        return self._blur_pix

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # 防抖：尺寸稳定后再重建（R020：复用同一 QTimer，避免拖拽累积大量定时器）
        if not hasattr(self, '_resize_timer'):
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._build_blur)
        self._resize_timer.stop()
        self._resize_timer.start(200)


class GlassCard(QGroupBox):
    """真正的玻璃卡片（对齐网页 .card）。

    玻璃背景（模糊裁剪 + 渐变 + 光扫 + 高光 + 描边 + 标题）整体渲染到一张
    缓存 QPixmap，只在尺寸/位置变化、以及一个慢速定时器（追踪背景辉光的
    缓慢呼吸）时重建；paintEvent 只贴图，避免背景动画每帧触发全部卡片重绘。
    """

    RADIUS = 14

    def __init__(self, title, tint=None):
        super().__init__(title)
        self._hover = False
        # 逐实例版式覆写：None/False 时与原先完全一致（其它页不受影响）
        self._radius_override = None
        self._border_override = None
        self._no_shadow = False
        # 可选整卡色调（RGBA 四元组）：用于「功耗统计」等需要网页版
        # 绿色/青色基调的卡片，避免只透出背后辉光导致的偶然色差。
        self._tint = tint
        self._glyph_kind = None
        self._glyph_pm = None
        self.setMouseTracking(True)
        self._cache = None
        self._ckey = None      # R254：缓存对应的 (w,h)；尺寸变了才重建
        # R015-B6：移除 1.6s 周期整卡重建——背景辉光/星点已静态烘焙（Backdrop 只贴
        # _baked、模糊图仅 resize 重建），周期重建是纯浪费；失效改由
        # move/resize/show/hover 事件驱动。

    def _invalidate(self):
        self._cache = None
        self._ckey = None
        if self.isVisible():
            self.update()

    def _anim_running(self):
        """R254：淡入动画进行中——此时尺寸抖动不该触发整卡缓存重建。"""
        _a = getattr(self, '_fade_anim', None)
        try:
            return _a is not None and _a.state() == QAbstractAnimation.Running
        except Exception:
            return False

    def set_glyph(self, kind, size=18, color=UI_C['INK_2']):
        """R24-V1：卡片标题线稿（复用 _nav_icon 同一套笔触），只画在缓存里，不参与布局。"""
        self._glyph_kind = kind
        self._glyph_pm = None
        if kind:
            try:
                self._glyph_pm = _nav_icon(kind, size, color).pixmap(size, size)
            except Exception:
                self._glyph_pm = None
        self._invalidate()

    def showEvent(self, ev):
        self._cache = None
        self._ckey = None
        super().showEvent(ev)

    def hideEvent(self, ev):
        # R015-B6：周期重建定时器已移除，隐藏无需停表
        super().hideEvent(ev)

    def moveEvent(self, ev):
        self._cache = None
        self._ckey = None
        super().moveEvent(ev)

    def resizeEvent(self, ev):
        self._cache = None
        self._ckey = None
        super().resizeEvent(ev)

    def enterEvent(self, ev):
        self._hover = True
        self.update()

    def leaveEvent(self, ev):
        self._hover = False
        self.update()

    def _render_cache(self, w, h):
        _perf_mark('card_b')
        r = self._radius_override or self.RADIUS
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # 1) 模糊背景裁剪（真正的 backdrop-filter）；主题态下没有星虹图，跳过
        bp = BACKDROP
        _tk = THEME_KIT.get(XH_ADV.get('theme'))
        blur = None if _tk is not None else (bp.blur_pixmap() if bp else None)
        if blur is not None and not blur.isNull():
            tl = self.mapTo(bp, QPoint(0, 0))
            s = bp.BLUR_SCALE
            p.save()
            p.setClipPath(path)
            target = QRectF(-tl.x(), -tl.y(),
                            blur.width() / s, blur.height() / s)
            p.drawPixmap(target, blur,
                         QRectF(0, 0, blur.width(), blur.height()))
            p.restore()

        # 2) 165° 玻璃渐变（R024-A5：Whaleglass 海军蓝玻璃 rgba(16,28,54,.55) 系）
        p.save()
        p.setClipPath(path)
        gg = QLinearGradient(0, 0, w * 0.27, h)
        if _tk is not None:
            # R247：主题态卡片改令牌色（高对比→近黑+白描边；暗夜护眼→墨绿）
            gg.setColorAt(0.0, QColor(*_tk['CARD_TOP']))
            gg.setColorAt(1.0, QColor(*_tk['CARD_BOT']))
        else:
            gg.setColorAt(0.0, QColor(16, 28, 54, 150))
            gg.setColorAt(1.0, QColor(8, 16, 36, 125))
        p.fillRect(QRectF(0, 0, w, h), gg)
        # 2b) 可选整卡色调（如功耗卡的网页版绿色基调），自上而下渐隐
        if self._tint is not None:
            r0, g0, b0, a0 = self._tint
            tg = QLinearGradient(0, 0, 0, h)
            tg.setColorAt(0.0, QColor(r0, g0, b0, a0))
            tg.setColorAt(1.0, QColor(r0, g0, b0, int(a0 * 0.35)))
            p.fillRect(QRectF(0, 0, w, h), tg)

        # 3) 对角光扫（::after，115°）——R024-A5：尾端改极光紫
        sweep = QLinearGradient(0, 0, w, h)
        sweep.setColorAt(0.0, QColor(255, 255, 255, 20))
        sweep.setColorAt(0.28, QColor(255, 255, 255, 0))
        sweep.setColorAt(0.72, QColor(56, 226, 255, 0))
        sweep.setColorAt(1.0, QColor(155, 107, 255, 18))
        p.fillRect(QRectF(0, 0, w, h), sweep)

        # 4) inset 顶部高光
        top_line = QLinearGradient(0, 0, w, 0)
        top_line.setColorAt(0.0, QColor(255, 255, 255, 0))
        top_line.setColorAt(0.5, QColor(255, 255, 255, 33))
        top_line.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(top_line, 1.0))
        p.setClipping(False)
        p.drawLine(QPointF(r, 1.0), QPointF(w - r, 1.0))
        p.setPen(QPen(QColor(120, 180, 255, 15), 1.0))
        p.drawLine(QPointF(r, h - 1.0), QPointF(w - r, h - 1.0))
        p.restore()

        # 4b) R015（领导）液态玻璃：边缘折射光缘 + 底部内侧暗缘（进深）
        rim = QLinearGradient(0, 0, w * 0.7, h)
        rim.setColorAt(0.0, QColor(255, 255, 255, 58))
        rim.setColorAt(0.35, QColor(255, 255, 255, 14))
        rim.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(rim, 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if not self._no_shadow:
            p.save()
            p.setClipPath(path)
            _shg = QLinearGradient(0, h - 26, 0, h)
            _shg.setColorAt(0.0, QColor(0, 0, 0, 0))
            _shg.setColorAt(1.0, QColor(0, 0, 0, 44))
            p.fillRect(QRectF(0, h - 26, w, 26), _shg)
            p.restore()

        # 5) 描边（非 hover 态）——R024-A5：Whaleglass 线 rgba(120,190,255,.18)+顶部高光
        p.setPen(QPen(self._border_override or
                      QColor(*(_tk['CARD_BD'] if _tk else (120, 190, 255, 46))), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # 6) 标题 + 霓虹下划线（对齐 .card h2）；紧凑模式标题更小、更靠上
        _ct = getattr(self, '_compact_title', False)
        f = QFont()
        f.setPointSizeF(9.5 if _ct else 11.0)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        f.setCapitalization(QFont.AllUppercase)
        p.setFont(f)
        p.setPen(QColor(_tk['CARD_INK']) if _tk else QColor(238, 247, 255))
        title = self.title()
        _ty = 3 if _ct else 8
        _th = 16 if _ct else 22
        # R24-V1：有 glyph 时线稿占标题左侧 18px + 6px 间距，无 glyph 时标题起点不变
        _gp = self._glyph_pm
        _tx = 20
        if _gp is not None and not _gp.isNull() and not _ct:
            p.drawPixmap(20, 8, _gp)
            _tx = 20 + _gp.width() + 6
        p.drawText(QRectF(_tx, _ty, w - _tx - 20, _th),
                   Qt.AlignLeft | Qt.AlignVCenter, title)
        tw = p.fontMetrics().horizontalAdvance(title)
        ug = QLinearGradient(_tx, 0, _tx + 34, 0)
        ug.setColorAt(0.0, QColor(56, 226, 255))
        ug.setColorAt(1.0, QColor(155, 107, 255, 0))
        p.setPen(QPen(ug, 2.0))
        _uly = 18 if _ct else 27
        p.drawLine(QPointF(_tx, _uly), QPointF(_tx + min(34, tw + 6), _uly))
        p.end()
        self._cache = pm
        _perf_mark('card_e')

    def paintEvent(self, ev):
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        key = (w, h)
        if self._cache is None or self._ckey != key:
            if (XH_CARD_FIX and self._cache is not None
                    and self._anim_running()):
                # R254：淡入动画期间不重建缓存——尺寸抖动只拉伸旧缓存，
                # 动画结束时由 fade_in 的 cleanup 失效，再按最终尺寸重画一帧。
                self._anim_stretched = True
                p = QPainter(self)
                p.drawPixmap(self.rect(), self._cache)
                p.end()
                return
            self._render_cache(w, h)
            self._ckey = key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self._hover:
            r = self._radius_override or self.RADIUS
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)
            p.setPen(QPen(QColor(56, 226, 255, 105), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.end()


def _attr_install():
    """R254：装归因 patch。只在 XH_ATTR 非空时调用；纯只读计数，不改绘制结果。"""
    if _ATTR['installed'] or not _ATTR['on']:
        return
    _ATTR['installed'] = True
    try:
        _bd_paint = Backdrop.paintEvent
        _gc_paint = GlassCard.paintEvent
        _gc_cache = GlassCard._render_cache
        _wg_update = QWidget.update
        _bd_resize = Backdrop.resizeEvent
        _gc_resize = GlassCard.resizeEvent
        _gc_move = GlassCard.moveEvent
        _gc_show = GlassCard.showEvent

        def _bd_paint_w(self, ev):
            if not _attr_live():
                return _bd_paint(self, ev)
            t0 = time.perf_counter()
            try:
                return _bd_paint(self, ev)
            finally:
                _r = ev.rect()
                _attr_rec('paint:Backdrop', self, t0,
                          '%dx%d' % (_r.width(), _r.height()))

        def _gc_paint_w(self, ev):
            if not _attr_live():
                return _gc_paint(self, ev)
            t0 = time.perf_counter()
            try:
                return _gc_paint(self, ev)
            finally:
                _attr_rec('paint:GlassCard', self, t0,
                          '%dx%d' % (self.width(), self.height()))

        def _gc_cache_w(self, w, h):
            if not _attr_live():
                return _gc_cache(self, w, h)
            t0 = time.perf_counter()
            try:
                return _gc_cache(self, w, h)
            finally:
                _attr_rec('card:cache', self, t0, '%dx%d' % (w, h))

        def _wg_update_w(self, *a, **k):
            if not _attr_live():
                return _wg_update(self, *a, **k)
            t0 = time.perf_counter()
            try:
                _t = _attr_now()
                _reason, _site = _attr_classify(sys._getframe(1))
                _nm = self.objectName() or type(self).__name__
                self._attr_upd = (_t, _nm, _reason, _site)
                _key = '%s|%s|%s' % (_nm, _reason, _site)
                _b = _ATTR['upd'].get(_key)
                if _b is None:
                    _ATTR['upd'][_key] = [1, round(_t, 1), round(_t, 1)]
                else:
                    _b[0] += 1
                    _b[2] = round(_t, 1)
            except Exception:
                _err()
            try:
                return _wg_update(self, *a, **k)
            finally:
                _ATTR['upd_cost_ms'] += (time.perf_counter() - t0) * 1000.0

        def _bd_resize_w(self, ev):
            _attr_mark_evt(self, 'resize')
            return _bd_resize(self, ev)

        def _gc_resize_w(self, ev):
            _attr_mark_evt(self, 'resize')
            return _gc_resize(self, ev)

        def _gc_move_w(self, ev):
            _attr_mark_evt(self, 'move')
            return _gc_move(self, ev)

        def _gc_show_w(self, ev):
            _attr_mark_evt(self, 'show')
            return _gc_show(self, ev)

        Backdrop.paintEvent = _bd_paint_w
        GlassCard.paintEvent = _gc_paint_w
        GlassCard._render_cache = _gc_cache_w
        QWidget.update = _wg_update_w
        Backdrop.resizeEvent = _bd_resize_w
        GlassCard.resizeEvent = _gc_resize_w
        GlassCard.moveEvent = _gc_move_w
        GlassCard.showEvent = _gc_show_w
    except Exception:
        _err()


def _nav_icon(kind, size=18, color=UI_C['INK_2']):
    """R13（P1-4）：统一线性图标——18×18、1.6px 描边、圆头圆角、四周 2.5px 留白。
    六项同一套笔触，不再混用 ⌂/⇄/✦/▶/⤢/⚙ 这类字重不一的字符。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    c = size / 2.0
    if kind == 'status':
        path = QPainterPath()
        path.moveTo(2.5, c)
        path.lineTo(5.5, c)
        path.lineTo(7.5, c - 4.5)
        path.lineTo(10.5, c + 4.5)
        path.lineTo(12.5, c)
        path.lineTo(15.5, c)
        p.drawPath(path)
    elif kind == 'routes':
        p.drawEllipse(QPointF(4.5, 4.5), 2.0, 2.0)
        p.drawEllipse(QPointF(13.5, 13.5), 2.0, 2.0)
        path = QPainterPath()
        path.moveTo(6.4, 4.5)
        path.cubicTo(11.6, 4.5, 6.4, 13.5, 11.6, 13.5)
        p.drawPath(path)
    elif kind == 'spark':
        path = QPainterPath()
        path.moveTo(9, 2.5)
        path.lineTo(10.8, 7.2)
        path.lineTo(15.5, 9)
        path.lineTo(10.8, 10.8)
        path.lineTo(9, 15.5)
        path.lineTo(7.2, 10.8)
        path.lineTo(2.5, 9)
        path.lineTo(7.2, 7.2)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == 'star':
        path = QPainterPath()
        _pts = ((9.0, 2.6), (10.65, 6.73), (15.09, 7.02), (11.66, 9.87),
                (12.76, 14.18), (9.0, 11.8), (5.24, 14.18), (6.34, 9.87),
                (2.91, 7.02), (7.35, 6.73))
        path.moveTo(*_pts[0])
        for _x, _y in _pts[1:]:
            path.lineTo(_x, _y)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == 'video':
        path = QPainterPath()
        path.moveTo(6.2, 3.8)
        path.lineTo(14.2, 9)
        path.lineTo(6.2, 14.2)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == 'expand':
        for a, b in (((3, 3), (7.2, 3)), ((3, 3), (3, 7.2)),
                     ((15, 15), (10.8, 15)), ((15, 15), (15, 10.8))):
            p.drawLine(QPointF(*a), QPointF(*b))
        for a, b in (((5.6, 5.6), (8.2, 8.2)), ((12.4, 12.4), (9.8, 9.8))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif kind == 'gear':
        p.drawEllipse(QPointF(c, c), 2.6, 2.6)
        for i in range(8):
            a = math.pi / 4.0 * i
            p.drawLine(QPointF(c + 4.6 * math.cos(a), c + 4.6 * math.sin(a)),
                       QPointF(c + 6.4 * math.cos(a), c + 6.4 * math.sin(a)))
    # R25-W1：状态页卡片语义线稿（同一套 1.6px 笔触/2.5px 留白）
    elif kind == 'memory':          # 显存：内存条 + 三格
        p.drawRoundedRect(QRectF(3.0, 5.5, 12.0, 7.0), 1.2, 1.2)
        for _x in (5.6, 9.0, 12.4):
            p.drawLine(QPointF(_x, 7.4), QPointF(_x, 10.6))
    elif kind == 'gpu':             # GPU：芯片 + 引脚 + 核心圆
        p.drawRoundedRect(QRectF(4.0, 4.0, 10.0, 10.0), 1.5, 1.5)
        p.drawEllipse(QPointF(c, c), 2.2, 2.2)
        for _k in (6.5, 9.0, 11.5):
            p.drawLine(QPointF(_k, 2.2), QPointF(_k, 4.0))
            p.drawLine(QPointF(_k, 14.0), QPointF(_k, 15.8))
            p.drawLine(QPointF(2.2, _k), QPointF(4.0, _k))
            p.drawLine(QPointF(14.0, _k), QPointF(15.8, _k))
    elif kind == 'cpu':             # CPU：芯片 + 内方框 + 四向引脚
        p.drawRoundedRect(QRectF(4.5, 4.5, 9.0, 9.0), 1.4, 1.4)
        p.drawRect(QRectF(7.2, 7.2, 3.6, 3.6))
        for _k in (6.8, 9.0, 11.2):
            p.drawLine(QPointF(_k, 2.4), QPointF(_k, 4.5))
            p.drawLine(QPointF(_k, 13.5), QPointF(_k, 15.6))
            p.drawLine(QPointF(2.4, _k), QPointF(4.5, _k))
            p.drawLine(QPointF(13.5, _k), QPointF(15.6, _k))
    elif kind == 'ram':             # 内存占用：三根柱
        p.drawLine(QPointF(3.0, 14.6), QPointF(15.0, 14.6))
        p.drawLine(QPointF(5.0, 14.0), QPointF(5.0, 9.6))
        p.drawLine(QPointF(9.0, 14.0), QPointF(9.0, 6.2))
        p.drawLine(QPointF(13.0, 14.0), QPointF(13.0, 4.2))
    elif kind == 'temp':            # 温度：温度计
        p.drawLine(QPointF(9.0, 3.2), QPointF(9.0, 10.4))
        p.drawLine(QPointF(7.4, 3.2), QPointF(7.4, 10.4))
        p.drawLine(QPointF(10.6, 3.2), QPointF(10.6, 10.4))
        p.drawEllipse(QPointF(9.0, 12.6), 2.4, 2.4)
    elif kind == 'power':           # 功耗：闪电
        path = QPainterPath()
        path.moveTo(10.2, 2.6)
        path.lineTo(6.2, 9.6)
        path.lineTo(8.8, 9.6)
        path.lineTo(7.8, 15.4)
        path.lineTo(11.8, 8.2)
        path.lineTo(9.2, 8.2)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == 'clock':           # 时钟：圆 + 指针
        p.drawEllipse(QPointF(c, c), 6.2, 6.2)
        p.drawLine(QPointF(c, c), QPointF(c, c - 3.4))
        p.drawLine(QPointF(c, c), QPointF(c + 2.6, c + 1.8))
    elif kind == 'chart':           # 曲线：折线
        path = QPainterPath()
        path.moveTo(2.8, 13.2)
        path.lineTo(6.2, 8.2)
        path.lineTo(9.2, 11.0)
        path.lineTo(12.4, 5.0)
        path.lineTo(15.2, 7.4)
        p.drawPath(path)
    # R28-V19..V23：配置中心/主操作按钮的补充线稿（同一 18 网格、1.6px 笔触）
    elif kind == 'folder':          # 文件夹：页签 + 箱体
        path = QPainterPath()
        path.moveTo(2.6, 5.4)
        path.lineTo(7.0, 5.4)
        path.lineTo(8.6, 7.0)
        path.lineTo(15.4, 7.0)
        path.lineTo(15.4, 13.6)
        path.lineTo(2.6, 13.6)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == 'trash':           # 清空：桶盖 + 桶身 + 两道竖线
        p.drawLine(QPointF(3.4, 5.6), QPointF(14.6, 5.6))
        path = QPainterPath()
        path.moveTo(6.8, 5.6)
        path.lineTo(6.8, 3.8)
        path.lineTo(11.2, 3.8)
        path.lineTo(11.2, 5.6)
        p.drawPath(path)
        p.drawRoundedRect(QRectF(4.6, 5.6, 8.8, 8.8), 1.2, 1.2)
        p.drawLine(QPointF(7.4, 8.0), QPointF(7.4, 12.2))
        p.drawLine(QPointF(10.6, 8.0), QPointF(10.6, 12.2))
    elif kind == 'refresh':         # 刷新：3/4 圆 + 箭头
        p.drawArc(QRectF(3.6, 3.6, 10.8, 10.8), 40 * 16, 280 * 16)
        path = QPainterPath()
        path.moveTo(13.4, 2.6)
        path.lineTo(13.4, 5.6)
        path.lineTo(10.4, 5.6)
        p.drawPath(path)
    elif kind == 'shield':          # 健康：盾牌 + 勾
        path = QPainterPath()
        path.moveTo(9.0, 2.6)
        path.lineTo(14.2, 4.6)
        path.lineTo(14.2, 9.2)
        path.cubicTo(14.2, 12.4, 11.8, 14.2, 9.0, 15.4)
        path.cubicTo(6.2, 14.2, 3.8, 12.4, 3.8, 9.2)
        path.lineTo(3.8, 4.6)
        path.closeSubpath()
        p.drawPath(path)
        path2 = QPainterPath()
        path2.moveTo(6.5, 9.1)
        path2.lineTo(8.2, 10.9)
        path2.lineTo(11.6, 7.5)
        p.drawPath(path2)
    # R29：推理路线按钮族的补充画法（同一 18 网格、1.6px 笔触）
    elif kind == 'list':            # 日志：三条横线 + 三个小方点
        for _y in (5.5, 9.0, 12.5):
            p.drawLine(QPointF(8.0, _y), QPointF(15.0, _y))
            p.drawLine(QPointF(3.4, _y), QPointF(5.0, _y))
    elif kind == 'stop':            # 停止：圆角方块
        p.drawRoundedRect(QRectF(5.4, 5.4, 7.2, 7.2), 1.4, 1.4)
    elif kind == 'scan':            # 测速：放大镜
        p.drawEllipse(QRectF(3.4, 3.4, 8.2, 8.2))
        p.drawLine(QPointF(10.6, 10.6), QPointF(14.8, 14.8))
    elif kind == 'eject':           # 卸载：上三角 + 底托线
        path = QPainterPath()
        path.moveTo(4.2, 10.6)
        path.lineTo(9.0, 5.4)
        path.lineTo(13.8, 10.6)
        p.drawPath(path)
        p.drawLine(QPointF(4.2, 13.4), QPointF(13.8, 13.4))
    p.end()
    return QIcon(pm)


# R38-9：状态点原来只有颜色一个维度。实测（_r38_state_row.py）在红色盲下
# 正常-警告 ΔE00=3.0、全灰下 正常-异常 ΔE00=2.4，等于分辨不出 → 同一套 10px
# 状态点补形状编码：正常=实心圆、警告=三角、异常=菱形、空闲=空心环。
_STATE_SHAPE = {
    UI_C['OK'].upper(): 'circle',
    UI_C['WARN'].upper(): 'tri',
    UI_C['ERR'].upper(): 'diamond',
    UI_C['IDLE'].upper(): 'ring',
}


def _state_dot(color, size=10):
    """R28/R38-9：状态点（颜色 + 形状双编码，色弱与灰度下仍可分）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    shape = _STATE_SHAPE.get(QColor(color).name().upper(), 'circle')
    if shape == 'ring':
        pen = QPen(QColor(color), max(1.2, size * 0.18))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = size * 0.5
        p.drawEllipse(QRectF(size / 2.0 - r + 1.0, size / 2.0 - r + 1.0,
                             size - 2.0, size - 2.0))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        m = size * 0.09
        if shape == 'tri':
            p.drawPolygon(QPolygonF([QPointF(size / 2.0, m),
                                     QPointF(size - m, size - m),
                                     QPointF(m, size - m)]))
        elif shape == 'diamond':
            p.drawPolygon(QPolygonF([QPointF(size / 2.0, m),
                                     QPointF(size - m, size / 2.0),
                                     QPointF(size / 2.0, size - m),
                                     QPointF(m, size / 2.0)]))
        else:
            p.drawEllipse(QRectF(1.0, 1.0, size - 2.0, size - 2.0))
    p.end()
    return pm


def _style_form_labels(form, size=12, color=TXT3):
    """R24-V4：QFormLayout 的 LabelRole 统一 12px + 次级色（只改既有 label，不新增控件）。"""
    try:
        for _i in range(form.rowCount()):
            _it = form.itemAt(_i, QFormLayout.LabelRole)
            _wgt = _it.widget() if _it is not None else None
            if isinstance(_wgt, QLabel):
                _wgt.setStyleSheet('color:%s; font-size:%dpx; background:transparent;'
                                   % (color, size))
    except Exception:
        _err()


def _brand_asset(*parts):
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', '') or os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(base, *parts)
    return os.path.join(APP_SRC_DIR, 'assets', *parts)


def brand_pixmap(size=26):
    """R23-A1：品牌标记。优先用包内官方 ico（领导提供 R024-A4，随安装包分发）；
    缺失时回退到 QPainter 画的「星弧 + 星点」原创标记（随项目分发，不引第三方图）。"""
    pm = QIcon(_brand_asset('icons', 'xinghong.ico')).pixmap(int(size), int(size))
    if not pm.isNull() and pm.width() > 1:
        return pm
    s = int(size)
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0.0, QColor(56, 226, 255))
    g.setColorAt(1.0, QColor(155, 107, 255))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawEllipse(QRectF(s * 0.08, s * 0.08, s * 0.84, s * 0.84))
    p.setBrush(QColor(10, 18, 38))
    p.drawEllipse(QRectF(s * 0.28, s * 0.12, s * 0.62, s * 0.62))
    p.setBrush(QColor(236, 246, 255))
    cx, cy, r1, r2 = s * 0.63, s * 0.63, s * 0.30, s * 0.12
    path = QPainterPath()
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r1 if i % 2 == 0 else r2
        pt = QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    return pm


class NavRail(QFrame):
    """左侧导航栏（参考荣耀电脑管家）：品牌头 + 导航项（图标+文字），
    选中项为高亮渐变圆角卡片。"""

    def __init__(self, title, subtitle, items, on_select, bottom_items=None):
        super().__init__()
        self.setObjectName('navrail')
        # R247：导航栏版式：宽 208、边距 12/16/12/12、间距 8。
        self._expanded_w = 208
        self._collapsed_w = 64
        self._collapsed = False
        self.setFixedWidth(self._expanded_w)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 16, 12, 12)
        v.setSpacing(8)
        brand = QHBoxLayout()
        brand.setSpacing(8)
        # R23-A1：10px 呼吸点 → 26px 品牌标记（缺图根子之一：品牌位只有一个小圆点）
        self._brand_logo = QLabel()
        self._brand_logo.setObjectName('navlogo')
        self._brand_logo.setFixedSize(26, 26)
        self._brand_logo.setPixmap(brand_pixmap(26))
        brand.addWidget(self._brand_logo)
        self._brand_lbl = QLabel(title)
        self._brand_lbl.setObjectName('navbrand')
        brand.addWidget(self._brand_lbl)
        brand.addStretch(1)
        self._brand_row = brand
        v.addLayout(brand)
        self._sub_lbl = QLabel(subtitle)
        self._sub_lbl.setObjectName('navsub')
        self._sub_lbl.setWordWrap(True)
        v.addWidget(self._sub_lbl)
        v.addSpacing(8)   # R21：间距 token 归一到 8（导航品牌块与按钮组之间，原 10）
        self.buttons = []
        self._glyphs = []
        # R25-W3：选中/未选中两套图标预生成（选中态深色，避免亮图标压在青紫渐变上）
        self._icons = []
        self._as_text = []
        self._sep = None

        def _add_item(glyph, name, idx, as_text=False):
            # R248：底部入口用 14 px 字体的字符当图标，不再塞 18×18 位图。
            b = QPushButton(('%s   %s' % (glyph, name)) if as_text else name)
            b.setObjectName('navitem')
            if as_text:
                _ic_off = _ic_on = None
            else:
                _ic_off = _nav_icon(glyph, 18, UI_C['INK_2'])
                _ic_on = _nav_icon(glyph, 18, UI_C['ACCENT_INK'])
                b.setIcon(_ic_off)
                b.setIconSize(QSize(18, 18))
            self._icons.append((_ic_off, _ic_on))
            self._as_text.append(as_text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _checked=False, i=idx: on_select(i))
            v.addWidget(b)
            self.buttons.append(b)
            self._glyphs.append((glyph, name))

        for i, (glyph, name) in enumerate(items):
            _add_item(glyph, name, i)
        v.addStretch(1)
        # R247：底部辅助入口独立分组：1 px 分隔线 rgba(255,255,255,0.10)，
        # 与一级导航隔开，页号接在主项之后。
        if bottom_items:
            self._sep = QFrame()
            self._sep.setFixedHeight(1)
            self._sep.setStyleSheet('background:rgba(255,255,255,0.10); border:none;')
            v.addWidget(self._sep)
            for j, (glyph, name) in enumerate(bottom_items):
                _add_item(glyph, name, len(items) + j, as_text=True)
        self.set_selected(0)

    def set_collapsed(self, collapsed):
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self.setFixedWidth(self._collapsed_w)
            self._brand_lbl.hide()
            self._sub_lbl.hide()
            self._brand_row.setAlignment(self._brand_logo, Qt.AlignHCenter)
            if getattr(self, '_xh_footer', None) is not None:
                self._xh_footer.hide()
            if getattr(self, '_sep', None) is not None:
                self._sep.hide()
            lay = self.layout()
            lay.setContentsMargins(8, 16, 8, 12)
            for i, b in enumerate(self.buttons):
                _gl = self._glyphs[i][0]
                b.setText(_gl if self._as_text[i] else '')
                b.setToolTip(self._glyphs[i][1])
        else:
            self.setFixedWidth(self._expanded_w)
            self._brand_lbl.show()
            self._sub_lbl.show()
            self._brand_row.setAlignment(self._brand_logo, Qt.AlignLeft | Qt.AlignVCenter)
            if getattr(self, '_xh_footer', None) is not None:
                self._xh_footer.show()
            if getattr(self, '_sep', None) is not None:
                self._sep.show()
            self.layout().setContentsMargins(12, 16, 12, 12)
            for i, b in enumerate(self.buttons):
                _gl, _nm = self._glyphs[i]
                b.setText(('%s   %s' % (_gl, _nm)) if self._as_text[i] else _nm)
                b.setToolTip('')

    def set_selected(self, idx):
        for i, b in enumerate(self.buttons):
            _on = (i == idx)
            b.setChecked(_on)
            if i < len(self._icons):
                _ic = self._icons[i][1 if _on else 0]
                if _ic is not None:          # 底部字符入口没有图标，只换选中底色
                    b.setIcon(_ic)



POWER_MAX_W = 1000.0        # R33：本机 GPU 物理上限（量程 115 W 的 8.7 倍）
CLK_SM_MAX = 5000.0         # R33：SM 时钟物理上限（MHz）
CLK_MEM_MAX = 30000.0       # R33：显存时钟物理上限（MHz）

# R33 条件自证：分支命中计数（unitprobe 读取前后差值）
_PW_STAT = {'n': 0, 'mw_fix': 0, 'unknown': 0, 'ok': 0}
_CLK_STAT = {'n': 0, 'unknown': 0, 'ok': 0}


def xh_norm_power(pw, pl):
    """R32/R33：功耗读数单位归一 + 物理区间校验；量程未知时不猜。

    实测 `%LOCALAPPDATA%/XingHongPanel/data/metrics.jsonl`：同一 `power` 字段
    在 8.7 与 52502.4 之间逐条跳变（104421 条里 3152 条 >140），而同一记录的
    `powerLimit` 恒为 115 W。即采集通路偶发以 µW 返回（量纲审计 BUG-A）。
    仅在量程已知（pl>20 W）且读数 > 量程×3 时按 µW 纠正（/1000）；
    量程缺失/可疑时不做任何倍率猜测，读数超出物理区间（>1000 W）直接返回
    None（显示端给「—/未知」），绝不显示一个错的数。
    """
    _PW_STAT['n'] += 1
    try:
        v = float(pw)
    except (TypeError, ValueError):
        _PW_STAT['unknown'] += 1
        return None
    try:
        lim = float(pl) if pl is not None else 0.0
    except (TypeError, ValueError):
        lim = 0.0
    fixed = False
    if lim > 20.0 and v > lim * 3.0:
        v = v / 1000.0
        fixed = True
    if not (0.0 <= v <= POWER_MAX_W):
        _PW_STAT['unknown'] += 1
        return None
    if fixed:
        _PW_STAT['mw_fix'] += 1
    _PW_STAT['ok'] += 1
    return v


def xh_fmt_clk(v, maxv=CLK_SM_MAX):
    """R33：时钟读数按物理区间校验；越界/缺失一律「未知」，不换算不猜。"""
    _CLK_STAT['n'] += 1
    try:
        x = float(v)
    except (TypeError, ValueError):
        _CLK_STAT['unknown'] += 1
        return '未知'
    if not (0.0 <= x <= maxv):
        _CLK_STAT['unknown'] += 1
        return '未知'
    _CLK_STAT['ok'] += 1
    return '%.0f' % x


# R34 条件自证：量纲归一分支命中计数（unitprobe 前后差值）
_DUR_STAT = {'n': 0, 'carry_h': 0, 'carry_d': 0, 'bad': 0}
_EN_STAT = {'n': 0, 'kwh': 0, 'bad': 0}


def xh_fmt_dur(sec):
    """R34：时长量纲归一。秒→分→时→天逐级进位，避免首屏出现「有载 5071 分」这种越界值。

    实测 `/api/energy?range=all`：dur=304260 s（5071 分 = 84.5 时）。改前直接
    `dur/60` 印成「5071分」——分钟字段越界 84 倍。缺失/脏值返回「—」，不猜 0。
    """
    _DUR_STAT['n'] += 1
    try:
        s = int(round(float(sec)))
    except (TypeError, ValueError):
        _DUR_STAT['bad'] += 1
        return '—'
    if s < 0:
        _DUR_STAT['bad'] += 1
        return '—'
    if s < 60:
        return '%d秒' % s
    if s < 3600:
        return '%d分%02d秒' % (s // 60, s % 60)
    if s < 86400:
        _DUR_STAT['carry_h'] += 1
        return '%d时%02d分' % (s // 3600, (s % 3600) // 60)
    _DUR_STAT['carry_d'] += 1
    return '%d天%02d时' % (s // 86400, (s % 86400) // 3600)


def xh_fmt_energy(wh):
    """R34：累计电量量纲归一。>=999.5 Wh 进位 kWh（保留 2 位），否则 Wh；缺失返回「—」。

    改前首屏固定 `%.2f Wh`：1319.19 Wh 以 6 位有效数字撑满「功耗统计」卡，
    再涨到 5 位整数就会顶到卡边；同时 5071 分的有载时长已越界。
    """
    _EN_STAT['n'] += 1
    try:
        v = float(wh)
    except (TypeError, ValueError):
        _EN_STAT['bad'] += 1
        return '—'
    if v < 0:
        _EN_STAT['bad'] += 1
        return '—'
    if v >= 999.5:
        _EN_STAT['kwh'] += 1
        return '%.2f\u00A0kWh' % (v / 1000.0)
    return '%.2f\u00A0Wh' % v


class Gauge(QWidget):
    def __init__(self, title, unit, c1, c2, maxv=100.0):
        super().__init__()
        self._title = title
        self._unit = unit
        self._c1 = QColor(c1)
        self._c2 = QColor(c2)
        self._maxv = float(maxv)
        self._value = None
        self.setMinimumSize(90, 64)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)      # R02：620→320 ms（每 2 s 一次采样，占空约 16%）
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._value = float(v)
        self.update()

    def set_value(self, v, maxv=None):
        if maxv:
            self._maxv = float(maxv)
        if v is None:
            self._value = None
            self.update()
            return
        v = float(v)
        if 'nogauge' in XH_PROBE:
            self._value = v
            self.update()
            return
        # R020/XH-M2：等值短路——空闲时不再每秒重启 620ms 动画持续重绘
        if (self._value is not None
                and (maxv is None or float(maxv) == self._maxv)
                and abs(v - self._value) < 1e-6):
            return
        # R03：小幅波动（<1.5% 量程，如空闲功率 28→29 W）直接落值不再吊 320 ms 插值，
        # 否则 6 个数字/仪表每 2 s 一次 60 fps 动画，占空接近 100%。
        if self._value is not None and abs(v - self._value) < max(1e-6, self._maxv * 0.015):
            self._anim.stop()
            self._value = v
            self.update()
            return
        start = self._value if self._value is not None else v
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(v)
        self._anim.start()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 18
        rect = QRectF((self.width() - side) / 2 + 5, 8, side - 10, side - 10)
        lw = 11.0
        track = QPen(QColor(140, 190, 255, 36), lw)
        track.setCapStyle(Qt.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 45 * 16, -270 * 16)
        if self._value is not None:
            ratio = max(0.0, min(1.0, self._value / self._maxv if self._maxv else 0.0))
            span = int(-270 * 16 * ratio)
            halo = QPen(QColor(self._c1.red(), self._c1.green(), self._c1.blue(), 60), lw + 9)
            halo.setCapStyle(Qt.RoundCap)
            p.setPen(halo)
            p.drawArc(rect, 45 * 16, span)
            # R15：锥形渐变按尺寸缓存（尺寸不变时每帧复用，像素完全一致）
            _gk = (self.width(), self.height(), self._c1.rgba(), self._c2.rgba())
            if getattr(self, '_gcache_key', None) != _gk:
                g = QConicalGradient(rect.center(), 135)
                g.setColorAt(0.0, self._c1)
                g.setColorAt(0.75, self._c2)
                g.setColorAt(1.0, self._c1)
                self._gcache = g
                self._gcache_key = _gk
            arc = QPen(self._gcache, lw)
            arc.setCapStyle(Qt.RoundCap)
            p.setPen(arc)
            p.drawArc(rect, 45 * 16, span)
        _ty = rect.center().y() - 6.0
        if self._value is None:
            # R27：空值只画破折号（与 R23-B3 空值族同色），不画单位
            p.setPen(QColor(UI_C['INK_3']))
            fe = QFont()
            fe.setPointSize(15)
            fe.setBold(True)
            p.setFont(fe)
            fme = QFontMetricsF(fe)
            p.drawText(QPointF(rect.center().x() - fme.horizontalAdvance('—') / 2.0,
                               _ty + (fme.ascent() - fme.descent()) / 2.0), '—')
        else:
            # R27：数字 17pt TXT / 单位 11px TXT3 分列，同基线，整体居中
            # R32：位数变多时按内圈可用宽度逐级降字号——原先只把整组文字夹在
            # rect 内，ring 描边 11px 仍会压住末位数字与单位（实测 26315 W
            # 的 W 落在圆环上）。_iw 先扣掉两侧环宽与 1px 安全带（内圈实宽 62，
            # 留 2px 余量后为 60；26.3 W 的 53.2、100 °C 的 55.7 仍按 17pt 渲染，
            # 只有 5 位数才降到 11pt）。
            txt = ('%.1f' % self._value).rstrip('0').rstrip('.')
            gap = 3.0
            _iw = max(24.0, rect.width() - 2 * lw - 2.0)
            fu = QFont()
            wn = wu = 0.0
            for _ps, _ups in ((17, 11), (15, 10), (13, 10), (11, 9), (9, 8)):
                f = QFont()
                f.setPointSize(_ps)
                f.setBold(True)
                fm = QFontMetricsF(f)
                fu.setPixelSize(_ups)
                wn = fm.horizontalAdvance(txt)
                wu = QFontMetricsF(fu).horizontalAdvance(self._unit)
                if wn + gap + wu <= _iw:
                    break
            x0 = rect.center().x() - (wn + gap + wu) / 2.0
            x0 = max(rect.left() + lw + 3.0,
                     min(x0, rect.left() + lw + 3.0
                         + max(0.0, _iw - wn - gap - wu)))
            base = _ty + (fm.ascent() - fm.descent()) / 2.0
            p.setPen(QColor(TXT))
            p.setFont(f)
            p.drawText(QPointF(x0, base), txt)
            p.setPen(QColor(TXT3))
            p.setFont(fu)
            p.drawText(QPointF(x0 + wn + gap, base), self._unit)
        p.setPen(QColor(SUB))
        f2 = QFont()
        # R38-23：原 setPointSize(9)。实测三档缩放（QT_SCALE_FACTOR 1.0/1.25/1.5）
        # 下 9pt 与 12px 的 ascent（12.00）与字宽（264.00）完全相同，且 12 已在本轮
        # 字号阶梯内；换成 px 是为了与 QSS 的 px 口径统一，视觉零变化。
        f2.setPixelSize(12)
        p.setFont(f2)
        p.drawText(QRectF(0, self.height() - 24, self.width(), 20),
                   Qt.AlignHCenter | Qt.AlignVCenter, self._title)


class ReflowGrid(QWidget):
    """响应式网格：按可用宽度自动决定列数（桌面端窄窗口适配）。

    给定一组 (widget, minW, stretch)，resize 时按宽度重排为若干等宽列；
    窄窗口自动从一行多列变为多行（如 4 卡 1 行 -> 2×2），不横向溢出。
    """

    def __init__(self, items, gap=12, parent=None):
        super().__init__(parent)
        self._items = items           # list of (widget, minW, stretch)
        self._gap = gap
        self._gl = QGridLayout(self)
        self._gl.setSpacing(gap)
        self._gl.setContentsMargins(0, 0, 0, 0)
        self._cols = -1
        for w, _minw, _st in items:
            w.setParent(self)
        self._reflow()

    def resizeEvent(self, ev):
        self._reflow()
        super().resizeEvent(ev)

    def _reflow(self):
        avail = self.width()
        # 决定列数：在能放下各列最小宽度的前提下，尽量让每个 widget 达到其 minW
        cols = 1
        for c in range(len(self._items), 0, -1):
            # 把 items 按顺序分到 c 列，每列宽度 = 该列所有 item 的最大 minW
            colw = [0] * c
            for i, (w, minw, st) in enumerate(self._items):
                colw[i % c] = max(colw[i % c], minw)
            if sum(colw) + self._gap * (c - 1) <= avail:
                cols = c
                break
        if cols == self._cols:
            return
        self._cols = cols
        # 清空旧布局
        while self._gl.count():
            self._gl.takeAt(0)
        for i, (w, minw, st) in enumerate(self._items):
            r, c = divmod(i, cols)
            self._gl.addWidget(w, r, c)
        # 列拉伸：按该列 item 的最大 stretch
        for c in range(cols):
            st = max(self._items[i][2] for i in range(c, len(self._items), cols))
            self._gl.setColumnStretch(c, st)


class KVModelCard(QFrame):
    """KVMem 单个模型卡：标签 + 8 个可编辑参数 + 启动/保存/停止。

    对齐网页端 modelCard（index.html:994-1019）：每个 profile 一张卡，
    8 个参数 ctx/n/ctk/ctv/budget/reserve/block/threads 全部可编辑。
    """

    def __init__(self, pid, tag, kv_opts):
        super().__init__()
        self.pid = pid
        self.tag = tag
        self.setObjectName('kvcard')
        # R07：卡内 8 个参数两列并排，卡宽被左右分栏压到 ~349px，全局 QSS 的
        # min-height:24 + padding 5px 让字段实际 36px 高（行高 35 → 上下行重叠 1px），
        # padding 8px 又让 100px 最小宽超出 82px 列宽（标签被输入框压住 4px）。
        # 这里只对卡内字段收紧内边距/最小高，外观（底色/描边/圆角）仍走全局 QSS。
        self.setStyleSheet(
            'QFrame#kvcard { background: rgba(10,24,46,0.55);'
            ' border: 1px solid rgba(122,190,255,0.18); border-radius: 14px; }'
            'QFrame#kvcard QSpinBox, QFrame#kvcard QComboBox {'
            ' min-height: 20px; padding: 1px 6px; }')
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)

        # 标题行：标签 + 名称 + 文件缺失 + 状态
        # X20：标题层级——名称独占一行，"架构上限"作次级小字
        top = QHBoxLayout()
        self.tag_lbl = QLabel(tag)
        self.tag_lbl.setFixedSize(26, 18)
        self.tag_lbl.setAlignment(Qt.AlignCenter)
        self.tag_lbl.setStyleSheet(
            'background:%s; color:#fff; border-radius:6px;'
            ' font-size:11px; font-weight:700;' % KV_TAG_COLOR.get(pid, '#888'))
        name_box = QVBoxLayout()
        name_box.setSpacing(0)
        self.name_lbl = QLabel('—')
        self.name_lbl.setStyleSheet(_t('font-weight:600; font-size:12px; color:@INK_0@;'))
        self.sub_lbl = QLabel(
            {'flash64': '架构上限 64K',
             'qwen9bq4': '架构上限 256K'}.get(pid, '架构上限 256K'))
        self.sub_lbl.setStyleSheet(_t('color:@INK_2@; font-size:11px;'))
        name_box.addWidget(self.name_lbl)
        name_box.addWidget(self.sub_lbl)
        self.miss_lbl = QLabel('')
        self.miss_lbl.setStyleSheet(_t('color:@WARN_2@; font-size:11px;'))
        self.status_lbl = QLabel('停止')
        self.status_lbl.setStyleSheet(_t('font-size:11px; color:@INK_2@;'))
        top.addWidget(self.tag_lbl)
        top.addLayout(name_box, 1)
        top.addWidget(self.miss_lbl)
        top.addWidget(self.status_lbl)
        v.addLayout(top)

        # 8 参数：2 列 × 4 行
        grid = QGridLayout()
        # R07：列距 14 → 8、标签 60 → 48、字段 100/96 → 84/84——原本两卡最小宽
        # 384px > 实际 349px，栅格被压到列宽 82，输入框溢出列宽盖住右列标签。
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.widgets = {}

        def add_num(key, label, row, col, maxv=1000000, minv=0):
            lab = QLabel(label)
            lab.setStyleSheet(_t('color:@INK_2@; font-size:11px;'))
            lab.setMinimumWidth(48)  # R07：48px 容得下 4 字标签「生成保留」，且不再挤压字段列
            lab.setAlignment(Qt.AlignVCenter)
            w = QSpinBox()
            w.setRange(minv, maxv)
            w.setKeyboardTracking(False)
            w.setMinimumWidth(84)
            w.setFixedHeight(26)  # R07：26px 保点击热区，同时给 4 行参数留出卡内呼吸位
            grid.addWidget(lab, row, col * 2)
            grid.addWidget(w, row, col * 2 + 1)
            grid.setRowMinimumHeight(row, 26)
            self.widgets[key] = w

        def add_combo(key, label, row, col, opts):
            lab = QLabel(label)
            lab.setStyleSheet(_t('color:@INK_2@; font-size:11px;'))
            lab.setMinimumWidth(48)
            lab.setAlignment(Qt.AlignVCenter)
            w = QComboBox()
            w.addItems(list(opts))
            w.setMinimumWidth(84)
            w.setFixedHeight(26)
            grid.addWidget(lab, row, col * 2)
            grid.addWidget(w, row, col * 2 + 1)
            grid.setRowMinimumHeight(row, 26)
            self.widgets[key] = w

        add_num('ctx', '上下文', 0, 0)
        add_num('n', '预留', 0, 1)
        add_combo('ctk', 'KV-K', 1, 0, kv_opts)
        add_combo('ctv', 'KV-V', 1, 1, kv_opts)
        add_num('budget', '工作集', 2, 0)
        add_num('reserve', '生成保留', 2, 1)
        add_num('block', '块', 3, 0, minv=1)
        add_num('threads', '线程', 3, 1, maxv=256)
        v.addLayout(grid)

        # 按钮
        self.start_b = QPushButton('启动此模型')
        self.save_b = QPushButton('保存参数')
        self.stop_b = QPushButton('停止')
        self.stop_b.setObjectName('danger')
        br = QHBoxLayout()
        # R07：三按钮等宽。stretch 只分配「剩余空间」，各按钮 sizeHint 106/105/106 不等
        # 时仍会排出 106/105/106；改为统一固定宽 105（卡内容宽 328，三钮+两隙=325，余 3px 靠右）。
        for _b in (self.start_b, self.save_b, self.stop_b):
            _b.setFixedWidth(105)
        br.addWidget(self.start_b)
        br.addWidget(self.save_b)
        br.addWidget(self.stop_b)
        br.addStretch(1)
        v.addLayout(br)

    def populate(self, p):
        self.name_lbl.setText(str(p.get('label', '')))
        self.miss_lbl.setText('' if p.get('fileExists') else '文件缺失')

        def setnum(key, val):
            try:
                self.widgets[key].setValue(int(val))
            except (TypeError, ValueError):
                _err()

        setnum('ctx', p.get('ctx'))
        setnum('n', p.get('n'))
        setnum('budget', p.get('budget'))
        setnum('reserve', p.get('reserve'))
        setnum('block', p.get('block'))
        setnum('threads', p.get('threads'))
        ix = self.widgets['ctk'].findText(str(p.get('ctk')))
        if ix >= 0:
            self.widgets['ctk'].setCurrentIndex(ix)
        ix = self.widgets['ctv'].findText(str(p.get('ctv')))
        if ix >= 0:
            self.widgets['ctv'].setCurrentIndex(ix)
        self.sync_state(p)

    def sync_state(self, p):
        running = bool(p.get('running'))
        ready = bool(p.get('ready'))
        # R20-3：状态没变就不重设 QSS/按钮文案（setStyleSheet 会触发 StyleChange）
        _key = (ready, running)
        if getattr(self, '_sync_key', None) != _key:
            self._sync_key = _key
            if ready:
                self.status_lbl.setText('就绪')
                self.status_lbl.setStyleSheet(_t('font-size:11px; color:@OK@;'))
                self.start_b.setText('运行中')
                self.start_b.setEnabled(False)
            elif running:
                self.status_lbl.setText('启动中')
                self.status_lbl.setStyleSheet(_t('font-size:11px; color:@WARN_2@;'))
                self.start_b.setText('启动中…')
                self.start_b.setEnabled(False)
            else:
                self.status_lbl.setText('停止')
                self.status_lbl.setStyleSheet(_t('font-size:11px; color:@INK_2@;'))
                self.start_b.setText('启动此模型')
                self.start_b.setEnabled(True)
        # X2：未运行→停止置灰（不做空操作）
        self.stop_b.setEnabled(bool(ready or running))

    def values(self):
        return {
            'id': self.pid,
            'ctx': self.widgets['ctx'].value(),
            'n': self.widgets['n'].value(),
            'ctk': self.widgets['ctk'].currentText(),
            'ctv': self.widgets['ctv'].currentText(),
            'budget': self.widgets['budget'].value(),
            'reserve': self.widgets['reserve'].value(),
            'block': self.widgets['block'].value(),
            'threads': self.widgets['threads'].value(),
        }


class LogDialog(QDialog):
    """单路日志实时查看（对齐网页 KV/Comfy 日志 modal）。

    endpoint 为 /api/log/kv 或 /api/log/comfy；可自动刷新、立即刷新。
    """

    # X21：后台取数经信号回主线程追加，日志刷新不再卡主线程
    result_ready = Signal(object)

    def __init__(self, parent, title, endpoint, api):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.endpoint = endpoint
        self.api = api
        self.resize(720, 480)
        v = QVBoxLayout(self)
        # R19：三浮层外边距统一 16/16/16/12（LogDialog 原先 bottom=16，与另两个 12 不一致）
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.chip_lbl = QLabel('—')
        self.chip_lbl.setObjectName('sub')
        self.auto_b = QPushButton('自动刷新：开')
        self.refresh_b = QPushButton('立即刷新')
        self.refresh_b.setIcon(_nav_icon('refresh', 14, UI_C['INK_1']))
        self.refresh_b.setIconSize(QSize(14, 14))
        self.auto_on = True
        self.auto_b.clicked.connect(self.toggle_auto)
        self.refresh_b.clicked.connect(self.refresh)
        row.addWidget(self.chip_lbl, 1)
        row.addWidget(self.auto_b)
        row.addWidget(self.refresh_b)
        v.addLayout(row)
        self.box = QPlainTextEdit()
        self.box.setReadOnly(True)
        self.box.setMaximumBlockCount(1000)
        self.box.setStyleSheet('font-family: Consolas, monospace; font-size:12px;')
        # R38-3 S3：日志区内容溢出时必须能看到滚动条（此前恒关 → 溢出部分无法感知）
        self.box.setVerticalScrollBarPolicy(SCROLL_V)
        self.box.setHorizontalScrollBarPolicy(SCROLL_H)
        self.box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        v.addWidget(self.box, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2500)
        self.result_ready.connect(self._on_fetch_done)
        self.refresh()

    def showEvent(self, ev):
        super().showEvent(ev)
        pop_in(self, duration=280)

    def toggle_auto(self):
        self.auto_on = not self.auto_on
        if self.auto_on:
            self.auto_b.setText('自动刷新：开')
            self.timer.start(2500)
        else:
            self.auto_b.setText('自动刷新：关')
            self.timer.stop()

    def refresh(self):
        # X21：请求飞行中禁用按钮；后台拉取经信号回主线程
        self.refresh_b.setEnabled(False)
        self.refresh_b.setText('刷新中…')

        def _fetch():
            try:
                _, d = self.api('GET', self.endpoint)
                return d
            except Exception:
                return None
        WORKER_POOL.submit(lambda: self.result_ready.emit(_fetch()))

    def _on_fetch_done(self, d):
        self.refresh_b.setEnabled(True)
        self.refresh_b.setText('立即刷新')
        if d is None or not isinstance(d, dict):
            return
        lines = d.get('lines', []) if isinstance(d, dict) else []
        txt = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '',
                     '\n'.join(str(x) for x in lines))
        if self.box.toPlainText() != txt:
            self.box.setPlainText(txt)
            self.box.verticalScrollBar().setValue(
                self.box.verticalScrollBar().maximum())


class PreviewDialog(QDialog):
    """应用内资源预览（对齐网页 prevModal）：大图/视频 + 左右切换 + 元信息 + 收藏。

    items 为 /api/outputs 的 output 项列表（dict，含 name/section/kind/size/mtime）。
    """

    def __init__(self, parent, items, index, api, get_bytes):
        super().__init__(parent)
        self.setWindowTitle('资源预览')
        self.resize(980, 720)
        self.items = list(items)
        self.index = index
        self.api = api
        self.get_bytes = get_bytes
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(12)

        # 媒体区：图片 / 视频 两页
        self.stack = QStackedWidget()
        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setMinimumSize(640, 460)
        self.stack.addWidget(self.img_lbl)
        self.video_widget = QVideoWidget()
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_widget)
        self.stack.addWidget(self.video_widget)
        v.addWidget(self.stack, 1)

        # 控制行
        ctrl = QHBoxLayout()
        self.prev_b = QPushButton('‹ 上一个')
        self.next_b = QPushButton('下一个 ›')
        self.play_b = QPushButton('播放/暂停')
        self.fav_b = QPushButton('加入杰作')
        self.meta_lbl = QLabel('')
        self.meta_lbl.setObjectName('sub')
        self.meta_lbl.setWordWrap(True)
        ctrl.addWidget(self.prev_b)
        ctrl.addWidget(self.next_b)
        ctrl.addWidget(self.play_b)
        ctrl.addWidget(self.fav_b)
        ctrl.addWidget(self.meta_lbl, 1)
        v.addLayout(ctrl)

        self.prev_b.clicked.connect(lambda: self.goto(self.index - 1))
        self.next_b.clicked.connect(lambda: self.goto(self.index + 1))
        self.play_b.clicked.connect(self.toggle_play)
        self.fav_b.clicked.connect(self.toggle_fav)
        self.goto(self.index)

    def showEvent(self, ev):
        super().showEvent(ev)
        pop_in(self, duration=300)

    def goto(self, i):
        if not self.items:
            return
        self.index = max(0, min(len(self.items) - 1, i))
        it = self.items[self.index]
        name = it.get('name') if isinstance(it, dict) else str(it)
        is_vid = str(name).lower().endswith(('.mp4', '.webm', '.mov'))
        # 元信息
        meta = os.path.basename(str(name))
        if isinstance(it, dict):
            if it.get('size'):
                meta += '  ·  %.1f KB' % (float(it['size']) / 1024)
            if it.get('mtime'):
                meta += '  ·  ' + xh_time.minute(float(it['mtime']))
        self.meta_lbl.setText(meta)
        # 收藏状态（R020/XH-S1：异步回填，方向键切换不再被同步 HTTP 冻结）
        self._refresh_fav_async(name)
        if is_vid:
            self.stack.setCurrentIndex(1)
            self.player.setSource(QUrl('http://%s:%d/output/%s' % (HOST, PANEL_PORT, name)))
            self.player.play()
        else:
            self.player.stop()
            self.stack.setCurrentIndex(0)
            threading.Thread(target=self._load_image, args=(name,), daemon=True).start()

    def _load_image(self, name):
        try:
            _, data = self.get_bytes('/output/' + name, timeout=20)
            pm = QPixmap()
            if pm.loadFromData(data):
                scaled = pm.scaled(
                    self.img_lbl.width() or 900, self.img_lbl.height() or 600,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                # R15-S03：经主线程桥回填（工作线程直调 setPixmap 是跨线程未定义行为）
                _bridge.run.emit(lambda: self.img_lbl.setPixmap(scaled))
        except Exception:
            _err()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _refresh_fav_async(self, name):
        """R020/XH-S1：收藏态后台读取，主线程只回填按钮文案。"""
        base = os.path.basename(str(name))

        def work():
            try:
                _, fav = self.api('GET', '/api/fav')
                items = fav.get('items', []) if isinstance(fav, dict) else []
                ok = base in [os.path.basename(str(x)) for x in items]
            except Exception:
                return

            def apply():
                cur = self.items[self.index] if self.items else None
                cur_name = cur.get('name') if isinstance(cur, dict) else str(cur)
                if os.path.basename(str(cur_name)) == base:
                    self.fav_b.setText('已在杰作' if ok else '加入杰作')
            _bridge.run.emit(apply)
        threading.Thread(target=work, daemon=True).start()

    def _is_fav(self, name):
        try:
            _, fav = self.api('GET', '/api/fav')
            items = fav.get('items', []) if isinstance(fav, dict) else []
            return os.path.basename(str(name)) in [os.path.basename(str(x)) for x in items]
        except Exception:
            return False

    def toggle_fav(self):
        it = self.items[self.index]
        name = it.get('name') if isinstance(it, dict) else str(it)
        base = os.path.basename(str(name))
        self.fav_b.setText('处理中…')

        def work():
            try:
                self.api('POST', '/api/fav/toggle', {'name': base})
            except Exception:
                _err()
            self._refresh_fav_async(name)
        threading.Thread(target=work, daemon=True).start()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.goto(self.index - 1)
        elif e.key() == Qt.Key_Right:
            self.goto(self.index + 1)
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.player.stop()
        super().closeEvent(e)


class KenBurnsImage(QWidget):
    """Ken Burns 缓推（对应网页 kb）：6.5s 内 scale 1→1.09 并轻微平移，循环。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = None
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._duration = 6500.0

    def start(self):
        if 'noanim' in R10:      # R10：装饰动画总开关
            return
        self._t = 0.0
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def set_pixmap(self, pm):
        self._pm = pm
        self._t = 0.0
        self.update()

    def _tick(self):
        self._t += 33.0
        if self._t >= self._duration:
            self._t = 0.0
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(UI_C['BG_ROOT']))
        if self._pm is None or self._pm.isNull():
            p.end()
            return
        k = self._t / self._duration
        # ease in-out
        e = k * k * (3 - 2 * k)
        scale = 1.0 + 0.09 * e
        tx = -0.012 * e
        ty = -0.010 * e
        iw, ih = self._pm.width(), self._pm.height()
        base = self._pm.scaled(self.width(), self.height(),
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
        bw, bh = base.width() * scale, base.height() * scale
        ox = (self.width() - bw) / 2 + tx * self.width()
        oy = (self.height() - bh) / 2 + ty * self.height()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(QRectF(ox, oy, bw, bh), self._pm,
                     QRectF(0, 0, iw, ih))
        p.end()


class ShowcaseDialog(QDialog):
    """杰作展示轮播（对齐网页 showModal）：图片 Ken Burns 自动轮播 / 视频逐个播放，
    上限 8，循环。数据来自 GET /api/fav。"""

    _data_got = Signal(object)
    _img_got = Signal(object)
    _remove_got = Signal(object)

    def __init__(self, parent, api, get_bytes):
        super().__init__(parent)
        self.setWindowTitle('杰作展示')
        self.resize(1040, 760)
        self.api = api
        self.get_bytes = get_bytes
        self.tab = 'image'
        self.all_items = []
        self.items = []
        self.idx = 0
        self._auto = False
        self._cur_pm = None

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(12)

        # 标签
        tab_row = QHBoxLayout()
        self.img_tab_b = QPushButton('图片')
        self.vid_tab_b = QPushButton('视频')
        self.count_lbl = QLabel('')
        self.count_lbl.setObjectName('sub')
        tab_row.addWidget(self.img_tab_b)
        tab_row.addWidget(self.vid_tab_b)
        tab_row.addStretch(1)
        tab_row.addWidget(self.count_lbl)
        v.addLayout(tab_row)

        # 舞台
        self.stage = QFrame()
        self.stage.setMinimumHeight(430)
        self.stage.setStyleSheet('QFrame { background:%s; border-radius:%dpx; }'
                                 % (STAGE_BG, R_CARD))
        # R19 复测补齐：舞台内 KenBurnsImage/QVideoWidget 各自填满整矩形，会把 QSS 圆角盖掉
        # （三档实测四角 offset=0，仍是直角）。用圆角遮罩裁剪子控件，尺寸变化时重算。
        self.stage.installEventFilter(self)
        sv = QVBoxLayout(self.stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        # 进度条（顶部）
        self.bar = FlowProgressBar()
        self.bar.setFixedHeight(3)
        self.bar.setStyleSheet('background: rgba(255,255,255,.08);')
        sv.addWidget(self.bar)
        self.stack = QStackedWidget()
        self.kb = KenBurnsImage()
        self.vid = QVideoWidget()
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.vid)
        self.stack.addWidget(self.kb)
        self.stack.addWidget(self.vid)
        sv.addWidget(self.stack, 1)
        v.addWidget(self.stage, 1)

        # 控制
        ctrl = QHBoxLayout()
        self.prev_b = QPushButton('上一张')
        self.play_b = QPushButton('暂停')
        self.next_b = QPushButton('下一张')
        self.remove_b = QPushButton('移除杰作')
        self.info_lbl = QLabel('')
        self.info_lbl.setObjectName('sub')
        ctrl.addWidget(self.prev_b)
        ctrl.addWidget(self.play_b)
        ctrl.addWidget(self.next_b)
        ctrl.addWidget(self.remove_b)
        ctrl.addStretch(1)
        ctrl.addWidget(self.info_lbl)
        v.addLayout(ctrl)

        self.img_tab_b.clicked.connect(lambda: self.switch_tab('image'))
        self.vid_tab_b.clicked.connect(lambda: self.switch_tab('video'))
        self.prev_b.clicked.connect(self.prev)
        self.next_b.clicked.connect(self.next)
        self.play_b.clicked.connect(self.toggle_play)
        self.remove_b.clicked.connect(self.remove_current)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.positionChanged.connect(self._video_pos)
        self.player.durationChanged.connect(self._video_dur)
        self._vid_dur = 0

        self._data_got.connect(self._apply_data)
        self._img_got.connect(self._show_image)
        self._remove_got.connect(self._after_remove)
        self.setModal(True)
        self.load_data()

    def eventFilter(self, obj, ev):
        if obj is self.stage and ev.type() == QEvent.Resize:
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.stage.rect()), R_CARD, R_CARD)
            self.stage.setMask(QRegion(path.toFillPolygon().toPolygon()))
        return super().eventFilter(obj, ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        pop_in(self, duration=300)

    def load_data(self):
        def work():
            try:
                code, f = self.api('GET', '/api/fav')
                if code == 200 and isinstance(f, dict):
                    self._data_got.emit(f)
            except Exception:
                _err()
        threading.Thread(target=work, daemon=True).start()

    def _apply_data(self, f):
        self.all_items = f.get('items', []) or []
        has_img = any(i.get('kind') == 'image' and i.get('exists')
                      for i in self.all_items)
        has_vid = any(i.get('kind') == 'video' and i.get('exists')
                      for i in self.all_items)
        self.switch_tab('image' if has_img else ('video' if has_vid else 'image'))

    def switch_tab(self, t):
        self.tab = t
        self.img_tab_b.setProperty('active', t == 'image')
        self.vid_tab_b.setProperty('active', t == 'video')
        self.img_tab_b.style().unpolish(self.img_tab_b)
        self.img_tab_b.style().polish(self.img_tab_b)
        self.vid_tab_b.style().unpolish(self.vid_tab_b)
        self.vid_tab_b.style().polish(self.vid_tab_b)
        self.items = [i for i in self.all_items
                      if i.get('exists') and i.get('kind') == t]
        self.idx = 0
        self.play_b.setText('下一个' if t == 'video' else '暂停')
        self.go()

    def go(self):
        if not self.items:
            self.stop_auto()
            self.kb.stop()
            self.player.stop()
            self.info_lbl.setText('暂无%s杰作 — 在资源池中点「加入杰作」'
                                  % ('图片' if self.tab == 'image' else '视频'))
            self.count_lbl.setText('')
            return
        if self.idx >= len(self.items):
            self.idx = 0
        it = self.items[self.idx]
        full = str(it.get('name'))
        name = os.path.basename(full)
        enc = urllib.parse.quote(full)
        self.count_lbl.setText('· %d/%d' % (self.idx + 1, len(self.items)))
        self.info_lbl.setText('%d/%d · %s' % (self.idx + 1, len(self.items), name))
        if self.tab == 'video':
            self.stack.setCurrentIndex(1)
            self.kb.stop()
            self.player.setSource(QUrl('http://%s:%d/output/%s'
                                      % (HOST, PANEL_PORT, enc)))
            self.player.play()
            self.bar.set_ratio(0)
        else:
            self.player.stop()
            self.stack.setCurrentIndex(0)
            self._load_image(enc)
        self.restart_auto()

    def _load_image(self, name):
        def work():
            try:
                _, data = self.get_bytes('/output/' + name, timeout=20)
                pm = QPixmap()
                if pm.loadFromData(data):
                    self._img_got.emit(pm)
            except Exception:
                _err()
        threading.Thread(target=work, daemon=True).start()

    def _show_image(self, pm):
        self._cur_pm = pm
        self.kb.set_pixmap(pm)
        self.kb.start()
        self._bar_anim = QVariantAnimation(self)
        self._bar_anim.setDuration(6000)
        self._bar_anim.setStartValue(0.0)
        self._bar_anim.setEndValue(1.0)
        self._bar_anim.valueChanged.connect(
            lambda v: self.bar.set_ratio(float(v)))
        self._bar_anim.start()

    def _media_status(self, st):
        if st == QMediaPlayer.EndOfMedia and self.tab == 'video':
            self.next()

    def _video_pos(self, pos):
        if self.tab == 'video' and self._vid_dur > 0:
            self.bar.set_ratio(pos / self._vid_dur)

    def _video_dur(self, dur):
        self._vid_dur = dur

    def restart_auto(self):
        self.stop_auto()
        if self.tab == 'image':
            self._auto = True
            self._auto_timer = QTimer(self)
            self._auto_timer.setSingleShot(True)
            self._auto_timer.timeout.connect(self.next)
            self._auto_timer.start(6000)

    def stop_auto(self):
        self._auto = False
        if getattr(self, '_auto_timer', None) is not None:
            self._auto_timer.stop()
        if getattr(self, '_bar_anim', None) is not None:
            self._bar_anim.stop()

    def toggle_play(self):
        if self.tab == 'video':
            self.next()
            return
        if self._auto:
            self.stop_auto()
            self.kb.stop()
            self.play_b.setText('播放')
        else:
            self.kb.start()
            self.play_b.setText('暂停')
            self.restart_auto()

    def prev(self):
        if not self.items:
            return
        self.idx = (self.idx - 1 + len(self.items)) % len(self.items)
        self.go()

    def next(self):
        if not self.items:
            return
        self.idx = (self.idx + 1) % len(self.items)
        self.go()

    def remove_current(self):
        if not self.items:
            return
        it = self.items[self.idx]
        name = str(it.get('name'))

        def work():
            self.api('POST', '/api/fav/toggle', {'name': name})
            try:
                code, f = self.api('GET', '/api/fav')
                if code == 200:
                    self._remove_got.emit(f)
            except Exception:
                _err()
        threading.Thread(target=work, daemon=True).start()

    def _after_remove(self, f):
        self.all_items = f.get('items', []) or []
        self.items = [i for i in self.all_items
                      if i.get('exists') and i.get('kind') == self.tab]
        if not self.items:
            self.go()
            return
        if self.idx >= len(self.items):
            self.idx = 0
        self.go()

    def closeEvent(self, e):
        self.stop_auto()
        self.kb.stop()
        self.player.stop()
        super().closeEvent(e)


def resource_path(rel):
    """定位随包资源：frozen 时基于 sys._MEIPASS（onedir 指向 _internal），
    源码运行时基于本文件所在目录。"""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS',
                       os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base = APP_SRC_DIR
    return os.path.join(base, str(rel))


def panel_dir():
    """后端 panel-server.py 所在目录（frozen 走随包 backend，源码态查源码目录）。"""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', '')
        cand = os.path.join(meipass, 'backend')
        if os.path.exists(os.path.join(cand, 'panel-server.py')):
            return cand
        base = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(base, 'panel-server.py')):
            return base
        return cand  # 即使缺失也返回随包路径，由启动自检报错
    for cand in (APP_SRC_DIR, os.path.join(APP_SRC_DIR, 'backend'), CANONICAL_DIR):
        if cand and os.path.exists(os.path.join(cand, 'panel-server.py')):
            return cand
    return CANONICAL_DIR


def port_up(port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect((HOST, port))
        s.close()
        return True
    except OSError:
        return False


# 启动自检结果（build_ui 用它决定是否在顶部挂横幅）
_PANEL_BOOT = {'ok': True, 'msg': '', 'short': '', 'pending': False}


def _probe_panel_identity(timeout=3.0):
    """确认 8899 上监听的是星虹自己的面板服务，而不是别的程序。

    只判断"端口有人监听"是不够的：别的程序占着 8899 时，原逻辑会误认为
    面板服务已就绪，跳过自启并把请求发给陌生服务，界面上全是 "—" 且没有任何提示。
    这里用 /api/health（免鉴权）做身份校验：必须是 200 + 含 score/checks 的 JSON。
    实现已收口到 xh_http.PanelClient.probe（文案与判据不变）。
    """
    return _HTTP.probe('/api/health', timeout=timeout)


def _boot_timing_write(ok, ms, timeout_ms):
    """R38-19：落一条启动自检耗时，供 03:00 窗口统计后端就绪 p50/p95 后再定超时常量。"""
    try:
        _d = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                          'XingHongPanel')
        os.makedirs(_d, exist_ok=True)
        with open(os.path.join(_d, 'boot_timing.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': xh_time.stamp(),
                                'ok': bool(ok), 'ready_ms': ms,
                                'timeout_ms': int(timeout_ms)},
                               ensure_ascii=False) + '\n')
    except Exception:
        pass


# X1：本地 token 鉴权——客户端从 runtime.json 读取并附加 X-Local-Token 头
def _local_token():
    try:
        tf = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'XingHongPanel', 'runtime.json')
        with open(tf, 'r', encoding='utf-8') as f:
            t = json.load(f).get('token')
        return str(t) if t else None
    except Exception:
        return None


# ── R38-26：HTTP 收口。四处手写 http.client / urllib 全部改走 xh_http，
# 超时/重试/错误归类/失败日志只在那里写一次；本文件只保留调用点。
def _http_fail_log(kind, method, path, ms, status, detail):
    """HTTP 失败唯一落点：并入既有的 _errs_live.log，前缀 [http] 便于过滤。

    不进 _ERRS 聚合表，因此不影响 `errs` 自检的白名单判据。
    """
    try:
        with open(_ERR_LOGPATH, 'a', encoding='utf-8') as _fh:
            _fh.write('[http] %s %s %s -> %s %sms %s\n'
                      % (kind, method, path, status, ms, detail))
    except Exception:
        pass


_HTTP = xh_http.PanelClient(HOST, PANEL_PORT, token_fn=lambda: _local_token())
xh_http.set_logger(_http_fail_log)


_CF_ERR_MAP = {
    'prompt must be non-empty string': '请先填写提示词',
    'width/height must be integers': '宽高必须是整数',
    'width/height must be 64-2048': '宽高需在 64–2048 之间',
    'steps must be integer': '步数必须是整数',
    'steps must be 1-200': '步数需在 1–200 之间',
    'cfg must be number': 'CFG 必须是数字',
    'cfg must be 0-32': 'CFG 需在 0–32 之间',
    'seed must be integer': '种子必须是整数',
    'seconds must be number': '时长必须是数字',
    'seconds must be 1-30': '时长需在 1–30 秒之间',
}


def cf_err_text(d, prefix=''):
    """把后端校验错误变成人话：优先 msg/code，其次翻译 error，最后才退回原始 dict。"""
    if not isinstance(d, dict):
        return prefix + str(d)
    code = str(d.get('code') or '')
    if code == 'no_model':
        txt = str(d.get('msg') or '生图模型未就绪')
    elif code == 'vram_busy':
        txt = str(d.get('msg') or '显存未释放，已中止')
    elif code == 'comfy_down':
        txt = 'ComfyUI 未就绪：请先点「启动 ComfyUI」，等状态变为在线再试'
    else:
        txt = str(d.get('msg') or '')
        if not txt:
            txt = _CF_ERR_MAP.get(str(d.get('error') or ''), str(d.get('error') or ''))
        if not txt:
            txt = str(d)
    return (prefix + txt) if prefix else txt


_BACKEND = {'m': None}
# R17-0a 取证开关：XH_LOCALGET=0 关掉同进程直取（走 HTTP），用于 A/B 对拍；默认开。
_GET_LOCAL = os.environ.get('XH_LOCALGET', '1') != '0'


def _local_get(path):
    """R17：panel-server 与面板同进程（start_server_thread 用 importlib 就地加载），
    GET 热路径直接读同一份缓存快照，跳过 socket。
    依据（round_217_xh 实测）：本机 TCP loopback 单次写固定 10.5 ms（26 B/2.6 KB 相同，
    UDP 0.078 ms → 栈过滤成本）、connect+close 26.9 ms；/api/state 单请求 40.8→29.0 ms
    只靠单写合并，剩余全在连接开销上，HTTP 层 floor 10.5 ms 无法满足 ≤8 ms。
    返回与 HTTP 完全相同的 JSON 语义（dumps→loads 往返，字段不增不减）；任何异常回落 HTTP。
    """
    m = _BACKEND.get('m')
    if m is None:
        return None
    try:
        p = path.split('?')[0]
        if p == '/api/state':
            obj = m.state()
        elif p == '/api/metrics':
            since = None
            if 'since=' in path:
                try:
                    since = float(path.split('since=')[1].split('&')[0])
                except Exception:
                    since = None
            obj = m.metrics(since)
        elif p == '/api/alerts':
            # R38：隐藏态告警通道（只读最近一条采样）
            obj = m.alerts()
        elif p == '/api/energy':
            rk = path.split('range=')[1].split('&')[0] if 'range=' in path else 'all'
            if rk not in ('all', 'today', 'week', 'month', 'session'):
                return 400, dict(ok=False, error='invalid range')
            try:
                win = int(path.split('win=')[1].split('&')[0] if 'win=' in path else 600)
            except Exception:
                return 400, dict(ok=False, error='invalid win')
            if not (1 <= win <= 86400):
                return 400, dict(ok=False, error='win out of range')
            obj = m.energy_stat(rk, win)
        elif p == '/api/settings/get':
            obj = dict(ok=True, settings=m.PANEL_SET)
        else:
            return None
        return 200, json.loads(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return None


def api(method, path, payload=None, timeout=8):
    if _GET_LOCAL and method == 'GET' and payload is None:
        _lg = _local_get(path)
        if _lg is not None:
            return _lg
    return _HTTP.json(method, path, payload, timeout)


def _child_pids(pid):
    """Toolhelp32 枚举直接子进程 → [(pid, exe_name)]。"""
    TH32CS_SNAPPROCESS = 0x00000002

    _wt = ctypes.wintypes

    class PE(ctypes.Structure):
        _fields_ = [('dwSize', _wt.DWORD), ('cntUsage', _wt.DWORD),
                    ('th32ProcessID', _wt.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', _wt.DWORD), ('cntThreads', _wt.DWORD),
                    ('th32ParentProcessID', _wt.DWORD), ('pcPriClassBase', _wt.LONG),
                    ('dwFlags', _wt.DWORD), ('szExeFile', _wt.WCHAR * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [_wt.DWORD, _wt.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not h or h == ctypes.c_void_p(-1).value:
        return []
    pe = PE()
    pe.dwSize = ctypes.sizeof(PE)
    out = []
    try:
        ok = k.Process32FirstW(h, ctypes.byref(pe))
        while ok:
            if pe.th32ParentProcessID == pid:
                out.append((int(pe.th32ProcessID), str(pe.szExeFile)))
            ok = k.Process32NextW(h, ctypes.byref(pe))
    finally:
        k.CloseHandle(h)
    return out


def _kill_children(pid, skip=('explorer.exe',)):
    """R38-9：退出超时兜底——把自己起的子进程（托管服务）连同孙进程一起杀掉，避免孤儿。

    只杀本进程的后代，跳过 explorer.exe（「打开目录」开的是它，杀了等于关用户的资源管理器）。

    R4：原实现每下钻一层做一次全系统快照（4 层 = 4 次，实测 0.09~0.13 s）；
    「强制退出」要求 1 s 内退干净，这里改成一次快照建父子表再按层杀。
    """
    TH32CS_SNAPPROCESS = 0x00000002
    _wt = ctypes.wintypes

    class PE(ctypes.Structure):
        _fields_ = [('dwSize', _wt.DWORD), ('cntUsage', _wt.DWORD),
                    ('th32ProcessID', _wt.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', _wt.DWORD), ('cntThreads', _wt.DWORD),
                    ('th32ParentProcessID', _wt.DWORD), ('pcPriClassBase', _wt.LONG),
                    ('dwFlags', _wt.DWORD), ('szExeFile', _wt.WCHAR * 260)]

    k = ctypes.windll.kernel32
    PROCESS_TERMINATE = 0x0001
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
    k.TerminateProcess.argtypes = [ctypes.c_void_p, _wt.UINT]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [_wt.DWORD, _wt.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not h or h == ctypes.c_void_p(-1).value:
        return []
    pe = PE()
    pe.dwSize = ctypes.sizeof(PE)
    kids = {}
    try:
        ok = k.Process32FirstW(h, ctypes.byref(pe))
        while ok:
            kids.setdefault(int(pe.th32ParentProcessID), []).append(
                (int(pe.th32ProcessID), str(pe.szExeFile)))
            ok = k.Process32NextW(h, ctypes.byref(pe))
    finally:
        k.CloseHandle(h)
    todo, killed = [pid], []
    seen = set()
    for _ in range(4):                      # 最多下钻 4 层，防环
        nxt = []
        for p in todo:
            for cpid, exe in kids.get(p, ()):
                if cpid in seen:
                    continue
                seen.add(cpid)
                if exe.lower() in skip:
                    continue            # 跳过的不杀，也不下钻（explorer 的子进程不是我们的）
                nxt.append(cpid)
                hp = k.OpenProcess(PROCESS_TERMINATE, False, cpid)
                if hp:
                    k.TerminateProcess(hp, 1)
                    k.CloseHandle(hp)
                    killed.append(cpid)
        if not nxt:
            break
        todo = nxt
    return killed


def get_bytes(path, timeout=10):
    return _HTTP.bytes(path, timeout)


def start_server_thread():
    path = os.path.join(panel_dir(), 'panel-server.py')
    spec = importlib.util.spec_from_file_location('xinghong_panel_server', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _BACKEND['m'] = mod          # R17：GET 热路径同进程直取同一份快照
    sys.argv = [sys.argv[0], '--no-open']
    # X23：内嵌服务线程 daemon=True（退出不再被非守护线程拖住）
    threading.Thread(target=mod.main, daemon=True, name='xh_http').start()


def overlay_play_badge(image, size=96):
    """首帧固定方格 cover（等比放大+居中裁剪），再叠加半透明圆底 + 白色播放三角。"""
    from PySide6.QtGui import QPainter, QPainterPath, QPen, QBrush
    pm = QPixmap.fromImage(image)
    pm = cover_pixmap(pm, size)  # X4：先固定方格，避免比例不一错位
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    # 轻微压暗，突出播放徽标
    p.fillRect(pm.rect(), QColor(0, 0, 0, 60))
    cx = cy = size / 2.0
    r = size * 0.26
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # 白色三角
    tri = QPainterPath()
    tw = size * 0.14
    th = size * 0.18
    ox = cx - tw / 2.0 + size * 0.02
    oy = cy - th / 2.0
    tri.moveTo(ox, oy)
    tri.lineTo(ox, oy + th)
    tri.lineTo(ox + tw, cy)
    tri.closeSubpath()
    p.setPen(QPen(QColor(255, 255, 255, 230), 1))
    p.setBrush(QBrush(QColor(255, 255, 255, 235)))
    p.drawPath(tri)
    p.end()
    return pm


def _find_ffmpeg():
    """定位 ffmpeg：优先随包，其次 PATH，最后 winget 安装路径。"""
    import shutil
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        for p in (os.path.join(base, 'ffmpeg.exe'),
                  os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe')):
            if os.path.exists(p):
                return p
    found = shutil.which('ffmpeg')
    if found:
        return found
    gl = os.path.join(os.environ.get('LOCALAPPDATA', ''),
                      'Microsoft', 'WinGet', 'Packages')
    if os.path.isdir(gl):
        for root, _dirs, files in os.walk(gl):
            if 'ffmpeg.exe' in files:
                return os.path.join(root, 'ffmpeg.exe')
    return None


FFMPEG_BIN = _find_ffmpeg()

# R11（XH-074）：视频首帧优先读本机文件。走 /output/ 的 HTTP 时，面板的静态文件
# 弱支持 Range，moov 在尾部的 mp4 会被 ffmpeg 判成 partial file（实测
# video/vosr2_video_test_00001_.mp4 必失败，本地路径 100% 成功）。
COMFY_OUT_DIR = _pathcfg.comfy_out()
# R12（XH-082）：超过该体积的图片不再整图解码（改为明确占位），避免 102MB 级 PNG
# 把缩略图线程拖死、界面长期停在「加载中」。
_THUMB_MAX_BYTES = 40 * 1024 * 1024


def _out_local_size(name):
    try:
        return os.path.getsize(os.path.join(COMFY_OUT_DIR,
                                            str(name).replace('/', os.sep)))
    except Exception:
        return -1


def _extract_first_frame(url, size):
    """在工作线程中用 ffmpeg 抓首帧，返回 cover 裁剪后的 QImage（失败返回 None）。"""
    import subprocess, tempfile
    if not FFMPEG_BIN:
        return None
    fd, tmp = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    if isinstance(size, (tuple, list)):
        sw, sh = int(size[0]), int(size[1])
    else:
        sw = sh = int(size)
    vf = ('scale=%d:%d:force_original_aspect_ratio=increase,'
          'crop=%d:%d' % (sw, sh, sw, sh))
    try:
        # R009：creationflags=CREATE_NO_WINDOW，否则每个 ffmpeg 子进程闪一个控制台
        # （启动时多个视频并发抓首帧 → 一大串 CMD 窗口）。
        _cf = 0x08000000 if os.name == 'nt' else 0
        r = subprocess.run(
            [FFMPEG_BIN, '-y', '-loglevel', 'error', '-ss', '0.1',
             '-i', url, '-frames:v', '1', '-vf', vf, tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_cf, timeout=25)
        if r.returncode != 0:
            return None
        img = QImage(tmp)
        if img.isNull():
            return None
        return img
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except Exception:
            _err()


class VideoThumbnailLoader(QObject):
    """提取视频首帧作为历史墙缩略图（对齐网页 <video> 自动显示首帧）。

    首帧提取在后台线程用 ffmpeg 完成（不阻塞主线程），有限并发；
    失败时回退到标准播放图标，绝不显示空图。
    """

    def __init__(self, parent=None, size=96, workers=4):
        super().__init__(parent)
        self._size = size
        self._inflight = set()
        # R022：首帧缓存改 LRU 上限 40 条（260² QImage ≈ 0.27MB/条 → ≈11MB），
        # 原先无上限，长时间浏览大量视频会持续增长。
        self._cache = OrderedDict()  # {key: img}——同名未变文件不再重复 ffmpeg
        self._cache_limit = 40
        self._holders = []  # 保留信号对象，避免发送者被 GC 导致信号丢失
        self._hold = set()  # X16：QVideoSink 兜底期间持有 player 防 GC
        # R11（XH-074）：同一个视频会同时出现在视频墙和超分墙。原先 _inflight
        # 直接 return，只有最先 enqueue 的那面墙拿到回调，另一面永久停在占位。
        self._waiters = {}  # key -> [callback, ...]

    def enqueue(self, key, url, callback):
        _c = self._cache.get(key)
        if _c is not None:
            self._cache.move_to_end(key)  # R022：LRU 热度刷新
            callback(key, _c)
            return
        if key in self._inflight:
            self._waiters.setdefault(key, []).append(callback)
            return
        self._inflight.add(key)
        holder = _TaskSignal()
        self._holders.append(holder)
        holder.result.connect(lambda img: self._finish(key, callback, holder, url, img),
                              Qt.QueuedConnection)

        def work():
            try:
                img = _extract_first_frame(url, self._size)
            except Exception:
                img = None
            holder.result.emit(img)

        WORKER_POOL.submit(work)

    def _finish(self, key, callback, holder, url, img):
        self._inflight.discard(key)
        try:
            if holder in self._holders:
                self._holders.remove(holder)
            holder.deleteLater()
        except Exception:
            _err()
        try:
            _cbs = [callback] + self._waiters.pop(key, [])
            if img is not None:
                self._cache[key] = img  # R15-X13：成功帧入缓存
                while len(self._cache) > self._cache_limit:
                    self._cache.popitem(last=False)  # R022：LRU 淘汰最久未用
                for _cb in _cbs:
                    _cb(key, img)
            else:
                # X16：ffmpeg 失败后的第二道兜底——主线程 QVideoSink 抓首帧
                self._fallback_poster(key, _cbs, url)
        except Exception:
            _err()

    def _fallback_poster(self, key, callbacks, url):
        """主线程 QMediaPlayer+QVideoSink 抓首帧（4s 超时）；
        再失败则回调 None，由调用方渲染「无预览」渐变封面。"""
        if not isinstance(callbacks, (list, tuple)):
            callbacks = [callbacks]

        def _emit(v):
            for _cb in callbacks:
                try:
                    _cb(key, v)
                except Exception:
                    _err()
        try:
            player = QMediaPlayer()
            sink = QVideoSink()
            player.setVideoOutput(sink)
            state = {'got': False, 'cleaned': False}
            self._hold.append(player)
            isz = self._size

            def _cleanup():
                if state['cleaned']:
                    return
                state['cleaned'] = True
                try:
                    sink.videoFrameChanged.disconnect(_grab)
                except Exception:
                    _err()
                try:
                    player.stop()
                    player.setSource(QUrl())
                    self._hold.discard(player)
                except Exception:
                    _err()
                QTimer.singleShot(0, lambda: player.deleteLater())

            def _grab():
                try:
                    f = sink.videoFrame()
                    if f.isNull() or state['got']:
                        return
                    state['got'] = True
                    img = f.toImage()
                    pm = QPixmap.fromImage(img)
                    pm = cover_pixmap(pm, isz)
                    _cleanup()
                    _emit(pm.toImage())
                except Exception:
                    _cleanup()
                    _emit(None)

            def _timeout():
                if state['got']:
                    return
                _cleanup()
                _emit(None)

            sink.videoFrameChanged.connect(_grab)
            QTimer.singleShot(4000, _timeout)
            # R11：url 现在优先是本机路径，QMediaPlayer 需要 file:// 形式
            player.setSource(QUrl(url) if str(url).startswith('http') else QUrl.fromLocalFile(url))
            player.play()
        except Exception:
            try:
                _emit(None)
            except Exception:
                _err()


class _TaskSignal(QObject):
    result = Signal(object)


# 模块级：main() 退出时要读它写 _graceful_exit.json；放在 build_ui 里会取不到（NameError）
# 对象收在 infra/config.py，这里沿用原名 _XH_EXIT_HOW。


# ── 本地落盘唯一出口：infra/store.py（C1-B3 收口）─────────────────────────────
# 会话 JSONL / Agent 步骤 / 同意日志 / 密钥擦除四类读写全部搬出，逐字未改；
# launcher 只保留「合法工具名从 app 层注入」与旧名字别名，调用点不受影响。
XX_SESSION_DIR = _store.XX_SESSION_DIR
XX_AGENT_DIR = _store.XX_AGENT_DIR
_xx_scrub = _store.xx_scrub
xx_secret_register = _store.xx_secret_register
xx_session_write = _store.xx_session_write
xx_agent_save = _store.xx_agent_save


def xx_agent_load():
    return _store.xx_agent_load(XX_AGENT_NAMES)


# ===== C1-B3-1：小星两个后台线程搬到 app/xx_workers.py（原 L6417–6602，185 行）=====
# 工厂式绑定模块级依赖：api / _xx_scrub / XX_SESSION_DIR 显式传参；
# 类名与构造签名不变（pages_xiaoxing 的调用点一行未改）。
_XXW = build_xx_workers(api=api, _xx_scrub=_xx_scrub,
                        XX_SESSION_DIR=XX_SESSION_DIR)
XxChatWorker = _XXW['XxChatWorker']
XxAgentWorker = _XXW['XxAgentWorker']


# ── P3：小星的 Agent 步骤定义（落盘走 infra/store.py）──────────────────────
# 步骤定义落 %LOCALAPPDATA%\XingHongPanel\ai_agents\agent_steps.json；执行在工作线程。
# XX_AGENT_TOOLS / XX_AGENT_NAMES 随两个 worker 一起去了 app/xx_workers.py（顶部 import）。
# XX_AGENT_DIR 由 infra/store.py 定义，上方已别名回原名。


def _comfy_power(path, label, btn, qwait, w):
    """ComfyUI 启停：飞行期置灰 + 失败弹窗 + 180s 超时（stop 要杀进程树）。

    C1-B2：原定义在 build_ui() 生图页段（1.4.3 修 do() 未定义时引入），
    上移到模块级由页面共用；逻辑与搬迁前逐字一致，只把 qwait / w 显式化成参数。
    """
    btn.setEnabled(False)

    def _done(r):
        btn.setEnabled(True)
        code = r[0] if isinstance(r, tuple) else 0
        body = r[1] if isinstance(r, tuple) and isinstance(r[1], dict) else {}
        if code != 200:
            QMessageBox.warning(w, 'ComfyUI', '%s失败：%s'
                                % (label, body.get('msg') or ('HTTP %s' % code)))

    qwait(lambda: _safe_post(path, timeout=180), _done)


def _safe_post(path, body=None, timeout=8):
    """POST 兜底：异常统一折成 (0, {"msg": ...})，供 qwait 后台线程调用。

    C1-B2：原定义在 build_ui() 内（状态页段），因生图页 comfy 启停（_comfy_power）也用它，
    上移到模块级共用；逻辑与搬迁前逐字一致。
    """
    try:
        return api('POST', path, body or {}, timeout=timeout)
    except Exception as e:
        return (0, {'msg': str(e)})


def build_ui():
    import pyqtgraph as pg
    _perf_mark('ui_b')

    class TimeAxisItem(pg.AxisItem):
        """epoch 秒 -> HH:MM:SS（窄间隔）/ HH:MM（宽间隔）。"""

        def tickStrings(self, values, scale, spacing):
            import time as _tm
            out = []
            for v in values:
                try:
                    lt = _tm.localtime(float(v) * scale)
                    fmt = '%H:%M' if spacing >= 60 else '%H:%M:%S'
                    out.append(_tm.strftime(fmt, lt))
                except Exception:
                    out.append('')
            return out

    def smooth_xy(xs, ys, sub=4):
        """Catmull-Rom 插值（等价网页 echarts smooth:true），numpy 向量化实现。"""
        import numpy as np
        x = np.asarray(xs, dtype=float)
        y = np.asarray(ys, dtype=float)
        n = len(x)
        if n < 3:
            return x, y
        xp = np.concatenate([[x[0]], x, [x[-1]]])
        yp = np.concatenate([[y[0]], y, [y[-1]]])
        # 段 i（i=0..n-2，共 n-1 段）使用 p0..p3 四个相邻点
        p0x, p1x, p2x, p3x = xp[0:n - 1], xp[1:n], xp[2:n + 1], xp[3:n + 2]
        p0y, p1y, p2y, p3y = yp[0:n - 1], yp[1:n], yp[2:n + 1], yp[3:n + 2]
        t = np.linspace(0.0, 1.0, sub, endpoint=False)[None, :]
        t2, t3 = t * t, t * t * t
        X = 0.5 * ((2 * p1x[:, None]) + (-p0x[:, None] + p2x[:, None]) * t
                   + (2 * p0x[:, None] - 5 * p1x[:, None] + 4 * p2x[:, None] - p3x[:, None]) * t2
                   + (-p0x[:, None] + 3 * p1x[:, None] - 3 * p2x[:, None] + p3x[:, None]) * t3)
        Y = 0.5 * ((2 * p1y[:, None]) + (-p0y[:, None] + p2y[:, None]) * t
                   + (2 * p0y[:, None] - 5 * p1y[:, None] + 4 * p2y[:, None] - p3y[:, None]) * t2
                   + (-p0y[:, None] + 3 * p1y[:, None] - 3 * p2y[:, None] + p3y[:, None]) * t3)
        # 防过冲：每段插值结果钳制到相邻四点的 Y 范围，避免数据突变时
        # Catmull-Rom 越界形成自环（CPU 等尖峰场景）。
        seg_lo = np.minimum.reduce([p0y, p1y, p2y, p3y])[:, None]
        seg_hi = np.maximum.reduce([p0y, p1y, p2y, p3y])[:, None]
        Y = np.clip(Y, seg_lo, seg_hi)
        # X 同样钳制到本段端点之间，保证单调不回退，消除数据间隔不均时的
        # 横向自环（曲线折返形成的小三角）。
        X = np.clip(X, p1x[:, None], p2x[:, None])
        X = np.concatenate([X.ravel(), [x[-1]]])
        Y = np.concatenate([Y.ravel(), [y[-1]]])
        return X, Y

    def _hex_to_qcol(hexcol, alpha=1.0):
        h = hexcol.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return QColor(r, g, b, int(alpha * 255))

    def make_plot():
        """透明背景 PlotWidget（融入玻璃卡），X 为时间轴；视口禁止白底填充。"""
        pl = pg.PlotWidget(axisItems={'bottom': TimeAxisItem(orientation='bottom')})
        pl.setBackground(None)
        pl.setAutoFillBackground(False)
        vp = pl.viewport()
        vp.setAutoFillBackground(False)
        vp.setBackgroundRole(vp.palette().ColorRole.NoRole)
        pal = vp.palette()
        pal.setColor(pal.ColorRole.Window, QColor(0, 0, 0, 0))
        pal.setColor(pal.ColorRole.Base, QColor(0, 0, 0, 0))
        vp.setPalette(pal)
        return pl

    app = QApplication.instance()
    app.setStyle("Fusion")  # 保证 QHeaderView 等 QSS 正确生效（windows 样式下表头会变白）
    app.setStyleSheet(DARK_QSS)
    # R03：曲线层默认抗锯齿；`noaa` 仅用于性能归因（对比重绘成本）
    pg.setConfigOptions(antialias=('noaa' not in XH_PROBE))

    def fnum(v, nd=1):
        try:
            return ('%.' + str(nd) + 'f') % float(v)
        except Exception:
            return '—'

    _task_holders = []

    _inflight = set()  # R017-XH01：按 key 去重——轮询重入时丢弃旧任务，不积压线程池

    def qwait(fn, done, err=None, key=None, pool=None):
        # 用主线程 QObject 的信号做跨线程派发（QTimer.singleShot 在工作线程
        # 无事件循环时不会触发，已实测确认）。
        if key is not None:
            if key in _inflight:
                return
            _inflight.add(key)
        holder = _TaskSignal()

        def _deliver(r):
            if key is not None:
                try:
                    _inflight.discard(key)
                except Exception:
                    _err()
            _c0 = (time.perf_counter(), time.thread_time())
            done(r)
            _cost('ui:%s' % key, _c0)
            # 回调完成后释放 holder，避免高频轮询下 holder 无限累积
            QTimer.singleShot(0, lambda: _release_holder(holder))

        holder.result.connect(_deliver, Qt.QueuedConnection)
        _task_holders.append(holder)

        def run():
            _c0 = (time.perf_counter(), time.thread_time())
            try:
                r = fn()
            except Exception as e:
                r = ('ERR', str(e))
            _cost('net:%s' % key, _c0)
            holder.result.emit(r)
        (pool or WORKER_POOL).submit(run)

    def _release_holder(holder):
        try:
            if holder in _task_holders:
                _task_holders.remove(holder)
            holder.deleteLater()
        except Exception:
            _err()

    w = QMainWindow()
    # R023：520×360 下卡片压盖、按钮文字纵切（R022 测试员 P1）；最小尺寸提升
    w.setMinimumSize(980, 640)
    back = Backdrop()
    w.setCentralWidget(back)
    # R14：Backdrop.__init__ 里已设 WA_OpaquePaintEvent，但 setCentralWidget 会重挂父级，
    # 实测装好的 exe 里该属性丢失（paintwho 快照 opaque=0）→ 任一小控件 update 都要
    # 连带父链补底。这里在重挂后与事件循环首帧各再确认一次；像素输出不变，只影响
    # Qt 是否要在 Backdrop 底下补画一层。
    back.setAttribute(Qt.WA_OpaquePaintEvent, not XH_NOOPAQUE)
    # R20-实验A（仅测量）：强制顶层半透明，用来验证「顶层透明 → 每次重绘都要从桌面
    # 重新合成」这个假设。默认不开；只切这一个属性，视觉会出现方角，仅测试用。
    if os.environ.get('XH_TOPALPHA', '') == '1':
        w.setAttribute(Qt.WA_TranslucentBackground, True)
    if not XH_NOFIX and not XH_NOOPAQUE:
        QTimer.singleShot(0, lambda: back.setAttribute(Qt.WA_OpaquePaintEvent, True))
    # R16：整页透明叠加层（装饰动效统一绘制）。必须在页面控件创建之前建好，
    # 之后的 FlowProgressBar / BreathDot 才会注册到它；层叠顺序由 _ensure_top 维持。
    # R19：带内不透明模式（XH_BAND=1，默认）不建整窗叠加层——透明整窗层会把
    # 每一个下层小控件的重绘都放大成「整窗透明重绘」（实测 DecoOverlay 9.19/s、单次 686000px）。
    _band_mode = os.environ.get('XH_BAND', '1') == '1'
    if not XH_NOOVERLAY and not _band_mode:
        try:
            _DECO['ov'] = DecoOverlay(back)
        except Exception:
            _DECO['ov'] = None
    _band_watch_install()        # R19：装饰带失效监看（压在带上的控件一重绘就重建该带）
    w.setWindowTitle(PANEL_TITLE)
    # R024-A4：应用图标=星虹 ico（打包于 _internal/icons，源码态走 assets/icons）
    _xh_icon = QIcon(_brand_asset('icons', 'xinghong.ico'))
    if not _xh_icon.isNull():
        w.setWindowIcon(_xh_icon)
    # 窗口尺寸严格对齐默认基准（1000×731，最小 520×360）；锁定初始大小
    WIN_W, WIN_H = 1000, 731
    w.resize(WIN_W, WIN_H)
    # 仅恢复窗口位置，不恢复旧的尺寸（避免历史大窗口导致不一致）
    _geo = QSettings('X.H', 'XingHongPanel')
    _restored_geo = False
    if _geo.value('geometry'):
        try:
            _old = w.screen().availableGeometry()
            w.restoreGeometry(_geo.value('geometry'))
            # 强制尺寸回到基准，只保留位置
            w.resize(WIN_W, WIN_H)
            if w.x() < 0 or w.y() < 0 or w.x() > _old.width():
                _restored_geo = False
            else:
                _restored_geo = True
        except Exception:
            _restored_geo = False
    w._default_maximized = False
    if not _restored_geo:
        _screen = app.primaryScreen().availableGeometry()
        w.move(int(_screen.center().x() - WIN_W / 2),
               int(_screen.center().y() - WIN_H / 2))
    outer = QVBoxLayout(back)
    outer.setContentsMargins(12, 12, 12, 12)
    _perf_mark('win_e')
    # 启动自检横幅：端口被占 / 后端没起来时，把原因摆在最上面，
    # 不让用户对着满屏 "—" 猜（第六轮真机走查：端口冲突时原版零提示）
    boot_banner = QLabel('')
    boot_banner.setWordWrap(True)
    boot_banner.setStyleSheet(
        _t('QLabel{background:rgba(196,120,80,34); border:1px solid rgba(224,148,96,150);'
        ' border-radius:8px; padding:8px 12px; color:@WARN@; font-size:12px;}'))
    boot_banner.hide()
    if not _PANEL_BOOT.get('ok', True):
        boot_banner.setText(str(_PANEL_BOOT.get('msg') or '启动自检未通过：面板服务不可用'))
        boot_banner.show()
    outer.addWidget(boot_banner)
    # 品牌已在左侧导航栏，不再重复顶部标题
    body = QHBoxLayout()
    body.setSpacing(12)
    stack = QStackedWidget()

    # R019-XH-B1：scroll_wrap 已无调用（R017/R018 改为压缩适配+无滚动实现），原实现见 git 历史

    # ============ C1-B2-8a：宫格墙 + R10 删除/清空 ============
    # 原 L6853–7162（310 行）整段搬到 ui/gallery.py；此处只做显式传参，
    # 解包延用原名字，下游三页传参与 w._r10_del 一行不改。
    _gal = build_gallery_helpers(
        w=w, api=api, qwait=qwait, ACTION_POOL=ACTION_POOL, _err=_err,
        FIT_STAT=FIT_STAT, SCROLL_H=SCROLL_H, SCROLL_OFF=SCROLL_OFF,
        _attach_empty_state=_attach_empty_state,
        overlay_play_badge=overlay_play_badge, fit_pixmap=fit_pixmap,
        cover_pixmap=cover_pixmap, _cover_canvas=_cover_canvas,
        _nav_icon=_nav_icon, UI_C=UI_C)
    make_grid_gallery = _gal['make_grid_gallery']
    _wall_names = _gal['wall_names']
    _do_delete = _gal['do_delete']
    _ask_delete = _gal['ask_delete']
    _attach_delete_menu = _gal['attach_delete_menu']
    _mk_clear_btn = _gal['mk_clear_btn']
    _ph_icon = _gal['ph_icon']
    _del_state = _gal['state']

    # 页面切换：首次进入时错落淡入该页所有卡片（对应网页 fadeUp），
    # 结束后挂回阴影；重复进入不再对整页做透明度特效（避免 QScrollArea
    # 上的 FBO 切页泄漏）。
    _entered = set()

    def _pt_is_heavy(page, idx):
        """含 pyqtgraph 绘图区的页面：切页/入场都不做 opacity 合成。

        QGraphicsOpacityEffect 会强制整页离屏合成，绘图区每帧要重画，是切页卡顿主因。
        """
        heavy = getattr(page, '_pt_heavy', None)
        if heavy is None:
            try:
                heavy = bool(page.findChildren(pg.PlotWidget)
                             or page.findChildren(pg.GraphicsLayoutWidget))
            except Exception:
                heavy = idx in (0, 2)
            page._pt_heavy = heavy
        return heavy

    def enter_page(idx):
        if idx in _entered:
            return
        page = stack.widget(idx)
        if page is None:
            return
        _entered.add(idx)
        if idx == 0:
            return  # 状态页已在构建时播放入场
        if _pt_is_heavy(page, idx):
            return  # 重页面跳过逐卡合成，避免与绘图区叠加 FBO
        card_list = sorted(set(c for c in page.findChildren(QGroupBox)
                               if isinstance(c, GlassCard)),
                           key=lambda c: (c.pos().y(), c.pos().x()))
        for i, card in enumerate(card_list):
            fade_in(card, duration=120, delay=20 * i, restore_shadow=True)

    def page_transition(idx):
        """切页过渡（R38-19：重页面去掉 opacity 合成，轻页面 260→120 ms）。"""
        _perf_mark('pt_b')
        page = stack.widget(idx)
        if page is None:
            return
        old = getattr(page, '_pt_anim', None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                _err()
        # QGraphicsOpacityEffect 会让整页走离屏合成；含 pyqtgraph 的页面每帧要重画绘图区，
        # 实测是切页卡顿主因。这类页直接硬切，轻页面才保留淡入。
        heavy = _pt_is_heavy(page, idx)
        if heavy:
            if page.graphicsEffect() is not None:
                page.setGraphicsEffect(None)
            _perf_mark('pt_e')
            return
        eff = QGraphicsOpacityEffect(page)
        eff.setOpacity(0.0)
        page.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b'opacity', page)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        # R022：PRD 规定视图过渡 260ms，此前实测 220ms 与文档不符
        # R023：动画强度由进阶配置中心控制（关闭=0ms 直接显示，降级=120ms）
        # R38-19：默认降到 120 ms，减少切页可见延迟
        _dur = int(XH_ADV.get('anim', 120))
        anim.setDuration(_dur)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        if _dur <= 0:
            eff.setOpacity(1.0)
            page.setGraphicsEffect(None)
            return

        def cleanup():
            if page.graphicsEffect() is eff:
                page.setGraphicsEffect(None)
        anim.finished.connect(cleanup)
        anim.start()
        page._pt_anim = anim
        _perf_mark('pt_e')

    _timer_hook = {'fn': None}

    # R23-B1：页级标题。QLabel#h1（19px/600）此前全文件 0 处使用，13 页都没有页头，
    # 页内直接进卡片 → 层级只剩卡片标题。这里起一条常驻内容页头，随导航/配置 Tab 更新。
    _PAGE_HEAD = [
        ('状态总览', '实时性能 · 每 1 秒采样'),
        ('推理路线', 'KVMem 模型加载 / 卸载'),
        ('ComfyUI 生图', '参数与历史作品'),
        ('视频生成', 'H3 路线 · 首帧驱动'),
        ('超分放大', '图片 / 视频 · 倍率可选'),
        ('配置中心', '数据设置'),
        ('小星', '本地模型助手 · 对话与配置'),
    ]
    _head_row = QHBoxLayout()
    _head_row.setContentsMargins(6, 2, 6, 2)
    _head_row.setSpacing(12)
    page_h1 = QLabel(_PAGE_HEAD[0][0])
    page_h1.setObjectName('h1')
    page_cap = QLabel(_PAGE_HEAD[0][1])
    page_cap.setObjectName('sub')
    _head_row.addWidget(page_h1)
    _head_row.addWidget(page_cap, 0, Qt.AlignBottom)
    _head_row.addStretch(1)
    _right_col = QVBoxLayout()
    _right_col.setContentsMargins(0, 0, 0, 0)
    _right_col.setSpacing(4)
    _right_col.addLayout(_head_row)
    _right_col.addWidget(stack, 1)
    _cfg_tab_now = ['数据设置']

    def _sync_head(idx):
        try:
            _t = _PAGE_HEAD[idx]
            page_h1.setText(_t[0])
            page_cap.setText(_cfg_tab_now[0] if idx == 5 else _t[1])
        except Exception:
            _err()

    def on_select(idx):
        _ftrace_nav(idx, 'begin')      # R36-N2：切页标记（仅 ftrace 期间写入，逐步打点）
        stack.setCurrentIndex(idx)
        _ftrace_nav(idx, 'stack')
        rail.set_selected(idx)
        _sync_head(idx)
        _ftrace_nav(idx, 'chrome')
        enter_page(idx)
        _ftrace_nav(idx, 'enter')
        if idx in _entered:
            page_transition(idx)
        # X3：切页立即按页启停轮询定时器（隐藏页不再持续轮询）
        _f = _timer_hook.get('fn')
        if _f is not None:
            try:
                _f(idx)
            except Exception:
                _err()
        _ftrace_nav(idx, 'end')

    NAV_ITEMS = [
        ('status', '状态'),
        ('routes', '推理路线'),
        ('spark', 'ComfyUI 生图'),
        ('video', '视频生成'),
        ('expand', '超分放大'),
        ('gear', '配置中心'),
    ]
    # R247：「小星」不再是第 7 个一级项，改为导航底部独立分组（含 1 px 分隔线），
    # 底部辅助入口与一级导航分离，页号仍为 6。
    # R248：图标为 14 px 重量的字符 ✦，不用 18×18 位图。
    NAV_BOTTOM = [('✦', '小星')]
    _perf_mark('nav_b')
    rail = NavRail(PANEL_TITLE, '硬件代号 v4.0\n原生客户端 v%s' % ux.APP_VERSION, NAV_ITEMS,
                   on_select, bottom_items=NAV_BOTTOM)
    _perf_mark('nav_e')
    # 导航底部 X.H 平台声明（0.3）：产品名/版本号一律取 ux_core 单源，不写死
    xh_footer = make_xh_footer(ux.PRODUCT_NAME, ux.APP_VERSION)
    rail._xh_footer = xh_footer
    rail.layout().addWidget(xh_footer)
    _perf_mark('foot_e')
    body.addWidget(rail)
    body.addLayout(_right_col, 1)
    outer.addLayout(body, 1)
    _perf_mark('rail_e')

    # 窄窗口自动折叠左侧导航为图标栏（小窗口适配）
    class _RailAutoCollapse(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Resize:
                rail.set_collapsed(ev.size().width() < 860)
            return False
    _rac = _RailAutoCollapse(w)
    w.installEventFilter(_rac)
    w._rac = _rac
    toast = ToastManager(w)
    w._toast = toast
    w._boot_banner = boot_banner
    # R38-19：启动自检移出主线程。窗口先建先亮，端口等待与身份校验都不再挡 show()。
    # 超时只有一个常量，文案与代码共用；忙等 time.sleep 已删除，改 150 ms QTimer 轮询。
    BOOT_READY_TIMEOUT_MS = 15000

    def _boot_check_async():
        _perf_mark('boot_b')
        _t0 = time.perf_counter()
        _st = {'done': False, 'timer': None,
               'deadline': _t0 + BOOT_READY_TIMEOUT_MS / 1000.0}

        def _finish(ok, short, msg):
            if _st['done']:
                return
            _st['done'] = True
            if _st['timer'] is not None:
                try:
                    _st['timer'].stop()
                except Exception:
                    _err()
            _ms = round((time.perf_counter() - _t0) * 1000.0, 1)
            _PANEL_BOOT.update({'ok': ok, 'short': short, 'msg': msg,
                                'pending': False, 'ready_ms': _ms})
            _boot_timing_write(ok, _ms, BOOT_READY_TIMEOUT_MS)
            _boot_mark('selfcheck_%s' % ('ok' if ok else 'fail'))
            if ok:
                boot_banner.hide()
            else:
                boot_banner.setText(msg)
                boot_banner.show()
                try:
                    toast.show('%s，数据区将显示 —' % short, 'warn')
                except Exception:
                    _err()

        def _on_ident(r):
            if _st['done']:
                return
            if isinstance(r, tuple) and len(r) >= 2:
                ok, why = bool(r[0]), str(r[1] or '')
            else:
                ok, why = False, '身份校验无返回'
            if ok:
                _finish(True, '', '')
            else:
                _finish(False, '端口 %d 被其他程序占用' % PANEL_PORT,
                        '启动自检：端口 %d 已被其他程序占用（%s），星虹面板服务无法启动，'
                        '本界面的数据区会一直显示 "—"。请关闭占用该端口的程序后重新打开星虹。'
                        % (PANEL_PORT, why))

        def _tick():
            if _st['done']:
                return
            if port_up(PANEL_PORT):
                if _st['timer'] is not None:
                    try:
                        _st['timer'].stop()
                    except Exception:
                        _err()
                qwait(_probe_panel_identity, _on_ident, key='boot_ident')
                return
            if time.perf_counter() >= _st['deadline']:
                _finish(False, '面板服务未能启动',
                        '启动自检：面板服务在 %d 秒内没有开始监听端口 %d。'
                        '请检查该端口是否被占用或安全软件拦截，然后重新打开星虹。'
                        % (BOOT_READY_TIMEOUT_MS // 1000, PANEL_PORT))

        if port_up(PANEL_PORT):
            qwait(_probe_panel_identity, _on_ident, key='boot_ident')
            return
        _perf_mark('boot_srv_b')
        try:
            start_server_thread()
        except Exception as _e:
            _finish(False, '面板后端加载失败',
                    '启动自检：面板后端加载失败（%s）。请确认安装目录下 backend/panel-server.py '
                    '存在且完整，然后重新打开星虹。' % _e)
            return
        _perf_mark('boot_srv_e')
        _tm = QTimer(w)
        _tm.setInterval(150)
        _tm.timeout.connect(_tick)
        _tm.start()
        _st['timer'] = _tm

    w._boot_check = _boot_check_async
    # R251：启动自检从 0 ms 挪到 160 ms——它要在主线程做端口探测/探活，
    # 挤在首帧那一轮里会加重尾帧；挪到首帧之后单独一轮，用户无感（只是错误提示晚 160 ms）。
    QTimer.singleShot(160, _boot_check_async)
    if not _PANEL_BOOT.get('ok', True):
        # 预置失败态（如外部注入）：横幅与 toast 直出
        boot_banner.setText(str(_PANEL_BOOT.get('msg') or '启动自检未通过：面板服务不可用'))
        boot_banner.show()
        try:
            QTimer.singleShot(
                800, lambda: toast.show(
                    '%s，数据区将显示 —，原因见顶部提示' % _PANEL_BOOT.get('short', '面板服务不可用'),
                    'warn'))
        except Exception:
            _err()

    # ============ Tab 1: 状态 ============
    # C1-B2：页面构建整段搬到 ui/pages_status.py；此处只做显式传参 + 取回 launcher
    # 后续（_probe_status / 定时器 / 推理页回调 / R37 指标缓冲）确实引用的 30 个句柄。
    # _safe_post 已上移 launcher 模块级（状态页 3 处 + 生图页 _comfy_power 共用）。
    _spage = build_page_status(
        stack=stack, w=w, api=api, qwait=qwait,
        _err=_err, _perf_mark=_perf_mark, _nav_icon=_nav_icon,
        _boot_mark=_boot_mark, _flow_strip_rebuild_soon=_flow_strip_rebuild_soon,
        _safe_post=_safe_post,
        _t=_t,
        fade_in=fade_in, fnum=fnum, make_plot=make_plot, pg=pg, smooth_xy=smooth_xy,
        GlassCard=GlassCard, Gauge=Gauge, FlowProgressBar=FlowProgressBar,
        AnimatedNumberLabel=AnimatedNumberLabel, ReflowGrid=ReflowGrid,
        UI_C=UI_C, ACCENT=ACCENT, ACCENT2=ACCENT2, TXT3=TXT3, R10=R10,
        CLK_SM_MAX=CLK_SM_MAX, CLK_MEM_MAX=CLK_MEM_MAX,
        _BOOT=_BOOT, _FLOW_STRIP_ON=_FLOW_STRIP_ON,
        xh_fmt_clk=xh_fmt_clk, xh_fmt_dur=xh_fmt_dur,
        xh_fmt_energy=xh_fmt_energy, xh_norm_power=xh_norm_power)
    BUF_CAP = _spage['BUF_CAP']
    _chart_na_probe = _spage['_chart_na_probe']
    _metrics_done = _spage['_metrics_done']
    _sv_done = _spage['_sv_done']
    back_to_live = _spage['back_to_live']
    btn_free = _spage['btn_free']
    btn_interrupt = _spage['btn_interrupt']
    btn_stopall = _spage['btn_stopall']
    cpu_big = _spage['cpu_big']
    gpu_big = _spage['gpu_big']
    mem_big = _spage['mem_big']
    ram_big = _spage['ram_big']
    lbl_chart_state = _spage['lbl_chart_state']
    lbl_energy = _spage['lbl_energy']
    lbl_energy2 = _spage['lbl_energy2']
    lbl_memclk = _spage['lbl_memclk']
    lbl_sm = _spage['lbl_sm']
    lbl_sv = _spage['lbl_sv']
    lbl_tp_compact = _spage['lbl_tp_compact']
    gauge_power = _spage['gauge_power']
    plot = _spage['plot']
    curves = _spage['curves']
    glow_curves = _spage['glow_curves']
    temp_curve = _spage['temp_curve']
    rows_buf = _spage['rows_buf']
    drawn = _spage['drawn']
    last_ts = _spage['last_ts']
    poll_energy = _spage['poll_energy']
    poll_metrics = _spage['poll_metrics']
    poll_sv = _spage['poll_sv']
    # chart_win 不再取：状态页的曲线窗口是它自己闭包内的变量，launcher 这个快照
    # 原本只被配置页写入、无任何读取方，B2-6 收口时已删该无效写入（见 pages_config）。
    # ============ Tab 2: 推理路线 ============
    # C1-B2：页面构建整段搬到 ui/pages_reasoning.py。跨页按钮（生图/视频/超分/中断）
    # 在此处尚未创建，用 lambda 在轮询回调执行时取值，与原闭包求值时刻一致。
    _rpage = build_page_reasoning(
        stack=stack, w=w, api=api, qwait=qwait, err=_err,
        perf_mark=_perf_mark, nav_icon=_nav_icon, state_dot=_state_dot, UI_C=UI_C,
        GlassCard=GlassCard, TwoColList=_TwoColList, KVModelCard=KVModelCard,
        LogDialog=LogDialog,
        get_cf_start_b=lambda: cf_start_b, get_cf_stop_b=lambda: cf_stop_b,
        get_btn_interrupt=lambda: btn_interrupt,
        get_gen_btn=lambda: gen_btn, get_vgen_btn=lambda: vgen_btn,
        get_up_btn=lambda: up_btn,
        get_gen_lbl=lambda: gen_lbl, get_vgen_lbl=lambda: vgen_lbl)
    poll_routes = _rpage['poll_routes']
    _routes_done = _rpage['routes_done']
    kv_chip_lbl = _rpage['kv_chip_lbl']
    lms_lbl = _rpage['lms_lbl']
    kv_cards = _rpage['kv_cards']
    # R07 自检探针：状态页 + 推理路线页的「无数据、断流、断流后恢复」走查入口。
    # 跨两页，留在 launcher 组装；只存引用，正常使用不受影响。
    w._probe_status = {'metrics_done': _metrics_done, 'routes_done': _routes_done,
                       'chart_state': lbl_chart_state, 'sv_lbl': lbl_sv,
                       'kv_chip': kv_chip_lbl, 'lms_lbl': lms_lbl,
                       'kv_cards': kv_cards,
                       'mem_big': mem_big, 'gpu_big': gpu_big,
                       'cpu_big': cpu_big, 'ram_big': ram_big,
                       'lbl_energy': lbl_energy, 'lbl_energy2': lbl_energy2,
                       'lbl_sm': lbl_sm,
                       'lbl_memclk': lbl_memclk, 'gauge_power': gauge_power,
                       'lbl_tp_compact': lbl_tp_compact,
                       'rows_buf': rows_buf, 'plot': plot, 'curves': curves,
                       'glow_curves': glow_curves, 'temp_curve': temp_curve,
                       'poll_metrics': poll_metrics, 'poll_routes': poll_routes}
    # ============ Tab 3: 生图 ============
    # C1-B2：页面构建整段搬到 ui/pages_gen.py；此处只做显式传参 + 取回 launcher
    # 后续（w._probe3 / w._invoke_map / t2 定时器 / 超分页缩略图工具）引用的 18 个句柄。
    # _safe_post 与 _comfy_power 留在模块级，由状态页/生图页共用（不复制第二份）。
    # _mbridge / tray 在配置页段才创建，按视频页同款懒取传入。
    _gpage = build_page_gen(
        stack=stack, w=w, api=api, qwait=qwait, _err=_err, _perf_mark=_perf_mark,
        _safe_post=_safe_post,
        comfy_power=lambda path, label, btn: _comfy_power(path, label, btn, qwait, w),
        _bridge=_bridge,
        _attach_delete_menu=_attach_delete_menu, _mk_clear_btn=_mk_clear_btn,
        make_grid_gallery=make_grid_gallery,
        mbridge=lambda: _mbridge, get_tray=lambda: tray,
        GlassCard=GlassCard, ChipGroup=ChipGroup, PreviewDialog=PreviewDialog,
        ShimmerCover=ShimmerCover, ShowcaseDialog=ShowcaseDialog,
        UI_C=UI_C,
        _THUMB_MAX_BYTES=_THUMB_MAX_BYTES,
        _out_local_size=_out_local_size, _style_form_labels=_style_form_labels,
        cf_err_text=cf_err_text, get_bytes=get_bytes,
        make_missing_cover=make_missing_cover,
        make_oversize_cover=make_oversize_cover, wall_icon=wall_icon)
    cf_start_b = _gpage['cf_start_b']
    cf_stop_b = _gpage['cf_stop_b']
    gen_btn = _gpage['gen_btn']
    gen_lbl = _gpage['gen_lbl']
    prompt = _gpage['prompt']
    gen_w = _gpage['gen_w']
    gen_h = _gpage['gen_h']
    steps = _gpage['steps']
    cfgv = _gpage['cfgv']
    seed = _gpage['seed']
    model_combo = _gpage['model_combo']
    outputs = _gpage['outputs']
    poll_outputs = _gpage['poll_outputs']
    poll_comfy = _gpage['poll_comfy']
    _comfy_done = _gpage['_comfy_done']
    _comfy_online = _gpage['_comfy_online']
    _fetch_thumb = _gpage['_fetch_thumb']
    _thumb_fail = _gpage['_thumb_fail']
    _THUMB_RETRY_MAX = _gpage['_THUMB_RETRY_MAX']
    # ============ Tab 4: 视频生成 ============
    # C1-B2：页面构建整段搬到 ui/pages_video.py；此处只做显式传参 + 取回 launcher
    # 后续（状态轮询、_probe3、刷新注册表）确实引用的 7 个句柄，其余语句原样不动。
    _vpage = build_page_video(
        stack=stack, w=w, api=api, qwait=qwait, err=_err,
        bridge=_bridge, mbridge=lambda: _mbridge, get_tray=lambda: tray,
        mute=_XH_MUTE,
        perf_mark=_perf_mark, nav_icon=_nav_icon, t=_t, UI_C=UI_C,
        GlassCard=GlassCard, ChipGroup=ChipGroup,
        make_grid_gallery=make_grid_gallery, mk_clear_btn=_mk_clear_btn,
        style_form_labels=_style_form_labels,
        video_loader_cls=VideoThumbnailLoader, ph_icon=_ph_icon,
        make_no_preview_cover=make_no_preview_cover,
        overlay_play_badge=overlay_play_badge, cf_err_text=cf_err_text,
        poll_outputs=poll_outputs, comfy_out_dir=COMFY_OUT_DIR,
        preview_dialog=PreviewDialog, get_bytes=get_bytes,
        attach_delete_menu=_attach_delete_menu)
    vwall = _vpage['vwall']
    poll_vwall = _vpage['poll_vwall']
    video_loader = _vpage['video_loader']
    vgen_btn = _vpage['vgen_btn']
    vgen_lbl = _vpage['vgen_lbl']
    vprompt = _vpage['vprompt']
    v_first = _vpage['v_first']
    # ============ Tab 5: 超分放大 ============
    # C1-B2：页面构建整段搬到 ui/pages_upscale.py；video_loader 与视频页共用同一实例。
    _upage = build_page_upscale(
        stack=stack, w=w, api=api, qwait=qwait, err=_err,
        bridge=_bridge, mbridge=lambda: _mbridge, get_tray=lambda: tray, mute=_XH_MUTE,
        perf_mark=_perf_mark, nav_icon=_nav_icon, t=_t, UI_C=UI_C,
        GlassCard=GlassCard, ChipGroup=ChipGroup,
        make_grid_gallery=make_grid_gallery, mk_clear_btn=_mk_clear_btn,
        style_form_labels=_style_form_labels, ph_icon=_ph_icon,
        make_no_preview_cover=make_no_preview_cover,
        overlay_play_badge=overlay_play_badge, cf_err_text=cf_err_text,
        preview_dialog=PreviewDialog, get_bytes=get_bytes,
        attach_delete_menu=_attach_delete_menu, comfy_out_dir=COMFY_OUT_DIR,
        state_dot=_state_dot, out_local_size=_out_local_size,
        thumb_max_bytes=_THUMB_MAX_BYTES, make_oversize_cover=make_oversize_cover,
        fetch_thumb=_fetch_thumb, thumb_fail=_thumb_fail,
        thumb_retry_max=_THUMB_RETRY_MAX, comfy_online=_comfy_online, R10=R10,
        video_loader=video_loader)
    uwall = _upage['uwall']
    poll_uwall = _upage['poll_uwall']
    up_btn = _upage['up_btn']  # Tab1 的状态轮询与 _probe3 都引用它
    poll_up = _upage['poll_up']  # t5 定时器 + 退场清理列表引用它
    # 自检探针：供单实例管道 walk 命令驱动三页空值路径（只存引用，不改变任何行为）
    w._probe3 = {'gen_btn': gen_btn, 'gen_lbl': gen_lbl, 'prompt': prompt, 'gen_w': gen_w, 'gen_h': gen_h,
                 'vgen_btn': vgen_btn, 'vgen_lbl': vgen_lbl, 'vprompt': vprompt, 'vfirst': v_first,
                 'up_btn': _upage['up_btn'], 'up_lbl': _upage['up_lbl'], 'up_img': _upage['up_img'],
                 'up_kind': _upage['up_kind'], 'up_engine': _upage['up_engine'],
                 'up_scale': _upage['up_scale'], 'up_scale_chips': _upage['up_scale_chips'],
                 'up_state_lbl': _upage['up_state_lbl'], 'up_refresh': _upage['up_refresh'],
                 'gen_steps': steps, 'gen_cfg': cfgv, 'gen_seed': seed, 'gen_model': model_combo,
                 'poll_up': _upage['poll_up']}
    # 调试探针（仅 XH_DEBUG_PROBE=1 时注册）：管道 invoke <name> 直接对按钮 .click()，
    # 用来真点一次（静态扫描只能证明名字存在，证明不了槽连对了对象）。
    # 正式运行态不建这张表 → invoke 命令是 no-op，发布包行为不变。
    if os.environ.get('XH_DEBUG_PROBE', '').strip() == '1':
        w._invoke_map = {
            'cf_start': cf_start_b, 'cf_stop': cf_stop_b,
            'free': btn_free, 'interrupt': btn_interrupt, 'stopall': btn_stopall,
            'gen': gen_btn, 'vgen': vgen_btn, 'up': up_btn,
        }
    # ============ Tab 6: 配置中心（四标签，对齐网页 cfgModal） ============
    # C1-B2：页面构建整段搬到 ui/pages_config.py；此处只做显式传参 + 取回 launcher
    # 后续确实引用的 10 个句柄（小星页复用 Prefs/SecretStore、t13/t14/t15、
    # apply_page_timers、_refresh_current、_state_done、管道 shot 5 / cfg1_jobs）。
    # 导航（说明与协议二级页面入口、返回位置）与 QSettings 读写语义一并不动。
    _cpage = build_page_config(
        stack=stack, w=w, api=api, qwait=qwait, _err=_err, _perf_mark=_perf_mark,
        _t=_t, _nav_icon=_nav_icon, _style_form_labels=_style_form_labels,
        _state_dot=_state_dot, get_bytes=get_bytes, ux=ux, xh_time=xh_time,
        page_cap=page_cap, _cfg_tab_now=_cfg_tab_now,
        BUF_CAP=BUF_CAP, rows_buf=rows_buf, back_to_live=back_to_live,
        _xh_cadence=_xh_cadence,
        _xh_apply_theme=_xh_apply_theme, CANONICAL_DIR=CANONICAL_DIR,
        UI_C=UI_C, CARD_BG=CARD_BG, CARD_BD=CARD_BD, R_CARD=R_CARD,
        R_INNER=R_INNER, TXT2=TXT2, TXT3=TXT3, TXT4=TXT4,
        SCROLL_V=SCROLL_V, SCROLL_H=SCROLL_H)
    adv_prefs = _cpage['adv_prefs']
    adv_secrets = _cpage['adv_secrets']
    w._adv_prefs = adv_prefs      # C1-B3：pageid 探针比对「配置页实例 is ux.prefs()」
    cfg_top = _cpage['cfg_top']
    cfg_jobs_tbl = _cpage['cfg_jobs_tbl']
    _cfg_done = _cpage['_cfg_done']
    poll_cfg = _cpage['poll_cfg']
    poll_jobs_tbl = _cpage['poll_jobs_tbl']
    poll_health = _cpage['poll_health']
    poll_cfg_logs = _cpage['poll_cfg_logs']
    cfg_sub_changed = _cpage['cfg_sub_changed']
    # ===================== 小星（本地模型助手） =====================
    # C1-B2-7：页面整段搬到 ui/pages_xiaoxing.py；此处只做显式传参。
    # Prefs/SecretStore 用的就是配置页那份 adv_prefs/adv_secrets——同一个实例，
    # 本页不再新建 ux.Prefs（证明见 docs/switches.md「Pre-同实例证明」与 id 探针）。
    _xpage = build_page_xiaoxing(
        stack=stack, w=w, ux=ux, _err=_err, _perf_mark=_perf_mark,
        adv_prefs=adv_prefs, adv_secrets=adv_secrets,
        GlassCard=GlassCard, UI_C=UI_C,
        XX_AGENT_TOOLS=XX_AGENT_TOOLS,
        XxChatWorker=XxChatWorker, XxAgentWorker=XxAgentWorker,
        xx_agent_load=xx_agent_load, xx_agent_save=xx_agent_save,
        xx_secret_register=xx_secret_register, xx_session_write=xx_session_write,
        _consent_log=_consent_log)
    xx_ask_consent = _xpage['xx_ask_consent']
    w._xx_ask_consent = xx_ask_consent

    # ===== C1-B2-8b：轮询 / 告警 / 节拍门控（原 L7381–7763，383 行 → app/timers.py）=====
    _tmr = build_polling_timers(
        w=w, api=api, qwait=qwait, stack=stack, R10=R10,
        _err=_err, _cost=_cost, _perf_mark=_perf_mark,
        _xh_cadence=_xh_cadence, _cad_hit=_cad_hit, _cad_rows=_cad_rows,
        _CAD_MEAS=_CAD_MEAS, _DECO=_DECO, _DECO_PAUSED=_DECO_PAUSED,
        _DECO_WHY=_DECO_WHY, _deco_deco_widgets=_deco_deco_widgets, get_tray=lambda: tray,
        _deco_set_paused=_deco_set_paused, _deco_resume_frame=_deco_resume_frame,
        _timer_hook=_timer_hook, drawn=drawn, _sv_done=_sv_done,
        _routes_done=_routes_done, _comfy_done=_comfy_done, _cfg_done=_cfg_done,
        poll_metrics=poll_metrics, poll_sv=poll_sv, poll_routes=poll_routes, poll_comfy=poll_comfy,
        poll_outputs=poll_outputs, poll_energy=poll_energy, poll_jobs_tbl=poll_jobs_tbl,
        poll_health=poll_health, poll_cfg=poll_cfg, poll_cfg_logs=poll_cfg_logs,
        poll_vwall=poll_vwall, poll_uwall=poll_uwall, poll_up=poll_up)
    _poll_timers, t2, poll_state = _tmr['timers'], _tmr['t2'], _tmr['poll_state']
    apply_page_timers = _tmr['apply_page_timers']
    _apply_poll_cadence, _hidden_alert = _tmr['cadence'], _tmr['hidden_alert']
    t_alert, _ALERT_ST = _tmr['t_alert'], _tmr['alert_state']

    # R03：子页切换即时刷新（子页级门控后不再等 6–12 s 定时器）；生成记录重进时
    # 清掉签名强制重建一次。C1-B2：函数体随配置页搬走，连接点仍留在此处（时序不变）。
    cfg_top.currentChanged.connect(cfg_sub_changed)

    # ===== tray & close behavior =====
    # R015-B3：工作线程 → 主线程 GUI 桥（托盘通知等跨线程 GUI 调用统一经此排队）
    class _MainBridge(QObject):
        run = Signal(object)

    _mbridge = _MainBridge()
    _mbridge.run.connect(lambda f: f())
    # ===== C1-B2-8c：托盘 / 关闭语义 / F5 刷新（原 L7776–8049，274 行 → app/tray.py）=====
    _tray_h = build_tray_and_close(
        w=w, api=api, on_select=on_select, stack=stack, _geo=_geo, _t=_t,
        PANEL_TITLE=PANEL_TITLE, _err=_err, _kill_children=_kill_children, _xh_cadence=_xh_cadence,
        _deco_set_paused=_deco_set_paused,
        _deco_resume_frame=_deco_resume_frame, _poll_timers=_poll_timers, t2=t2, poll_state=poll_state,
        apply_page_timers=apply_page_timers, _hidden_alert=_hidden_alert,
        _apply_poll_cadence=_apply_poll_cadence, t_alert=t_alert, _ALERT_ST=_ALERT_ST, rows_buf=rows_buf,
        last_ts=last_ts, _chart_na_probe=_chart_na_probe, cfg_jobs_tbl=cfg_jobs_tbl,
        poll_metrics=poll_metrics, poll_sv=poll_sv, poll_routes=poll_routes, poll_comfy=poll_comfy,
        poll_outputs=poll_outputs, poll_vwall=poll_vwall, poll_up=poll_up, poll_uwall=poll_uwall,
        poll_cfg=poll_cfg, poll_jobs_tbl=poll_jobs_tbl, poll_health=poll_health)
    tray, _refresh_current = _tray_h['tray'], _tray_h['refresh']
    # R10（XH-067）：向管道命令暴露删除演练入口（命令在 main() 作用域，取不到 build_ui 局部）
    w._r10_del = {'outs': outputs, 'uwall': uwall, 'vwall': vwall,
                  'refresh': (poll_outputs, poll_uwall, poll_vwall),
                  'ask': _ask_delete, 'state': _del_state, 'names': _wall_names}
    w._refresh_current = _refresh_current
    w._nav_select = on_select
    w._stack = stack
    return w


def load_bundled_font():
    """加载打包的 Noto Sans SC（N3），换机不回退；成功返回 True。"""
    candidates = []
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, 'fonts', 'NotoSansSC-VF.ttf'))
        candidates.append(os.path.join(os.path.dirname(sys.executable),
                                      'fonts', 'NotoSansSC-VF.ttf'))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, 'assets', 'fonts', 'NotoSansSC-VF.ttf'))
    for c in candidates:
        try:
            if c and os.path.exists(c):
                if QFontDatabase.addApplicationFont(c) >= 0:
                    return True
        except Exception:
            _err()
    return False


def _diag_bind(window, activate, hard_exit):
    """C1-B1：自检命令的模块级依赖一次性显式注入（禁止 app 层反向 import 本文件）。"""
    from app.diag_cmds import DiagCtx, dispatch as _dispatch
    ctx = DiagCtx(
        w=window, activate=activate, hard_exit=hard_exit,
        _err=_err, api=api, _t=_t, ux=ux,
        AnimatedNumberLabel=AnimatedNumberLabel,
        FlowProgressBar=FlowProgressBar, GlassCard=GlassCard,
        CLK_MEM_MAX=CLK_MEM_MAX, CLK_SM_MAX=CLK_SM_MAX,
        FIT_STAT=FIT_STAT, _BOOT=_BOOT,
        _CAD_KEYS=_CAD_KEYS, _CAD_MEAS=_CAD_MEAS,
        _CLK_STAT=_CLK_STAT, _DECO_TM=_DECO_TM,
        _DUR_STAT=_DUR_STAT, _EN_STAT=_EN_STAT,
        _ERR_LOGPATH=_ERR_LOGPATH, _NUM_FRAMES=_NUM_FRAMES,
        _OPAQUE_BASE=_OPAQUE_BASE, _PCOST=_PCOST,
        _PW_STAT=_PW_STAT, _SETTEXT_N=_SETTEXT_N,
        _cap_colors=_cap_colors, _cap_grab=_cap_grab,
        _deco_diag=_deco_diag,
        _errs_dump=_errs_dump, _errs_load_whitelist=_errs_load_whitelist,
        _ftrace_start=_ftrace_start,
        _perf_dump=_perf_dump, _perf_mark=_perf_mark,
        _proc_age_s=_proc_age_s, _xh_apply_theme=_xh_apply_theme,
        xh_fmt_clk=xh_fmt_clk, xh_fmt_dur=xh_fmt_dur,
        xh_fmt_energy=xh_fmt_energy, xh_norm_power=xh_norm_power,
    )
    return lambda msg: _dispatch(ctx, msg)


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt as _Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        _Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # R38-19：启动自检已移入 build_ui 的异步任务（QTimer.singleShot(0, _boot_check_async)）。
    # 主线程不再 sleep 等端口，窗口先建先亮；端口等待与身份校验都在后台，结果异步回填横幅。
    _PANEL_BOOT.update({'ok': True, 'msg': '', 'short': '', 'pending': True})
    _pcm = bool(os.environ.get('XH_PCOST'))

    if _pcm:
        class _CostApp(QApplication):
            """R20-10：paint 实耗探针（仅 XH_PCOST=1）。
            用 QApplication.notify 包住事件派发，能测到 paintEvent 真实耗时；
            事件过滤器只能测"派发前"，测不到耗时，这是两回事。"""

            def notify(self, recv, ev):
                if ev.type() == QEvent.Paint:
                    _t0 = time.perf_counter()
                    _r = super().notify(recv, ev)
                    try:
                        _on = recv.objectName() or ''
                        if not _on:
                            # 无名控件用尺寸区分（定位"整页重绘"是谁）
                            _on = '@%dx%d' % (int(recv.width()), int(recv.height()))
                        _pc_bump(type(recv).__name__, _on,
                                 (time.perf_counter() - _t0) * 1e6)
                    except Exception:
                        _err()
                    return _r
                return super().notify(recv, ev)

        app = _CostApp(sys.argv)
    else:
        app = QApplication(sys.argv)
    app.setApplicationName(PANEL_TITLE)
    # R36-N3：关窗只隐藏/询问，不因「最后一个窗口关闭」退出。
    # 真正退出仍走 confirm_exit → QApplication.quit()，不受影响。
    try:
        app.setQuitOnLastWindowClosed(False)
    except Exception:
        _err()
    _boot_mark('app_ready')
    if _ATTR['on']:
        _attr_install()          # R254：首帧后长帧归因（XH_ATTR 未设则直接返回）
    if XH_PAINT:
        _install_paint_spy(app, XH_PAINT)
    if XH_TSTAT:
        _install_timer_stat(app, XH_TSTAT)
    if XH_STACK:
        _install_stack_sampler(app, XH_STACK)
    if XH_PROFILE:
        try:
            _ft = QTimer()
            _ft.setInterval(16)
            _ft.timeout.connect(_frame_tick_probe)
            _ft.start()
            app._frame_timer = _ft
        except Exception:
            _err()
    # R023：启动时应用持久化的动画/性能偏好（进阶配置中心）
    try:
        _ap = ux.prefs()
        _lvl = _ap.get('animLevel')
        # R38-19：标准档过渡 260→120 ms（重页面已直接硬切，见 page_transition）
        XH_ADV['anim'] = 0 if _lvl == '关闭' else 120
        XH_ADV['reduce'] = bool(_ap.get('reduceMotion')) or _lvl in ('降级', '关闭')
        XH_ADV['perf'] = str(_ap.get('perfMode') or '均衡')
        XH_ADV['theme'] = str(_ap.get('theme') or '原版深色')
        if XH_ADV['perf'] == '省电':
            XH_ADV['anim'] = min(XH_ADV['anim'], 120)
            XH_ADV['reduce'] = True
    except Exception:
        _err()
    # 单实例锁：多开时激活已有窗口后退出，避免重复 GUI
    # R250：抓图/自检脚本用 XH_INSTANCE=cap 起私有实例，自己的 quit 只结束自己的实例，
    # 不会再把常驻面板带走（07:28 那次空档就是脚本 quit 误杀了常驻实例）。
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    LOCK_NAME = 'XingHongPanelSingleInstance_v1' + (os.environ.get('XH_INSTANCE') or '')
    _probe = QLocalSocket()
    _probe.connectToServer(LOCK_NAME)
    _conn = _probe.waitForConnected(200)
    if _conn:
        # R07：进程被 taskkill /F 硬杀后，Windows 可能残留同名管道，connectToServer 仍"成功"，
        # 新进程会误判「已有实例」直接退出（双击没反应）。先 ping，回 pong 才是真实例。
        _probe.write(b'ping'); _probe.flush()
        _probe.waitForBytesWritten(200)
        _alive = False
        if _probe.waitForReadyRead(500):
            _alive = bytes(_probe.readAll()).strip() == b'pong'
        if _alive:
            _probe.write(b'show'); _probe.flush()
            _probe.waitForBytesWritten(200)
            _probe.disconnectFromServer()
            return
        try:
            _probe.disconnectFromServer()
        except Exception:
            _err()
    QLocalServer.removeServer(LOCK_NAME)
    _xh_cmd = {"dispatch": None}
    _single = QLocalServer()
    _single.listen(LOCK_NAME)

    def _activate():
        # R37-1：隐藏到托盘后窗口 isVisible()=False，旧实现只遍历可见顶层窗口，
        # 管道 `show` / 第二次启动都「点了没反应」。改为优先走 show_window，
        # 顺带把隐藏期的降频间隔还原并立即补拍。
        try:
            _sw = getattr(w, '_show_window', None)
        except NameError:
            _sw = None
        if _sw is not None:
            _sw()
            return
        for wgt in app.topLevelWidgets():
            if wgt.isVisible():
                wgt.showNormal(); wgt.activateWindow()

    def _on_xh_conn():
        c = _single.nextPendingConnection()
        if c is None:
            return
        # R032：撤销 R031 的 disconnected→deleteLater——实测客户端写完即断开时，
        # 该回收会在数据到达前销毁套接字，命令静默丢失（quit 两次探针复现）。
        # R26：改为「断开后延迟 300ms 回收 + 4s 兜底回收」，既不抢在数据前删，
        # 也不再每个命令永久残留 1 个 QLocalSocket（≈3 个内核句柄）。
        _st = {"done": False, "gone": False}

        def _rd():
            try:
                msg = bytes(c.readAll()).decode("utf-8", "replace").strip()
                # R023：客户端 writes 后立刻断开，首次 readyRead 可能读到空串，
                # 导致第一次 quit 被吞（R022 测试员 2/2 复现）。补一次短等待重读。
                if not msg and c.waitForReadyRead(200):
                    msg = bytes(c.readAll()).decode("utf-8", "replace").strip()
            except Exception:
                msg = ""
            if not msg:
                return
            _st["done"] = True
            if msg == 'ping':
                try:
                    c.write(b'pong'); c.flush()
                except Exception:
                    _err()
                return
            if _xh_cmd["dispatch"] is not None:
                _xh_cmd["dispatch"](msg)
            elif msg == "show":
                _activate()
        c.readyRead.connect(_rd)
        _rd()

        def _reap():
            if _st["gone"]:
                return
            if not _st["done"]:
                _rd()
            _st["gone"] = True
            try:
                c.readyRead.disconnect(_rd)
            except Exception:
                _err()
            c.deleteLater()

        c.disconnected.connect(lambda: QTimer.singleShot(300, _reap))
        QTimer.singleShot(4000, _reap)
    _single.newConnection.connect(_on_xh_conn)
    # N3：先加载打包的 Noto Sans SC（换机不回退），再设置族名
    load_bundled_font()
    # R007（D-02）：显式应用字体。实测本机「Microsoft YaHei UI」已被替换为手写体
    # 字形（族名仍在但字形非雅黑），故首选 Noto Sans SC，缺失时回退雅黑/黑体。
    _appfont = QFont()
    _appfont.setFamilies(['Noto Sans SC', 'Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei'])
    # R38-23：原 setPointSize(10)。10pt 在三档缩放下恒定解析为 13px
    # （ascent 13.00、字宽 286.00 与 13px 逐项相同），改 px 后视觉零变化。
    _appfont.setPixelSize(13)
    app.setFont(_appfont)
    # R5：启动对账——上次退出若走了「先退、后台收尾」，这里看托管服务到底停完没有，
    # 写一行 startup.log 并把提示交给状态页（infra.exitd.STATE['msg']）。放在单实例
    # 检查之后：第二实例直接返回，不该替正在跑的实例消费这条对账。
    try:
        _exitd.reconcile()
    except Exception:
        _err()
    w = build_ui()
    _boot_mark('ui_built')

    # 自检命令：show=激活；quit=退出；shot N PATH=切到第N页并截图到PATH
    def _hard_exit(code=0):
        # R032：实测 os._exit 在多原生线程（Qt 网络线程/服务线程）下退出码偶发
        # 0xC0000005（DLL 卸载竞态）。TerminateProcess 跳过 DLL_PROCESS_DETACH，
        # 退出码必为 0。仅用于管道 quit 自检路径，不影响正常 UI 关闭流程。
        try:
            import ctypes
            _k = ctypes.windll.kernel32
            # R032 修复：GetCurrentProcess 伪句柄为 0xFFFF...FF（64 位），
            # 默认 restype=c_int 会截断成 32 位导致 TerminateProcess 报 err=183
            _k.GetCurrentProcess.restype = ctypes.c_void_p
            _k.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            _ok = _k.TerminateProcess(_k.GetCurrentProcess(), code)
            with open(os.path.join(os.environ.get('TEMP', '.'), '_xh_quit.log'),
                      'a', encoding='utf-8') as _f:
                _f.write('TerminateProcess ok=%r err=%s\n'
                         % (_ok, _k.GetLastError()))
            if not _ok:
                os._exit(code)
        except Exception as _e:
            try:
                with open(os.path.join(os.environ.get('TEMP', '.'),
                                       '_xh_quit.log'), 'a',
                          encoding='utf-8') as _f:
                    _f.write('hard_exit EXC %r\n' % _e)
            except Exception:
                _err()
            os._exit(code)

    # C1-B1：自检命令改路由表（xinghong/app/diag_cmds.py）；原 51 个分支按原顺序迁入
    # ROUTES，谓词与分支体逐字保留，依赖经 DiagCtx 显式注入。
    _xh_cmd["dispatch"] = _diag_bind(w, _activate, _hard_exit)

    # R36-N1：首帧打点——主窗第一次收到 Paint 即「可见首帧」。
    # 挂在 app 上按「属于本窗」过滤：顶层窗口常被 central widget 全覆盖，
    # 自身的 Paint 可能一次都不来（实测首版挂 w 上 never fired）。
    class _BootPaint(QObject):
        _done = False

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Paint and not _BootPaint._done:
                try:
                    _ww = obj.window()
                except Exception:
                    _ww = None
                if _ww is w:
                    _BootPaint._done = True
                    _boot_mark('first_paint')
                    try:
                        app.removeEventFilter(self)
                    except Exception:
                        pass
            return False

    _bp = _BootPaint()
    app.installEventFilter(_bp)
    w._boot_paint_filter = _bp
    _perf_start(w)          # R250：首帧前装帧节拍探针（XH_PERF_LOG 未设则零开销）
    if os.environ.get('XH_PREWARM') == '1':
        # R251 诊断开关：show 之前把样式解析与一次离屏光栅化做掉，
        # 用来判断「首帧那 150-200 ms」是不是首次样式/字形光栅化的成本。
        _perf_mark('prewarm_b')
        _PERF['in_prewarm'] = True
        try:
            w.ensurePolished()
            for _c in w.findChildren(QWidget):
                try:
                    _c.ensurePolished()
                except Exception:
                    pass
            w.render(QPixmap(w.size()))
        except Exception:
            _err()
        _PERF['in_prewarm'] = False
        _perf_mark('prewarm_e')
    if getattr(w, '_default_maximized', False):
        w.showMaximized()
    else:
        w.show()
    _boot_mark('shown')
    # 小星：启动时补一次隐私说明同意（门禁复用 Prefs.consent_ok / CONSENT_VERSION）。
    # 拒绝只影响小星三个页面，不影响其他业务页；延后 600 ms 避开首帧动画。
    # C1-B2-7：同意检查随小星页搬进 ui/pages_xiaoxing.py——那里用的就是配置页那份
    # adv_prefs（页面模块里仅配置页构造一次；main 里 R023 那处只读 Prefs 与此无关），
    # 同意检查不再自建。时序（600 ms）不变。
    QTimer.singleShot(600, w._xx_ask_consent)
    # R14：样式表/polish 之后再确认一次「背景不透明」标记（见 setCentralWidget 后注释）。
    if not XH_NOFIX:
        def _set_opaque():
            # R17-2 修 NameError：back 是构建函数内局部变量，main 作用域取不到，
            # 原 lambda 每次都抛 NameError，WA_OpaquePaintEvent 从未生效。
            # R18-3a：XH_NOOPAQUE=1 时保持关闭（A/B 用）。
            if XH_NOOPAQUE:
                return
            b = w.findChild(QWidget, 'root')
            if b is not None:
                b.setAttribute(Qt.WA_OpaquePaintEvent, True)
        for _d in (0, 400, 1200):
            QTimer.singleShot(_d, _set_opaque)
        # R20：QSS/主题切换会触发 QEvent.StyleChange，实测会把 `QWidget#root` 的
        # WA_OpaquePaintEvent 读回值从 1 打成 0（QSS 里 `QWidget#root{background:transparent}`
        # 会被重写背景规则）——等价于 R14 的 NameError 复发，静悄悄多出顶层每拍补底。
        # 这里在任一样式变更后一拍内把标记补回去（幂等，不触发新的 StyleChange）。
        _oqp = [False]

        def _repair_opaque():
            _oqp[0] = False
            _set_opaque()
            _ob_repair()          # R20：甲涉及的文本控件同样要在 StyleChange 后补设

        class _OpaqueGuard(QObject):
            # R20-3：StyleChange 只有落在「自己/卡片/已 arm 的控件」时才需要补设——
            # 其余（周期刷新的普通 QLabel）不再触发全量 _ob_repair/_ob_scan，
            # 否则一次 setStyleSheet 会放大成整窗 4 遍重绘（B3 实测 809k px/s 尖峰）。
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.StyleChange:
                    if (obj is w or getattr(obj, '_xh_ob', False)
                            or isinstance(obj, GlassCard)):
                        if not _oqp[0]:
                            _oqp[0] = True
                            QTimer.singleShot(0, _repair_opaque)
                elif ev.type() == QEvent.Resize and obj is w:
                    # R20-钩子①（resize）：重排后标签位置/底片尺寸都变了，重新扫描装一遍
                    # R20-4：流光条带的行分组也随重排变化 → 一并重建
                    # R20-10：加去抖（150% 下窗口被系统钳制会连续多次 Resize）
                    QTimer.singleShot(0, _ob_scan)
                    if _FLOW_STRIP_ON:
                        if os.environ.get('XH_STRIP_DBG'):
                            print('[strip] win resize %dx%d' % (
                                int(obj.width()), int(obj.height())), flush=True)
                        _flow_strip_rebuild_soon()
                return False

        try:
            _dq = QApplication.instance()
            _og = _OpaqueGuard()
            _dq.installEventFilter(_og)
            _dq._opaque_guard = _og       # 防 GC
            _ob_install()
            # 甲的自铺底要等卡片/背景各画过一帧才有底可切；多时点扫描覆盖 12 页
            for _d in (0, 400, 1200, 3000, 6000):
                QTimer.singleShot(_d, _ob_scan)
            # R20-钩子②（页面切换）：新页的标签第一次显示时补扫描一次
            try:
                _stk0 = w.findChild(QStackedWidget)
                if _stk0 is not None:
                    _stk0.currentChanged.connect(lambda *_: QTimer.singleShot(0, _ob_scan))
            except Exception:
                _err()
        except Exception:
            _err()
    # R007（D-01）：DWM 深色标题栏；（D-08）：Ctrl+1..N 切页 + F5 刷新
    try:
        hwnd = int(w.winId())
        for _attr in (20, 19):
            _val = ctypes.c_int(1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, _attr, ctypes.byref(_val), ctypes.sizeof(_val)) == 0:
                break
    except Exception:
        _err()
    try:
        for _i in range(w._stack.count()):
            _sc = QShortcut(QKeySequence('Ctrl+%d' % (_i + 1)), w)
            _sc.activated.connect(lambda idx=_i: w._nav_select(idx))
        _f5 = QShortcut(QKeySequence('F5'), w)
        _f5.activated.connect(w._refresh_current)
    except Exception:
        _err()
    if _deco_on():
        _deco_adopt(app)          # R16：接管早建的装饰控件（停私有定时器 → 叠加层统一绘制）
        try:
            _DECO['ov'].raise_()  # 页面/卡片均已建好后再置顶一次（之后节拍内不再动 z 序）
            _DECO['ov']._need_raise = False
        except Exception:
            _err()
    _rc = app.exec()
    try:
        # 正常退出留痕：菜单/托盘/关闭按钮都汇聚到这里；崩溃（被杀）不会写
        _d = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'XingHongPanel')
        os.makedirs(_d, exist_ok=True)
        with open(os.path.join(_d, '_graceful_exit.json'), 'w', encoding='utf-8') as _f:
            json.dump({"line": "xinghong", "ts": time.time(),
                       "at": xh_time.stamp(),
                       "how": _XH_EXIT_HOW['v'], "rc": _rc, "pid": os.getpid()},
                      _f, ensure_ascii=False)
    except Exception:
        _err()
    # R4：正常退出的留痕已落盘，用 TerminateProcess(...,0) 收尾——PySide 析构 + 解释器
    # finalize 实测要 0.2~0.6 s，「强制退出」要求 1 s 内退干净，这一秒不能白送给清理阶段。
    # 退出码明确为 0；服务子进程已在面板 finish() 里强杀过。
    try:
        # 窗口模式（PyInstaller --noconsole）下 sys.stdout / sys.stderr 为 None，
        # 直接 flush 会抛 AttributeError 并写进 _errs_live.log；退出路径不允许再失败。
        if sys.stdout is not None:
            sys.stdout.flush()
        if sys.stderr is not None:
            sys.stderr.flush()
        # 7501 已把 GetCurrentProcess.restype 设成 c_void_p（伪句柄 0xFFFF…FF），
        # 这里若用 ctypes 默认 int 传参就是 OverflowError；argtypes 必须一起给。
        _k32 = ctypes.windll.kernel32
        _k32.GetCurrentProcess.restype = ctypes.c_void_p
        _k32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        _k32.TerminateProcess(_k32.GetCurrentProcess(), 0)
    except Exception:
        _err()
    os._exit(_rc)


if __name__ == '__main__':
    # R5：退出守护进程（面板已退，这里替它把托管服务停完并补记 exit.log）。
    # 必须早于 main()：守护进程不走 Qt，也不碰单实例锁。
    if len(sys.argv) >= 3 and sys.argv[1] == '--exit-daemon':
        _exitd.run(sys.argv[2])
    else:
        main()

