# -*- coding: utf-8 -*-
"""自检（单实例管道）命令路由表。

C1-B1：由 xinghong_launcher.py 的原 _xh_dispatch if/elif 链机械搬出，
分支体逐字保留，只把依赖改成 DiagCtx 显式注入（禁止 import xinghong_launcher）。

路由顺序即原 elif 顺序；谓词原文照抄，行为等价。
"""
import ctypes
import json
import os
import sys
import threading
import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QFrame,
                               QGridLayout, QLabel, QListWidget, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget,
                               QTabWidget, QTextBrowser, QToolTip, QWidget)

# C1-B3：两个跨层配置对象直接读 infra.config（不再经 DiagCtx 中转）
from infra.config import XH_ADV, XH_CADENCE
import pathcfg as _pathcfg

# C1：模块导出清单（tools/check_imports.py 校验）
__all__ = ['DiagCtx', 'Probe', 'ROUTES', 'COMMANDS', 'COMMAND_NAMES', 'dispatch']


class Probe:
    """diag 分支公共探针（C1-B2 第 4 批：原各分支散落的 _wait/_snap 收口成这一份）。

    wait(ms)   主线程跑 processEvents，等切页动画 / 布局落定后再断言或截图。
    snap(tag)  先 wait(pause) 再截主窗；构造时给了 out 就写 'snap ok/FAIL' 文本，
               没给则异常走 err()（与原各分支两种写法一一对应）。
    grab_to(p) 按绝对路径抓主窗（单测定点快照用，无 tag）。
    """

    def __init__(self, w, err, prefix='', out=None, tick=0.03, pause=250):
        self.w = w
        self.err = err
        self.prefix = prefix
        self.out = out
        self.tick = tick
        self.pause = pause

    def wait(self, ms):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(self.tick)

    def snap(self, tag, full=False):
        self.wait(self.pause)
        try:
            if full:
                scr = self.w.screen() or QApplication.primaryScreen()
                scr.grabWindow(0).save('%s_%s.png' % (self.prefix, tag))
            else:
                self.w.grab().save('%s_%s.png' % (self.prefix, tag))
            if self.out is not None:
                self.out.append('snap %s ok' % tag)
        except Exception as e:
            if self.out is not None:
                self.out.append('snap %s FAIL %r' % (tag, e))
            else:
                self.err()

    def grab_to(self, path):
        try:
            self.w.grab().save(path)
        except Exception:
            self.err()


class DiagCtx:
    """管道命令的依赖容器；属性由 launcher 在 _diag_bind() 里一次性注入。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)



# 原分支 0：msg == "show"
def _h_00_show(ctx, msg, parts):
    w, _activate = ctx.w, ctx.activate
    _activate()

# 原分支 1：msg == "quit2"
def _h_01_quit2(ctx, msg, parts):
    w, _hard_exit = ctx.w, ctx.hard_exit
    _hard_exit(0)

# 原分支 2：msg == "quit"
def _h_02_quit(ctx, msg, parts):
    w, _err, _hard_exit, api = ctx.w, ctx._err, ctx.hard_exit, ctx.api
    def _blog(_s):
        try:
            with open(os.path.join(os.environ.get('TEMP', '.'),
                                   '_xh_quit.log'), 'a',
                      encoding='utf-8') as _f:
                _f.write(_s + '\n')
        except Exception:
            _err()
    _blog('quit received')
    if getattr(w, '_quitting', False):
        _blog('quit reentry blocked')
        return
    w._quitting = True
    try:
        w.hide()
        _blog('hidden')
    except Exception:
        _err()
    try:
        api('POST', '/api/stopall', {}, timeout=2)
        _blog('stopall ok')
    except Exception as _e:
        _blog('stopall EXC %r' % _e)
    _blog('calling hard_exit')
    _hard_exit(0)

# 原分支 3：len(parts) == 4 and parts[0] == "shot" and parts[1] == "5"
def _h_03_shot(ctx, msg, parts):
    w, _cap_grab, _err = ctx.w, ctx._cap_grab, ctx._err
    try:
        sub = int(parts[2]); path = parts[3]
    except Exception:
        return
    def _do_shot():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(5)
            # R024：sub>=20 表示设置顶层分区（20+index）
            # R036：cfgInner 已并入 cfgTop 单级承载——
            # 0数据设置 1生成记录 2运行日志 3进阶配置 4说明与协议（单页目录）；
            # 旧 sub>=20 语义（20常规→数据 21进阶 22说明 23隐私 24协议）按 -20 映射兼容。
            # R036 修复：R024 起 `_do_shot` 内 `sub -= 20` 使 sub 成为本地变量，
            # UnboundLocalError 被裸 except 吞掉（探针实测 traceback 复现）——
            # 管道切子页自 R024 起从未真正生效；改用局部 _idx，不再给 sub 赋值。
            _idx = sub - 20 if sub >= 20 else sub
            _qt = w.findChild(QTabWidget, 'cfgTop')
            if _idx == 4:
                # R255：4 = 说明与协议二级页面（不在 cfgTop 里）
                _op = getattr(w, '_xh_open_legal', None)
                if _op is None:
                    with open(path + '.err', 'w', encoding='utf-8') as _f:
                        _f.write('shot 5: legal page opener missing (sub=%d)\n' % sub)
                else:
                    _op()
            elif _qt is None:
                # R247：找不到 cfgTop 时不再静默——落 .err 让调用方能判定
                with open(path + '.err', 'w', encoding='utf-8') as _f:
                    _f.write('shot 5: cfgTop not found (sub=%d)\n' % sub)
            elif 0 <= _idx < _qt.count():
                _qt.setCurrentIndex(_idx)
            else:
                with open(path + '.err', 'w', encoding='utf-8') as _f:
                    _f.write('shot 5: sub %d out of range (count=%d)\n'
                             % (_idx, _qt.count()))
        except Exception:
            _err()
        _cap_grab(w, path, tag='shot 5 %d' % sub, wait=400)
    QTimer.singleShot(0, _do_shot)

# 原分支 4：len(parts) == 2 and parts[0] == "nav"
def _h_04_nav(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    try:
        idx = int(parts[1])
    except Exception:
        return
    def _do_nav():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(idx)
        except Exception:
            _err()
    QTimer.singleShot(0, _do_nav)

# 原分支 5：len(parts) == 2 and parts[0] == "sb"
def _h_05_sb(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _p = parts[1]

    def _do_sb():
        rows = []
        _nv = 0
        _nh = 0
        try:
            for _sa in w.findChildren(QAbstractScrollArea):
                _v = _sa.verticalScrollBar()
                _h = _sa.horizontalScrollBar()
                _vp = _sa.viewport()
                try:
                    _inner = _sa.widget()
                except Exception:
                    _inner = None
                _cw = int(_inner.width()) if _inner is not None else int(_vp.width())
                _ch = int(_inner.height()) if _inner is not None else int(_vp.height())
                _pv = bool(_v.isVisible())
                _ph = bool(_h.isVisible())
                _nv += 1 if _pv else 0
                _nh += 1 if _ph else 0
                _par = _sa.parentWidget()
                try:
                    _rows = int(_sa.count()) if hasattr(_sa, 'count') else -1
                except Exception:
                    _rows = -1
                rows.append({
                    'cls': type(_sa).__name__,
                    'name': _sa.objectName(),
                    'rows': _rows,
                    'vis': bool(_sa.isVisible()),
                    'hid': bool(_sa.isHidden()),
                    'parent': _par.objectName() if _par is not None else '',
                    'vvis': _pv,
                    'vmax': int(_v.maximum()),
                    'hvis': _ph,
                    'hmax': int(_h.maximum()),
                    'vp': [int(_vp.width()), int(_vp.height())],
                    'content': [_cw, _ch],
                    'over': [_cw - int(_vp.width()), _ch - int(_vp.height())],
                    'size': [int(_sa.width()), int(_sa.height())]})
            with open(_p, 'w', encoding='utf-8') as _f:
                json.dump({'counts': {'areas': len(rows), 'vbar_vis': _nv,
                                      'hbar_vis': _nh},
                           'areas': rows}, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_sb)

# 原分支 6：len(parts) == 2 and parts[0] == "edge"
def _h_06_edge(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _p = parts[1]

    def _do_edge():
        out = {'win': [int(w.width()), int(w.height())],
               'right_band': [], 'over_parent': [],
               'clipped': []}
        try:
            _ww = int(w.width())
            _lim = _ww - 80
            for _c in w.findChildren(QWidget):
                if not _c.isVisible():
                    continue
                _pt = _c.mapTo(w, QPoint(0, 0))
                _x = int(_pt.x())
                _y = int(_pt.y())
                _cw2 = int(_c.width())
                _ch2 = int(_c.height())
                _r = _x + _cw2 - 1
                _rec = {'cls': type(_c).__name__,
                        'name': _c.objectName(),
                        'x': _x, 'y': _y, 'w': _cw2, 'h': _ch2,
                        'right': _r,
                        'parent': _c.parentWidget().objectName()
                        if _c.parentWidget() is not None else ''}
                if _r >= _lim and _cw2 >= 2:
                    out['right_band'].append(_rec)
                if _r >= _ww:
                    _rec['over_win_px'] = _r - _ww + 1
                    out['clipped'].append(_rec)
                _par = _c.parentWidget()
                if _par is not None:
                    _pw = int(_par.width())
                    if _cw2 > _pw:
                        out['over_parent'].append({
                            'cls': type(_c).__name__,
                            'name': _c.objectName(),
                            'w': _cw2, 'parent': _par.objectName(),
                            'parent_w': _pw, 'over_px': _cw2 - _pw})
            out['right_band'].sort(key=lambda r: -r['right'])
            out['over_parent'].sort(key=lambda r: -r['over_px'])
            with open(_p, 'w', encoding='utf-8') as _f:
                json.dump(out, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_edge)

# 原分支 7：len(parts) == 2 and parts[0] == "shot"
def _h_07_shot(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _sp = parts[1]

    def _do_shot():
        try:
            w.grab().save(_sp)
        except Exception:
            _err()

    QTimer.singleShot(20, _do_shot)

# 原分支 8：len(parts) == 2 and parts[0] == "bootinfo"
def _h_08_bootinfo(ctx, msg, parts):
    w, _BOOT, _err, _proc_age_s = ctx.w, ctx._BOOT, ctx._err, ctx._proc_age_s
    _bp = parts[1]

    def _do_bootinfo():
        try:
            _m = dict(_BOOT['marks'])
            _seq = [(k, v) for k, v in sorted(_m.items(), key=lambda kv: kv[1])]
            _d = {'pid': os.getpid(),
                  'proc_age_now_s': round(_proc_age_s(), 3),
                  'marks': dict(_seq),
                  'delta_s': {},
                  'path': None}
            _prev = None
            for _k, _v in _seq:
                if _prev is not None:
                    _d['delta_s'][_k] = round(_v - _prev, 3)
                _prev = _v
            with open(_bp, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_bootinfo)

# 原分支 9：len(parts) == 3 and parts[0] == "ftrace"
def _h_09_ftrace(ctx, msg, parts):
    w, _ftrace_start = ctx.w, ctx._ftrace_start
    _fp = parts[1]
    try:
        _sec = float(parts[2])
    except Exception:
        return
    QTimer.singleShot(0, lambda: _ftrace_start(w, _fp, _sec))

# 原分支 10：len(parts) == 2 and parts[0] == "trayinfo"
def _h_10_trayinfo(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _tp = parts[1]

    def _do_trayinfo():
        try:
            _tj = getattr(w, '_tray', None)
            _tmj = getattr(w, '_tray_menu', None)
            _st = getattr(w, '_tray_state', {}) or {}
            _acts = []
            for _a in (_tmj.actions() if _tmj is not None else []):
                _acts.append({'text': _a.text(),
                              'sep': bool(_a.isSeparator()),
                              'checkable': bool(_a.isCheckable()),
                              'checked': bool(_a.isChecked())})
            _d = {
                'pid': os.getpid(),
                'tray_visible': bool(_tj is not None and _tj.isVisible()),
                'icon_null': (bool(_tj.icon().isNull())
                              if _tj is not None else None),
                'tooltip': (_tj.toolTip() if _tj is not None else ''),
                'actions': _acts,
                'quit_on_last_window_closed':
                    bool(QApplication.instance().quitOnLastWindowClosed()),
                'pause': bool(_st.get('pause')),
                'mute': bool(_st.get('mute')),
                'window_visible': bool(w.isVisible()),
                'timers_active': {
                    _k: bool(_v.isActive())
                    for _k, _v in (getattr(w, '_poll_timers', {}) or {}).items()},
            }
            with open(_tp, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(10, _do_trayinfo)

# 原分支 11：len(parts) == 2 and parts[0] == "cadstat"
def _h_11_cadstat(ctx, msg, parts):
    w, _CAD_KEYS, _CAD_MEAS, _err = ctx.w, ctx._CAD_KEYS, ctx._CAD_MEAS, ctx._err
    _cp = parts[1]

    def _do_cadstat():
        try:
            _now = time.perf_counter()
            _tm3 = getattr(w, '_poll_timers', {}) or {}
            _d = {
                'pid': os.getpid(),
                'now': round(_now, 6),
                'window_visible': bool(w.isVisible()),
                'effective_ms': {_k: int(_v.interval())
                                 for _k, _v in _tm3.items()},
                'active': {_k: bool(_v.isActive())
                           for _k, _v in _tm3.items()},
                'metrics_rows': len(getattr(w, '_metrics_buf', []) or []),
                'metrics_last_ts': round(float(
                    (getattr(w, '_metrics_last_ts', [0.0]) or [0.0])[0]), 3),
                'hits': {_k: [round(_x, 6)
                              for _x in (_CAD_MEAS.get(_k) or [])]
                         for _k in _CAD_KEYS},
            }
            with open(_cp, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()

    QTimer.singleShot(10, _do_cadstat)

# 原分支 12：len(parts) == 2 and parts[0] == "alertstat"
def _h_12_alertstat(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _ap = parts[1]

    def _do_alertstat():
        try:
            _st = dict(getattr(w, '_alert_state', {}) or {})
            _st.pop('_t0', None)
            _st['now'] = time.time()
            _st['window_visible'] = bool(w.isVisible())
            _tm4 = getattr(w, '_alert_timer', None)
            _st['timer_active'] = bool(_tm4.isActive()) if _tm4 is not None else None
            _st['timer_interval'] = int(_tm4.interval()) if _tm4 is not None else None
            _st['effective_ms'] = {_k: int(_v.interval()) for _k, _v
                                   in (getattr(w, '_poll_timers', {}) or {}).items()}
            with open(_ap, 'w', encoding='utf-8') as _f:
                json.dump(_st, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(10, _do_alertstat)

# 原分支 13：len(parts) == 2 and parts[0] == "chartna"
def _h_13_chartna(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _np = parts[1]

    def _do_chartna():
        try:
            _fn = getattr(w, '_chart_na_probe', None)
            if _fn is None:
                return
            with open(_np, 'w', encoding='utf-8') as _f:
                json.dump(_fn(), _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(10, _do_chartna)

# 原分支 14：len(parts) == 2 and parts[0] == "jobstbl"
def _h_14_jobstbl(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _jp = parts[1]

    def _do_jobstbl():
        try:
            _tb = getattr(w, '_jobs_tbl', None)
            if _tb is None:
                return
            _n = _tb.rowCount()
            _c = _tb.columnCount()
            _lines = ['\t'.join(
                str(_tb.horizontalHeaderItem(i).text()
                    if _tb.horizontalHeaderItem(i) else i)
                for i in range(_c))]
            for _r in range(_n):
                _cells = []
                for _cc in range(_c):
                    _it = _tb.item(_r, _cc)
                    _cells.append('' if _it is None else _it.text())
                _lines.append('\t'.join(_cells))
            with open(_jp, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(_lines) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(10, _do_jobstbl)

# 原分支 15：len(parts) == 2 and parts[0] == "traymenu"
def _h_15_traymenu(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _tp = parts[1]

    def _do_traymenu():
        try:
            import json as _json3
            _tm = getattr(w, '_tray_menu', None)
            _acts = [a.text() for a in (_tm.actions() if _tm is not None else [])]
            if _tm is not None:
                _tm.popup(w.mapToGlobal(w.rect().center()))

            def _snap():
                try:
                    _tm.grab().save(_tp + '.png')
                except Exception:
                    _err()
                try:
                    with open(_tp + '.json', 'w', encoding='utf-8') as _f:
                        _json3.dump({
                            'actions': _acts,
                            'menu_visible': bool(_tm.isVisible()),
                            'menu_geometry': [_tm.x(), _tm.y(),
                                              _tm.width(), _tm.height()],
                            'tray_visible': bool(getattr(w, '_tray', None)
                                                 and w._tray.isVisible()),
                        }, _f, ensure_ascii=False)
                except Exception:
                    _err()
                try:
                    _tm.hide()
                except Exception:
                    pass

            QTimer.singleShot(400, _snap)
        except Exception:
            _err()

    QTimer.singleShot(20, _do_traymenu)

# 原分支 16：len(parts) == 2 and parts[0] == "wscan"
def _h_16_wscan(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _wp = parts[1]

    def _do_wscan():
        import json as _json2
        _rows = []
        try:
            for _c in w.findChildren(QWidget):
                _nm = type(_c).__name__
                if not _nm.startswith(('QPushButton', 'QComboBox', 'QSpinBox',
                                       'QDoubleSpinBox', 'QCheckBox', 'QRadioButton',
                                       'QSlider', 'QLineEdit', 'QPlainTextEdit',
                                       'QTableWidget')):
                    continue
                if not _c.isVisible():
                    continue
                _val = ''
                try:
                    if _nm.startswith(('QComboBox', 'QLineEdit')):
                        _val = str(_c.text() if _nm.startswith('QLineEdit') else _c.currentText())
                    elif _nm.startswith(('QSpinBox', 'QDoubleSpinBox', 'QSlider')):
                        _val = str(_c.value())
                    elif _nm.startswith(('QCheckBox', 'QRadioButton')):
                        _val = str(bool(_c.isChecked()))
                except Exception:
                    _val = '?'
                _txt = ''
                try:
                    _txt = str(_c.text()) if _nm.startswith('QPushButton') else ''
                except Exception:
                    _txt = ''
                _rows.append({'cls': _nm, 'name': _c.objectName(),
                              'text': _txt[:60], 'val': _val[:60],
                              'enabled': bool(_c.isEnabled()),
                              'parent': _c.parentWidget().objectName()
                              if _c.parentWidget() is not None else ''})
            _stk = getattr(w, '_stack', None) or w.findChild(QStackedWidget)
            with open(_wp, 'w', encoding='utf-8') as _f:
                _json2.dump({'page': int(_stk.currentIndex()) if _stk is not None else -1,
                             'n': len(_rows), 'rows': _rows},
                            _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_wscan)

# 原分支 17：len(parts) == 2 and parts[0] == "sztest"
def _h_17_sztest(ctx, msg, parts):
    w = ctx.w
    _fp = getattr(w, '_sztest_fn', None)
    if _fp is not None:
        QTimer.singleShot(0, lambda: _fp(parts[1]))

# 原分支 18：len(parts) == 2 and parts[0] == "snap"
def _h_18_snap(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _p = parts[1]
    def _do_snap():
        def _grab():
            try:
                w.grab().save(_p)
                # R020 审计辅助：输出各宫格的实际尺寸，定位"单列/空隙"根因
                _info = []
                for _lw in w.findChildren(QListWidget):
                    if _lw.viewMode() != QListWidget.IconMode:
                        continue
                    _sz = _lw.item(0).sizeHint() if _lw.count() else QSize()
                    # R09（XH-064）：统计封面状态，区分「有图 / 缺失 / 拉取中」
                    _st = {'ok': 0, 'missing': 0, 'pending': 0,
                           'oversize': 0}
                    _uniform = 1
                    for _i in range(_lw.count()):
                        _v = _lw.item(_i).data(Qt.UserRole + 3)
                        _st[_v if _v in _st else 'pending'] += 1
                        if _lw.item(_i).sizeHint() != _sz:
                            _uniform = 0
                    _bad = [str(_lw.item(_i).data(Qt.UserRole))
                            for _i in range(_lw.count())
                            if _lw.item(_i).data(Qt.UserRole + 3) != 'ok'][:6]
                    # R13（P0-2）几何自证：可视高能排下的整行数、末行右下沿、
                    # 余量是否小于一行（无半行）、滚动条是否真的不可见
                    _vw, _vh = _lw.viewport().width(), _lw.viewport().height()
                    _g = _lw.gridSize().width()
                    _rows = int(getattr(_lw, '_rows', 0)) or (
                        max(1, int(max(60, _vh - 2) // max(1, _g))))
                    _used = _rows * _g
                    _rem = _vh - _used
                    # 余量 ≤ 行距(4px) 视为"整行可整除"（无半行）
                    _div = 1 if 0 <= _rem <= 4 else 0
                    _sbv = int(_lw.verticalScrollBar().isVisible())
                    _sbh = int(_lw.horizontalScrollBar().isVisible())
                    _info.append('vw=%d vh=%d icon=%d grid=%dx%d hint=%d,%d count=%d'
                                 ' rows=%d used=%d rem=%d div=%d sb=%d,%d'
                                 ' maxh=%d uniform=%d'
                                 ' thumbs=ok%d/miss%d/pend%d/over%d bad=%s'
                                 % (_vw, _vh, _lw.iconSize().width(),
                                    _g, _lw.gridSize().height(),
                                    _sz.width(), _sz.height(), _lw.count(),
                                    _rows, _used, _rem, _div, _sbv, _sbh,
                                    _lw.maximumHeight(), _uniform,
                                    _st['ok'], _st['missing'], _st['pending'],
                                    _st['oversize'], ','.join(_bad)))
                with open(_p + '.txt', 'w', encoding='utf-8') as _f:
                    _f.write('\n'.join(_info))
            except Exception:
                _err()
        QTimer.singleShot(500, _grab)
    QTimer.singleShot(0, _do_snap)

# 原分支 19：len(parts) == 2 and parts[0] == "deltest"
def _h_19_deltest(ctx, msg, parts):
    w, _err, _t, api = ctx.w, ctx._err, ctx._t, ctx.api
    _p = parts[1]

    def _do_deltest():
        import json as _json
        import time as _t
        import zlib as _zlib
        out = []
        res = {}

        pr = Probe(w, _err, _p, out, tick=0.01, pause=200)


        def _mk_png(path, tag=None):
            wpx, hpx = 16, 16
            raw = b''.join(b'\x00' + bytes([36, 58, 92]) * wpx for _ in range(hpx))

            def _ch(t, d):
                return (len(d).to_bytes(4, 'big') + t + d
                        + (_zlib.crc32(t + d) & 0xffffffff).to_bytes(4, 'big'))
            png = (b'\x89PNG\r\n\x1a\n'
                   + _ch(b'IHDR', wpx.to_bytes(4, 'big') + hpx.to_bytes(4, 'big')
                         + bytes([8, 2, 0, 0, 0]))
                   + ((_ch(b'tEXt', b'prompt\x00' + ('{"r10":"%s"}' % tag).encode())
                       if tag else b''))
                   + _ch(b'IDAT', _zlib.compress(raw))
                   + _ch(b'IEND', b''))
            with open(path, 'wb') as _f:
                _f.write(png)

        _root = _pathcfg.comfy_out()
        _par = _pathcfg.comfy_store()
        if not _root or not _par:
            out.append('SKIP 未配置 ComfyUI 输出目录（paths.json / XH_COMFY_STORE）')
            return
        _R = w._r10_del
        _refresh = lambda: (_R['refresh'][0](), _R['refresh'][1]())
        _names = _R['names']
        a = os.path.join(_root, 'photo', '_r10_del_a.png')
        b = os.path.join(_root, 'photo', '_r10_del_b.png')
        c = os.path.join(_root, 'upscale', '_r10_del_c.png')
        dec = os.path.join(_root, '_r10_not_program.png')
        sent = os.path.join(_par, '_r10_sentinel.png')
        try:
            w.showNormal(); w.activateWindow(); w._nav_select(2)
            pr.wait(600)
            for _f, _tag in ((a, 'a'), (b, 'b'), (c, 'c'), (sent, 'sentinel')):
                try:
                    os.makedirs(os.path.dirname(_f), exist_ok=True)
                    if os.path.exists(_f):
                        os.remove(_f)
                    _mk_png(_f, _tag)
                except Exception as _e:
                    out.append('mk %s EXC %r' % (_f, _e))
            # 诱饵：output 内但没有程序元数据的 PNG（模拟用户自己拷进来的图片）
            try:
                if os.path.exists(dec):
                    os.remove(dec)
                _mk_png(dec, None)
            except Exception as _e:
                out.append('mk decoy EXC %r' % (_e,))

            _refresh()
            pr.wait(2400)
            out.append('before gen_wall=%d up_wall=%d'
                       % (_R['outs'].count(), _R['uwall'].count()))
            pr.snap('01_before')

            _p_19_deltest_single(out, res, a, _R, _names, _refresh, pr.wait, pr.snap)

            _p_19_deltest_batch(out, res, b, c, _R, _names, api, _refresh, pr.wait, pr.snap)

            _p_19_deltest_guard(out, res, api, sent, dec)
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        finally:
            # 测试文件收尾（sentinel 在 output 之外，用文件系统删除，非删除接口）
            for _f in (a, b, c, dec, sent):
                try:
                    if os.path.exists(_f):
                        os.remove(_f)
                except Exception:
                    _err()
        try:
            with open(_p + '.json', 'w', encoding='utf-8') as _f:
                _json.dump(res, _f, ensure_ascii=False, indent=2)
        except Exception:
            _err()
        try:
            with open(_p + '.txt', 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_deltest)

def _p_19_deltest_single(out, res, a, _R, _names, _refresh, _wait, _snap):
    # 阶段1：单张删除 —— 真实确认对话框 → 点「确认删除」
    _R['ask'](['photo/_r10_del_a.png'], _refresh)
    _wait(700)
    _snap('02_confirm_single', full=True)
    try:
        _R['state']['box'].button(QMessageBox.Yes).click()
    except Exception as _e:
        out.append('confirm single EXC %r' % (_e,))
    _wait(2600)
    res['single_file_exists'] = os.path.exists(a)
    res['single_on_wall'] = 'photo/_r10_del_a.png' in _names(_R['outs'])
    out.append('single exists=%s on_wall=%s'
               % (res['single_file_exists'], res['single_on_wall']))
    _snap('03_after_single')

def _p_19_deltest_batch(out, res, b, c, _R, _names, api, _refresh, _wait, _snap):
    # 阶段2：批量清空（只清测试文件 b/c，不碰用户作品）
    # R11（XH-073）：批量门槛升级为「输入文件数」，先把门槛本身测一遍
    _R['ask'](['photo/_r10_del_b.png', 'upscale/_r10_del_c.png'], _refresh)
    _wait(700)
    _snap('04_confirm_clear', full=True)
    _gate = _R['state']
    try:
        res['gate_open'] = bool(_gate.get('dlg') and _gate.get('edit'))
        res['gate_default_disabled'] = not _gate['okb'].isEnabled()
        _gate['edit'].setText('1')
        _wait(300)
        res['gate_wrong_disabled'] = not _gate['okb'].isEnabled()
        _gate['edit'].setText('2')
        _wait(300)
        res['gate_exact_enabled'] = _gate['okb'].isEnabled()
        _snap('04b_gate_typed', full=True)
        out.append('gate default=%s wrong=%s exact=%s'
                   % (res['gate_default_disabled'],
                      res['gate_wrong_disabled'], res['gate_exact_enabled']))
        _gate['okb'].click()
    except Exception as _e:
        out.append('confirm clear EXC %r' % (_e,))
    _wait(2600)
    res['batch_files_exist'] = [os.path.exists(b), os.path.exists(c)]
    res['batch_on_wall'] = ['photo/_r10_del_b.png' in _names(_R['outs']),
                            'upscale/_r10_del_c.png' in _names(_R['uwall'])]
    out.append('batch exists=%s on_wall=%s'
               % (res['batch_files_exist'], res['batch_on_wall']))
    _snap('05_after_clear')
    # 再独立拉一次接口：刷新后不复活
    try:
        _code, _d = api('GET', '/api/outputs?limit=80')
        _all = ([str(o.get('name')) for o in (_d.get('outputs') or [])]
                if isinstance(_d, dict) else [])
        res['refresh_absent'] = [
            n not in _all for n in
            ('photo/_r10_del_a.png', 'photo/_r10_del_b.png',
             'upscale/_r10_del_c.png')]
        out.append('refresh_absent=%s' % (res['refresh_absent'],))
    except Exception as _e:
        out.append('refresh EXC %r' % (_e,))

def _p_19_deltest_guard(out, res, api, sent, dec):
    # 阶段3：防护 —— 越界路径 / 非程序产出都必须被拒
    try:
        _cg, _dg = api('POST', '/api/outputs/delete',
                       {'names': ['../_r10_sentinel.png',
                                  '/etc/passwd',
                                  '_r10_not_program.png']})
        res['guard'] = _dg if isinstance(_dg, dict) else str(_dg)
        res['guard_blocked'] = bool(
            isinstance(_dg, dict) and not _dg.get('ok')
            and len(_dg.get('failed') or []) == 3
            and not (_dg.get('deleted') or []))
        res['sentinel_exists'] = os.path.exists(sent)
        res['decoy_exists'] = os.path.exists(dec)
        out.append('guard blocked=%s sentinel=%s decoy=%s'
                   % (res['guard_blocked'], res['sentinel_exists'],
                      res['decoy_exists']))
    except Exception as _e:
        out.append('guard EXC %r' % (_e,))

# 原分支 20：len(parts) == 2 and parts[0] == "fitstat"
def _h_20_fitstat(ctx, msg, parts):
    w, FIT_STAT, _err = ctx.w, ctx.FIT_STAT, ctx._err
    _p = parts[1]

    def _do_fitstat():
        import json as _json
        try:
            _d = dict(FIT_STAT)
            try:
                _gl = [x for x in w.findChildren(QListWidget) if getattr(x, '_xname', '') == 'gen']
                if _gl:
                    _d['out_count'] = _gl[0].count()
                    _d['icon'] = _gl[0].iconSize().width()
                else:
                    _d['out_count'] = -1
                    _d['icon'] = -1
            except Exception:
                _err()
            _d['win_client'] = [w.width(), w.height()]
            with open(_p, 'w', encoding='utf-8') as _f:
                _json.dump(_d, _f, ensure_ascii=False, indent=1)
            for _k in list(FIT_STAT.keys()):
                FIT_STAT[_k] = 0
        except Exception:
            _err()

    QTimer.singleShot(0, _do_fitstat)

# 原分支 21：len(parts) == 2 and parts[0] == "errtest"
def _h_21_errtest(ctx, msg, parts):
    w, _ERR_LOGPATH, _err, _errs_dump = ctx.w, ctx._ERR_LOGPATH, ctx._err, ctx._errs_dump
    _errs_load_whitelist = ctx._errs_load_whitelist
    _en = int(parts[1] or 0)

    def _do_errtest():
        for _i in range(_en):
            try:
                raise ValueError('synthetic %d' % _i)
            except ValueError:
                _err()
        _errs_load_whitelist()
        _d = _errs_dump()
        with open(_ERR_LOGPATH + '.test.json', 'w', encoding='utf-8') as _f:
            json.dump(_d, _f, ensure_ascii=False, indent=1)

    QTimer.singleShot(0, _do_errtest)

# 原分支 22：len(parts) == 2 and parts[0] == "errs"
def _h_22_errs(ctx, msg, parts):
    w, _err, _errs_dump, _errs_load_whitelist = ctx.w, ctx._err, ctx._errs_dump, ctx._errs_load_whitelist
    _ep = parts[1]

    def _do_errs():
        try:
            _errs_load_whitelist()
            _d = _errs_dump()
            with open(_ep, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_errs)

# 原分支 23：len(parts) == 2 and parts[0] == "dump"
def _h_23_dump(ctx, msg, parts):
    w, _DECO_TM, _PCOST, _deco_diag = ctx.w, ctx._DECO_TM, ctx._PCOST, ctx._deco_diag
    _err = ctx._err
    _dp = parts[1]

    def _do_dump():
        try:
            _d = _deco_diag()
            _d['timers'] = [{'iv': int(_x.interval()),
                             'on': bool(_x.isActive()),
                             'name': _x.objectName() or '?'}
                            for _x in _DECO_TM]
            _d['dpr'] = round(float(w.devicePixelRatioF()), 4)
            _d['logical'] = [int(w.width()), int(w.height())]
            if _PCOST:
                _d['pcost'] = {_k: [int(_v[0]), round(_v[1], 1)]
                               for _k, _v in _PCOST.items()}
            with open(_dp, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_dump)

# 原分支 24：len(parts) >= 2 and parts[0] == "节拍"
def _h_24_x(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _cp = parts[1]
    _cs = float(parts[2]) if len(parts) >= 3 else 180.0

    def _do_cad():
        _fp = getattr(w, '_cadence_probe', None)
        if _fp is None:
            return
        try:
            _fp(_cp, _cs)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_cad)

# 原分支 25：len(parts) == 3 and parts[0] == "setpoll"
def _h_25_setpoll(ctx, msg, parts):
    w, XH_ADV, XH_CADENCE, _err = ctx.w, XH_ADV, XH_CADENCE, ctx._err
    _sp_n = int(parts[1]); _sp_p = parts[2]

    def _do_setpoll():
        out = {'req_s': _sp_n}
        try:
            _ens = getattr(w, '_ensure_adv_panel', None)
            if _ens is not None:
                _ens()      # R253：惰性模式下先建真面板，保证与用户路径同一条 on_apply
            _pn = getattr(w, '_adv_panel', None)
            _vals = _pn.collect() if _pn is not None else {}
            _vals['pollInterval'] = _sp_n
            _fa = getattr(w, '_xh_apply_adv', None)
            if _fa is not None:
                _fa(_vals, silent=True)
            _t1 = getattr(w, '_poll_t1', None)
            out['t1_interval_ms'] = int(_t1.interval()) if _t1 is not None else None
            out['cadence_t1_ms'] = XH_CADENCE.get('t1')
            out['pollInterval'] = XH_ADV.get('pollInterval')
        except Exception:
            _err()
            out['err'] = 'apply failed'
        try:
            with open(_sp_p, 'w', encoding='utf-8') as _f:
                json.dump(out, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_setpoll)

# 原分支 26：len(parts) == 2 and parts[0] == "paintwho"
def _h_26_paintwho(ctx, msg, parts):
    w, AnimatedNumberLabel, FlowProgressBar, _OPAQUE_BASE = ctx.w, ctx.AnimatedNumberLabel, ctx.FlowProgressBar, ctx._OPAQUE_BASE
    _SETTEXT_N, _err = ctx._SETTEXT_N, ctx._err
    _p = parts[1]

    def _do_paintwho():
        import json as _json
        try:
            out = {'dot_chain': [], 'effects': [], 'win': [w.width(), w.height()]}
            # R20-实验A：顶层窗口的「原生是否分层（WS_EX_LAYERED）」——
            # WA_TranslucentBackground 在 Windows 上会打开分层窗口，
            # 分层意味着每次脏区都要由 DWM 与桌面重新合成。
            try:
                _hw = int(w.winId())
                _ex = ctypes.windll.user32.GetWindowLongW(_hw, -20)
                out['win_exstyle'] = {'ex': _ex, 'layered': bool(_ex & 0x80000)}
            except Exception as _e:
                out['win_exstyle'] = {'err': str(_e)}
            try:
                out['win_attrs'] = {
                    'translucent': bool(w.testAttribute(Qt.WA_TranslucentBackground)),
                    'opaque': bool(w.testAttribute(Qt.WA_OpaquePaintEvent)),
                    'nosysbg': bool(w.testAttribute(Qt.WA_NoSystemBackground))}
            except Exception:
                _err()
            _dot = None
            for _w in w.findChildren(QWidget):
                if type(_w).__name__ == 'BreathDot':
                    _dot = _w
                    break
            _attrs = ((Qt.WA_OpaquePaintEvent, 'opaque'),
                      (Qt.WA_TranslucentBackground, 'translucent'),
                      (Qt.WA_NoSystemBackground, 'nosysbg'),
                      (Qt.WA_StyledBackground, 'styledbg'),
                      (Qt.WA_StaticContents, 'static'))

            def _desc(x):
                d = {'cls': type(x).__name__, 'name': x.objectName(),
                     'geo': [x.x(), x.y(), x.width(), x.height()],
                     'visible': bool(x.isVisible()),
                     'autofill': int(bool(x.autoFillBackground()))}
                try:
                    _e = x.graphicsEffect()
                    d['effect'] = type(_e).__name__ if _e else ''
                except Exception:
                    d['effect'] = '?'
                for _a, _nm in _attrs:
                    try:
                        if x.testAttribute(_a):
                            d[_nm] = 1
                    except Exception:
                        _err()
                return d

            # ── R20-1（通行证）：四级控件在两个标记上的「读回值」，
            #    并对比「重设样式表 / 切页 / 切回」之后是否被改写。
            def _one(wd):
                if wd is None:
                    return None
                try:
                    return {'cls': type(wd).__name__, 'name': wd.objectName(),
                            'opaque': int(bool(wd.testAttribute(Qt.WA_OpaquePaintEvent))),
                            'nosysbg': int(bool(wd.testAttribute(Qt.WA_NoSystemBackground))),
                            'static': int(bool(wd.testAttribute(Qt.WA_StaticContents))),
                            'autofill': int(bool(wd.autoFillBackground())),
                            'geo': [wd.width(), wd.height()],
                            'vis': int(bool(wd.isVisible()))}
                except Exception as e:
                    return {'err': str(e)}

            def _snap_all():
                try:
                    _stack = w.findChild(QStackedWidget)
                except Exception:
                    _stack = None
                _sub = None
                try:
                    for _l in w.findChildren(QLabel):
                        if _l.objectName() == 'sub' and _l.isVisible():
                            _sub = _l
                            break
                except Exception:
                    _err()
                return {'flow': _one(w.findChild(FlowProgressBar)),
                        'num': _one(w.findChild(AnimatedNumberLabel)),
                        'sub': _one(_sub),
                        'page': _one(_stack.currentWidget() if _stack else None),
                        'stack': _one(_stack),
                        'root': _one(w.findChild(QWidget, 'root'))}

            _p_26_paintwho_attrprobe(out, w, _snap_all)
            try:
                out['settext_num'] = _SETTEXT_N[0]      # 3c：setText 计数
                out['opaque_base'] = len(_OPAQUE_BASE)
            except Exception:
                _err()
            _p_26_paintwho_chain(out, w, _dot, _desc, _err)
            with open(_p, 'w', encoding='utf-8') as _f:
                _json.dump(out, _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_paintwho)

def _p_26_paintwho_attrprobe(out, w, _snap_all):
    # 阶段：四级控件属性读回值，并对比「重设样式表 / 切页 / 切回」之后是否被改写
    _ap = {'before': _snap_all()}
    if os.environ.get('XH_ATTRPROBE', '') == '1':
        try:
            w.setStyleSheet(w.styleSheet() + '\n')
            QApplication.processEvents()
            _ap['after_stylesheet'] = _snap_all()
        except Exception as e:
            _ap['after_stylesheet'] = {'err': str(e)}
        try:
            w._nav_select(1)
            QApplication.processEvents()
            _ap['after_nav1'] = _snap_all()
            w._nav_select(0)
            QApplication.processEvents()
            _ap['after_nav0'] = _snap_all()
        except Exception as e:
            _ap['after_nav0'] = {'err': str(e)}
    out['attr_probe'] = _ap

def _p_26_paintwho_chain(out, w, _dot, _desc, _err):
    # 阶段：BreathDot 的父链逐级属性，以及全窗 graphicsEffect 采集
    x = _dot
    while x is not None:
        out['dot_chain'].append(_desc(x))
        x = x.parentWidget()
    for _w in w.findChildren(QWidget):
        try:
            _e = _w.graphicsEffect()
            if _e is not None:
                out['effects'].append({
                    'cls': type(_w).__name__, 'name': _w.objectName(),
                    'geo': [_w.width(), _w.height()],
                    'effect': type(_e).__name__})
        except Exception:
            _err()

# 原分支 27：len(parts) == 2 and parts[0] == "adv"
def _h_27_adv(ctx, msg, parts):
    w, _err, ux = ctx.w, ctx._err, ctx.ux
    _p = parts[1]

    def _do_adv():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(5)
            _qt = w.findChild(QTabWidget, 'cfgTop')
            if _qt is not None:
                _qt.setCurrentIndex(3)
        except Exception:
            _err()

        def _dump():
            try:
                pn = w.findChild(ux.AdvPanel)
                ks = sorted(pn.controls.keys()) if pn is not None else []
                with open(_p, 'w', encoding='utf-8') as _f:
                    _f.write('controls=%d\n' % len(ks))
                    for _k in ks:
                        _f.write('%s\t%s\n' % (_k, pn.controls[_k][1]))
                    _f.write('groups=%d\n' % (len(pn._groups) if pn is not None else 0))
                    _f.write('values=%r\n' % (pn.collect() if pn is not None else {}))
                if pn is not None:
                    for _i, (_h, _b) in enumerate(pn._groups):
                        _h.setChecked(True)
                        QApplication.processEvents()
                        w.grab().save('%s_%d.png' % (_p, _i))
            except Exception as _e:
                try:
                    with open(_p + '.err', 'w', encoding='utf-8') as _f:
                        _f.write(repr(_e))
                except Exception:
                    _err()

        QTimer.singleShot(1200, _dump)

    QTimer.singleShot(0, _do_adv)

# 原分支 28：len(parts) == 2 and parts[0] == "kvinfo"
def _h_28_kvinfo(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _p = parts[1]

    def _do_kv():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(1)
        except Exception:
            _err()

        def _dump():
            out = []
            try:
                out.append('win=%dx%d' % (w.width(), w.height()))
                # 状态页同行卡片几何（R07 对齐核查）
                for _nm, _wd in (w._cards_geo or {}).items():
                    if isinstance(_wd, list):
                        for _i, _cc in enumerate(_wd):
                            out.append('STAT%d geo=%d,%d,%dx%d' % (
                                _i, _cc.x(), _cc.y(), _cc.width(), _cc.height()))
                    elif hasattr(_wd, 'geometry'):
                        out.append('CARD_%s geo=%d,%d,%dx%d' % (
                            _nm, _wd.x(), _wd.y(), _wd.width(), _wd.height()))
                _cards = w.findChildren(QFrame, 'kvcard')
                out.append('cards=%d' % len(_cards))
                for _card in _cards:
                    _pid = getattr(_card, 'pid', '?')
                    out.append('CARD %s geo=0,0,%dx%d hint=%dx%d min=%dx%d' % (
                        _pid, _card.width(), _card.height(),
                        _card.sizeHint().width(), _card.sizeHint().height(),
                        _card.minimumSizeHint().width(),
                        _card.minimumSizeHint().height()))
                    _gl = None
                    for _i in range(_card.layout().count()):
                        _lay = _card.layout().itemAt(_i).layout()
                        if isinstance(_lay, QGridLayout):
                            _gl = _lay
                    if _gl is not None:
                        out.append('  GRID rows=%d cols=%d vs=%d hs=%d' % (
                            _gl.rowCount(), _gl.columnCount(),
                            _gl.verticalSpacing(), _gl.horizontalSpacing()))
                        for _r in range(_gl.rowCount()):
                            _crr = _gl.cellRect(_r, 0)
                            out.append('    row%d y=%d h=%d' % (
                                _r, _crr.y(), _crr.height()))
                        for _c in range(_gl.columnCount()):
                            _crr = _gl.cellRect(0, _c)
                            out.append('    col%d x=%d w=%d' % (
                                _c, _crr.x(), _crr.width()))
                    for _wd in _card.findChildren(QWidget):
                        _pp = _wd.mapTo(_card, QPoint(0, 0))
                        _txt = ''
                        try:
                            _txt = _wd.text()
                        except Exception:
                            _err()
                        out.append('  %-12s %-16r pos=%d,%d size=%dx%d vis=%s' % (
                            _wd.__class__.__name__, _txt[:16],
                            _pp.x(), _pp.y(), _wd.width(), _wd.height(),
                            _wd.isVisible()))
            except Exception as _e:
                out.append('ERR %r' % (_e,))
            try:
                with open(_p, 'w', encoding='utf-8') as _f:
                    _f.write('\n'.join(out) + '\n')
            except Exception:
                _err()
            try:
                w.grab().save(_p + '.png')
            except Exception:
                _err()

        QTimer.singleShot(1200, _dump)

    QTimer.singleShot(0, _do_kv)

# 原分支 29：len(parts) == 2 and parts[0] == "walk"
def _h_29_walk(ctx, msg, parts):
    w, _err, _t = ctx.w, ctx._err, ctx._t
    _p = parts[1]

    def _do_walk():
        out = []

        pr = Probe(w, _err, _p, tick=0.03, pause=420)


        try:
            pb = getattr(w, '_probe3', {}) or {}
            w.showNormal(); w.activateWindow()
            # 1) 生图页：空提示词点「生成」
            _keep = pb['prompt'].toPlainText()
            w._nav_select(2)
            pr.wait(420)
            # ComfyUI 离线时按钮被禁用（真实门控），走查强制启用只是为了验证空值守卫，
            # 守卫命中即 return，不会向 ComfyUI 发请求。
            _en0 = pb['gen_btn'].isEnabled()
            pb['gen_btn'].setEnabled(True)
            pb['gen_lbl'].setText('')
            pb['prompt'].setPlainText('')
            pb['gen_btn'].click()
            pr.snap('gen_empty')
            out.append('gen_empty=%r btn_enabled(原)=%s w=%d h=%d'
                       % (pb['gen_lbl'].text(), _en0,
                          pb['gen_w'].value(), pb['gen_h'].value()))
            pb['gen_btn'].setEnabled(_en0)
            # 边界：宽高拉到上下限
            pb['gen_w'].setValue(-5); pb['gen_h'].setValue(99999)
            out.append('gen_bounds=w%d h%d' % (pb['gen_w'].value(), pb['gen_h'].value()))
            pb['gen_w'].setValue(1216); pb['gen_h'].setValue(832)
            pb['prompt'].setPlainText(_keep)
            # 2) 视频页：空提示词点「生成视频」
            _keepv = pb['vprompt'].toPlainText()
            w._nav_select(3)
            pr.wait(420)
            _env = pb['vgen_btn'].isEnabled()
            pb['vgen_btn'].setEnabled(True)
            pb['vgen_lbl'].setText('')
            pb['vprompt'].setPlainText('')
            pb['vgen_btn'].click()
            pr.snap('vid_empty')
            out.append('vid_empty=%r btn_enabled(原)=%s'
                       % (pb['vgen_lbl'].text(), _env))
            # 2b) 视频页：有提示词、首帧路径不存在
            pb['vprompt'].setPlainText('走查用')
            pb['vfirst'].setText(r'Z:\__no_such_dir__\nope.png')
            pb['vgen_lbl'].setText('')
            pb['vgen_btn'].click()
            pr.snap('vid_badfirst')
            out.append('vid_badfirst=%r' % pb['vgen_lbl'].text())
            pb['vgen_btn'].setEnabled(_env)
            pb['vfirst'].setText(''); pb['vprompt'].setPlainText(_keepv);
            pb['vgen_lbl'].setText('')
            # 3) 超分页：无输入点「放大」
            _kid = pb['up_kind'].currentIndex()
            w._nav_select(4)
            pr.wait(420)
            _enu = pb['up_btn'].isEnabled()
            pb['up_btn'].setEnabled(True)
            pb['up_lbl'].setText('')
            pb['up_kind'].setCurrentIndex(0)
            pb['up_img'].clear()
            pb['up_btn'].click()
            pr.snap('up_noinput')
            out.append('up_noinput=%r btn_enabled(原)=%s'
                       % (pb['up_lbl'].text(), _enu))
            pb['up_btn'].setEnabled(_enu)
            pb['up_kind'].setCurrentIndex(_kid)
            try:
                if callable(pb.get('poll_up')):
                    pb['poll_up']()
            except Exception:
                _err()
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_walk)

# 原分支 30：len(parts) == 2 and parts[0] == "real"
def _h_30_real(ctx, msg, parts):
    w, _err, _t, api = ctx.w, ctx._err, ctx._t, ctx.api
    _p = parts[1]

    def _do_real():
        out = []
        pb = getattr(w, '_probe3', {}) or {}

        pr = Probe(w, _err, _p, out, tick=0.03, pause=300)


        def _until(fn, timeout=900, gap=0.3):
            import time as _t
            _end = _t.time() + timeout
            while _t.time() < _end:
                QApplication.processEvents()
                if fn():
                    return True
                _t.sleep(gap)
            return False

        def _post_bg(path):
            def _r():
                try:
                    _s, _d = api('POST', path, {}, timeout=180)
                    out.append('POST %s -> %s %r' % (path, _s, _d))
                except Exception as _e:
                    out.append('POST %s EXC %r' % (path, _e))
            threading.Thread(target=_r, daemon=True).start()

        def _cf_on():
            try:
                _s, _d = api('GET', '/api/state', timeout=6)
                _c = (_d.get('comfy') or {}) if isinstance(_d, dict) else {}
                return bool(_c.get('alive') or _c.get('ready'))
            except Exception:
                return False

        def _cf_wait(timeout=180):
            return _until(_cf_on, timeout=timeout, gap=1.0)

        try:
            w.showNormal(); w.activateWindow()
            out.append('start cf_online=%s' % _cf_on())
            if not _cf_on():
                _post_bg('/api/comfy/start')
                _cf_wait(180)
                out.append('cf_start_done online=%s' % _cf_on())

            # ① 真生图 768×768
            w._nav_select(2)
            pr.wait(600)
            pb['gen_lbl'].setText('')
            pb['gen_w'].setValue(768); pb['gen_h'].setValue(768)
            try:
                pb['gen_steps'].setValue(8)
            except Exception:
                _err()
            pb['prompt'].setPlainText(
                'a serene mountain lake at sunrise, photorealistic, detailed')
            out.append('gen_model=%r w=%d h=%d steps=%s'
                       % (pb['gen_model'].currentText(), pb['gen_w'].value(),
                          pb['gen_h'].value(),
                          pb['gen_steps'].value() if pb.get('gen_steps') else '?'))
            pb['gen_btn'].setEnabled(True)
            pb['gen_btn'].click()
            pr.wait(1500)
            pr.snap('gen_running')
            out.append('gen_running_lbl=%r' % pb['gen_lbl'].text())
            _ok = _until(lambda: pb['gen_lbl'].text().startswith(('完成', '失败')),
                         timeout=420, gap=0.5)
            out.append('gen_done_seen=%s lbl=%r' % (_ok, pb['gen_lbl'].text()))
            pr.snap('gen_done')

            _p_30_real_gen_fail(out, pb, _post_bg, pr.wait, _until, pr.snap, _cf_wait)

            _p_30_real_upscale(out, w, pb, pr.wait, pr.snap, _until)

            _p_30_real_offline(out, w, pb, pr.wait, pr.snap, _until, _cf_on, _post_bg)

            # ⑥ 恢复
            _post_bg('/api/comfy/start')
            out.append('cf_final_restore online=%s' % _cf_wait(180))
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_real)

def _p_30_real_gen_fail(out, pb, _post_bg, _wait, _until, _snap, _cf_wait):
    # 阶段②：受控真失败 —— 再点一次，3 秒后停掉 ComfyUI
    pb['gen_lbl'].setText('')
    pb['gen_btn'].setEnabled(True)
    pb['gen_btn'].click()
    _wait(3000)
    out.append('gen2_running_lbl=%r' % pb['gen_lbl'].text())
    _post_bg('/api/comfy/stop')
    _ok = _until(lambda: pb['gen_lbl'].text().startswith(('完成', '失败')),
                 timeout=180, gap=0.5)
    out.append('gen_fail_seen=%s lbl=%r' % (_ok, pb['gen_lbl'].text()))
    _snap('gen_fail')
    # ③ 恢复 ComfyUI
    _post_bg('/api/comfy/start')
    out.append('cf_restore online=%s' % _cf_wait(180))

def _p_30_real_upscale(out, w, pb, _wait, _snap, _until):
    # 阶段④：真超分 —— Real-ESRGAN + 输入目录第一张图
    w._nav_select(4)
    _wait(800)
    try:
        pb['up_kind'].setCurrentIndex(0)
        pb['up_refresh'].click()
    except Exception as _e:
        out.append('up_refresh EXC %r' % (_e,))
    _wait(2500)
    _eng_ix = -1
    for _i in range(pb['up_engine'].count()):
        if str(pb['up_engine'].itemData(_i) or '').startswith('esrgan:'):
            _eng_ix = _i
            break
    if _eng_ix >= 0:
        pb['up_engine'].setCurrentIndex(_eng_ix)
    pb['up_scale'].setCurrentText('2x')
    _imgs = [pb['up_img'].itemText(_i) for _i in range(pb['up_img'].count())]
    out.append('up_engine=%r (index=%d) imgs=%d scale=%r chips=%r'
               % (pb['up_engine'].currentText(), _eng_ix, len(_imgs),
                  pb['up_scale'].currentText(),
                  pb.get('up_scale_chips').value()
                  if pb.get('up_scale_chips') else None))
    if _imgs:
        pb['up_img'].setCurrentText(_imgs[0])
    else:
        out.append('up_no_input_images')
    pb['up_lbl'].setText('')
    pb['up_btn'].setEnabled(True)
    pb['up_btn'].click()
    _wait(1500)
    _snap('up_running')
    out.append('up_running_lbl=%r' % pb['up_lbl'].text())
    _ok = _until(lambda: pb['up_lbl'].text().startswith(('完成', '失败')),
                 timeout=1200, gap=0.5)
    out.append('up_done_seen=%s lbl=%r' % (_ok, pb['up_lbl'].text()))
    _snap('up_done')

def _p_30_real_offline(out, w, pb, _wait, _snap, _until, _cf_on, _post_bg):
    # 阶段⑤：离线真机态 —— 停 ComfyUI，清空标签让门控写入离线说明
    pb['gen_lbl'].setText(''); pb['vgen_lbl'].setText('')
    _post_bg('/api/comfy/stop')
    out.append('cf_offline=%s' % _until(lambda: not _cf_on(), timeout=120, gap=1.0))
    _wait(5000)   # 等 2s 状态轮询刷新门控与文案
    w._nav_select(2)
    _wait(1200)
    out.append('off_gen_btn_enabled=%s tip=%r lbl=%r'
               % (pb['gen_btn'].isEnabled(), pb['gen_btn'].toolTip(),
                  pb['gen_lbl'].text()))
    _snap('off_gen')
    try:
        QToolTip.showText(pb['gen_btn'].mapToGlobal(QPoint(24, 14)),
                          pb['gen_btn'].toolTip(), pb['gen_btn'],
                          pb['gen_btn'].rect(), 20000)
        _wait(500)
        _snap('off_tip', full=True)
        QToolTip.hideText()
    except Exception as _e:
        out.append('tooltip EXC %r' % (_e,))
    _wait(400)
    w._nav_select(4)
    _wait(1500)
    out.append('off_up_btn_enabled=%s tip=%r state=%r lbl=%r'
               % (pb['up_btn'].isEnabled(), pb['up_btn'].toolTip(),
                  pb['up_state_lbl'].text(), pb['up_lbl'].text()))
    _snap('off_up')

# 原分支 31：len(parts) == 2 and parts[0] == "status"
def _h_31_status(ctx, msg, parts):
    w, _err, api = ctx.w, ctx._err, ctx.api
    _p = parts[1]

    def _do_status():
        out = []
        ps = getattr(w, '_probe_status', {}) or {}

        pr = Probe(w, _err, _p, out, tick=0.03, pause=250)

        # finally 里要恢复定时器：先显式初始化，避免 try 早段抛异常时未绑定
        _tm = _tm_iv = _ts = _ts_iv = None
        try:
            w.showNormal(); w.activateWindow()
            # R08：注入必须能"撑住"——但停表不能太早：showNormal/nav 会触发
            # apply_page_timers 重启 t1/t2，先让事件跑完、拿正常态，再停表。
            w._nav_select(0)
            pr.wait(1600)
            _tm = ps.get('t_metrics'); _ts = ps.get('t_state')
            _tm_iv = _tm.interval() if _tm is not None else 2000
            _ts_iv = _ts.interval() if _ts is not None else 5000
            # ① 正常态：手动取一拍（此时真实轮询仍在跑，数据是新鲜的）
            ps['poll_metrics']()
            pr.wait(5000)
            pr.snap('status_normal')
            out.append('normal chart=%r mem=%r gpu=%r cpu=%r ram=%r pw=%r sv=%r'
                       % (ps['chart_state'].text(), ps['mem_big'].text(),
                          ps['gpu_big'].text(), ps['cpu_big'].text(),
                          ps['ram_big'].text(), ps['lbl_energy'].text(),
                          ps['sv_lbl'].text()))
            # 正常态截完后停表，并等最后一拍在途回包落地，再开始注入
            for _t in (_tm, _ts):
                try:
                    if _t is not None:
                        _t.stop()
                except Exception:
                    _err()
            pr.wait(3000)
            _p_31_status_lost(out, ps, pr.wait, pr.snap)
            _p_31_status_nodata(out, ps, api, pr.wait, pr.snap, _err)
            _p_31_status_recover(out, ps, _tm, _tm_iv, _ts, _ts_iv, pr.wait, pr.snap, _err)
            _p_31_status_routes(out, ps, w, _ts, pr.wait, pr.snap, _err)
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        finally:
            # 无论成败都恢复轮询，避免自检把应用留在「停表」状态
            for _t, _iv in ((_tm, _tm_iv), (_ts, _ts_iv)):
                try:
                    if _t is not None and not _t.isActive():
                        _t.start(_iv)
                except Exception:
                    _err()
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_status)

def _p_31_status_lost(out, ps, _wait, _snap):
    # 阶段②：断流 —— 连续 3 次真实失败返回，保持 4.2 s（>2 个 2 s 轮询周期）
    for _i in range(3):
        ps['metrics_done'](('ERR', 'connection refused'))
        _wait(150)
    _wait(4200)
    ps['metrics_done'](('ERR', 'connection refused'))  # 截图前再确认一次
    _wait(250)
    _snap('status_lost')
    out.append('lost chart=%r' % ps['chart_state'].text())

def _p_31_status_nodata(out, ps, api, _wait, _snap, _err):
    # 阶段③：无数据 —— 清真实采样缓冲（rows_buf 与探针是同一对象）并清线
    try:
        _s, _d = api('GET', '/api/metrics', timeout=10)
        _sys = (_d.get('sys') or {}) if isinstance(_d, dict) else {}
    except Exception:
        _sys = {}
    try:
        ps['rows_buf'].clear()
        _cl = 0
        for _c in ps['curves'].values():
            if _c is None:  # 温度序列走独立视图，此处字典值为 None
                continue
            _c.setData([], [])
            _cl += 1
        for _g in ps['glow_curves'].values():
            if _g is not None:
                _g.setData([], [])
        ps['temp_curve'].setData([], [])
        out.append('clear rows=%d curves=%d' % (len(ps['rows_buf']), _cl))
    except Exception as _e:
        out.append('clear EXC %r' % (_e,))
    ps['metrics_done']((200, {'sys': _sys, 'history': []}))
    _wait(4200)
    # 截图前再清一次：防止在途真实拍把缓冲填回来（上一轮 nodata 空白根因）
    try:
        ps['rows_buf'].clear()
        for _c in ps['curves'].values():
            if _c is not None:
                _c.setData([], [])
        for _g in ps['glow_curves'].values():
            if _g is not None:
                _g.setData([], [])
        ps['temp_curve'].setData([], [])
    except Exception:
        _err()
    ps['metrics_done']((200, {'sys': _sys, 'history': []}))
    _wait(250)
    _snap('status_nodata')
    out.append('nodata chart=%r mem=%r rows=%d'
               % (ps['chart_state'].text(), ps['mem_big'].text(),
                  len(ps['rows_buf'])))

def _p_31_status_recover(out, ps, _tm, _tm_iv, _ts, _ts_iv, _wait, _snap, _err):
    # 阶段④：恢复 —— 重新挂表再取数
    for _t, _iv in ((_tm, _tm_iv), (_ts, _ts_iv)):
        try:
            if _t is not None:
                _t.start(_iv)
        except Exception:
            _err()
    ps['poll_metrics']()
    _wait(3200)
    _snap('status_recover')
    out.append('recover chart=%r mem=%r rows=%d'
               % (ps['chart_state'].text(), ps['mem_big'].text(),
                  len(ps['rows_buf'])))

def _p_31_status_routes(out, ps, w, _ts, _wait, _snap, _err):
    # 阶段⑤-⑧：推理路线 —— 正常 / 断流 / 卡片空值 / 恢复
    # ⑤ 推理路线：正常（nav 会重启 t2 并立即 poll_routes，先让回包落地）
    w._nav_select(1)
    ps['poll_routes']()
    _wait(5000)
    _snap('routes_normal')
    out.append('routes chip=%r lms=%r cards=%r' % (
        ps['kv_chip'].text(), ps['lms_lbl'].text(),
        [ps['kv_cards'][_k].name_lbl.text() for _k in ps['kv_cards']]))
    # 停 t2（否则 5 s 一拍会把断流注入重置），等在途回包落地
    try:
        if _ts is not None:
            _ts.stop()
    except Exception:
        _err()
    _wait(3000)
    # ⑥ 断流：连续 2 次失败，保持 10.5 s（>2 个 5 s 轮询周期）
    for _i in range(2):
        ps['routes_done'](('ERR', 'connection refused'))
        _wait(150)
    _wait(10500)
    ps['routes_done'](('ERR', 'connection refused'))  # 截图前再确认一次
    _wait(250)
    _snap('routes_lost')
    out.append('routes_lost chip=%r lms=%r' % (ps['kv_chip'].text(),
                                              ps['lms_lbl'].text()))
    # ⑦ 卡片空值：后端返回空 profiles / 空 kvOpts
    ps['routes_done']((200, {'profiles': [], 'kvOpts': []}))
    _wait(700)
    _snap('routes_empty')
    out.append('routes_empty chip=%r lms=%r cards=%r' % (
        ps['kv_chip'].text(), ps['lms_lbl'].text(),
        [ps['kv_cards'][_k].name_lbl.text() for _k in ps['kv_cards']]))
    # ⑧ 恢复
    ps['poll_routes']()
    _wait(2200)
    _snap('routes_recover')
    out.append('routes_recover chip=%r' % ps['kv_chip'].text())

# 原分支 32：len(parts) == 2 and parts[0] == "fail"
def _h_32_fail(ctx, msg, parts):
    w, _err, _t, api = ctx.w, ctx._err, ctx._t, ctx.api
    _p = parts[1]

    def _do_fail():
        out = []
        pb = getattr(w, '_probe3', {}) or {}

        pr = Probe(w, _err, _p, out, tick=0.03, pause=200)


        def _on():
            try:
                _s, _d = api('GET', '/api/state', timeout=6)
                _c = (_d.get('comfy') or {}) if isinstance(_d, dict) else {}
                return bool(_c.get('alive') or _c.get('ready'))
            except Exception:
                return False

        try:
            w.showNormal(); w.activateWindow()
            out.append('online_at_start=%s' % _on())
            if not _on():
                out.append('start -> %r' % (api('POST', '/api/comfy/start', {}, timeout=60),))
                for _i in range(90):
                    QApplication.processEvents()
                    if _on():
                        break
                    pr.wait(1000)
            out.append('online_before_gen=%s' % _on())
            w._nav_select(2)
            pr.wait(500)
            pb['gen_w'].setValue(512); pb['gen_h'].setValue(512)
            try:
                pb['gen_steps'].setValue(4)
            except Exception:
                _err()
            pb['prompt'].setPlainText('a red apple on a wooden table, studio light')
            pb['gen_lbl'].setText('')
            pb['gen_btn'].setEnabled(True)
            pb['gen_btn'].click()
            pr.wait(2500)
            out.append('pre_stop_lbl=%r' % pb['gen_lbl'].text())
            out.append('stop -> %r' % (api('POST', '/api/comfy/stop', {}, timeout=180),))
            _ok = False
            for _i in range(90):
                QApplication.processEvents()
                if pb['gen_lbl'].text().startswith(('完成', '失败')):
                    _ok = True
                    break
                pr.wait(1000)
            out.append('fail_seen=%s lbl=%r' % (_ok, pb['gen_lbl'].text()))
            pr.snap('fail')
            out.append('restore -> %r' % (api('POST', '/api/comfy/start', {}, timeout=60),))
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_fail)

# 原分支 33：len(parts) == 2 and parts[0] == "upfail"
def _h_33_upfail(ctx, msg, parts):
    w, _err, _t, api = ctx.w, ctx._err, ctx._t, ctx.api
    _p = parts[1]

    def _do_upfail():
        out = []
        pb = getattr(w, '_probe3', {}) or {}

        pr = Probe(w, _err, _p, out, tick=0.03, pause=220)


        def _on():
            try:
                _s, _d = api('GET', '/api/state', timeout=6)
                _c = (_d.get('comfy') or {}) if isinstance(_d, dict) else {}
                return bool(_c.get('alive') or _c.get('ready'))
            except Exception:
                return False

        try:
            w.showNormal(); w.activateWindow()
            out.append('online_at_start=%s' % _on())
            if not _on():
                out.append('start -> %r' % (api('POST', '/api/comfy/start', {}, timeout=60),))
                for _i in range(90):
                    QApplication.processEvents()
                    if _on():
                        break
                    pr.wait(1000)
            out.append('online_before_up=%s' % _on())
            w._nav_select(4)
            pr.wait(700)
            try:
                pb['up_kind'].setCurrentIndex(0)
                pb['up_refresh'].click()
            except Exception as _e:
                out.append('up_refresh EXC %r' % (_e,))
            pr.wait(2500)
            _eng_ix = -1
            for _i in range(pb['up_engine'].count()):
                if str(pb['up_engine'].itemData(_i) or '').startswith('esrgan:'):
                    _eng_ix = _i
                    break
            if _eng_ix >= 0:
                pb['up_engine'].setCurrentIndex(_eng_ix)
            pb['up_scale'].setCurrentText('2x')
            _imgs = [pb['up_img'].itemText(_i) for _i in range(pb['up_img'].count())]
            out.append('up_engine=%r (index=%d) imgs=%d scale=%r chips=%r'
                       % (pb['up_engine'].currentText(), _eng_ix, len(_imgs),
                          pb['up_scale'].currentText(),
                          pb.get('up_scale_chips').value()
                          if pb.get('up_scale_chips') else None))
            if _imgs:
                pb['up_img'].setCurrentText(_imgs[0])
            else:
                out.append('up_no_input_images')
            pb['up_lbl'].setText('')
            pb['up_btn'].setEnabled(True)
            pb['up_btn'].click()
            pr.wait(4000)
            out.append('up_running_lbl=%r state=%r'
                       % (pb['up_lbl'].text(), pb['up_state_lbl'].text()))
            pr.snap('upfail_running')
            out.append('stop -> %r' % (api('POST', '/api/comfy/stop', {}, timeout=180),))
            _ok = False
            for _i in range(120):
                QApplication.processEvents()
                if pb['up_lbl'].text().startswith(('完成', '失败')):
                    _ok = True
                    break
                pr.wait(1000)
            out.append('up_fail_seen=%s lbl=%r' % (_ok, pb['up_lbl'].text()))
            pr.snap('upfail_fail')
            out.append('restore -> %r' % (api('POST', '/api/comfy/start', {}, timeout=60),))
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_upfail)

# 原分支 34：len(parts) == 2 and parts[0] == "scalechk"
def _h_34_scalechk(ctx, msg, parts):
    w, _err, _t = ctx.w, ctx._err, ctx._t
    _p = parts[1]

    def _do_scalechk():
        out = []
        pb = getattr(w, '_probe3', {}) or {}

        pr = Probe(w, _err, _p, tick=0.01, pause=250)

        def _state(tag):
            _cb = pb.get('up_scale')
            _ch = pb.get('up_scale_chips')
            return '%s combo=%r chip=%r' % (
                tag, _cb.currentText() if _cb else None,
                _ch.value() if _ch else None)

        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(4)
            pr.wait(900)
            out.append(_state('startup'))
            _w0 = w.width(); _h0 = w.height()
            out.append('viewport=%dx%d' % (_w0, _h0))
            pb['up_scale'].setCurrentText('2x')
            pr.wait(700)
            out.append(_state('combo->2x'))
            w.grab().save(_p + '_2x.png')
            pb['up_scale'].setCurrentText('4x')
            pr.wait(700)
            out.append(_state('combo->4x'))
            w.grab().save(_p + '_4x.png')
            # 用户真实路径：点可见 chips 的「2x」，combo（请求参数源）应跟随
            for _v, _b in (getattr(pb.get('up_scale_chips'), '_buttons', None) or []):
                if _v == 2:
                    _b.click()
                    break
            pr.wait(700)
            out.append(_state('chipclick->2x'))
            w.grab().save(_p + '_chip2x.png')
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_scalechk)

# 原分支 35：len(parts) == 2 and parts[0] == "offline"
def _h_35_offline(ctx, msg, parts):
    w, _err, _t, api = ctx.w, ctx._err, ctx._t, ctx.api
    _p = parts[1]

    def _do_offline():
        out = []
        pb = getattr(w, '_probe3', {}) or {}

        pr = Probe(w, _err, _p, out, tick=0.03, pause=250)


        try:
            w.showNormal(); w.activateWindow()
            out.append('stop -> %r' % (api('POST', '/api/comfy/stop', {}, timeout=180),))
            pr.wait(6500)   # 等 2s 状态轮询刷新门控
            pb['gen_lbl'].setText(''); pb['vgen_lbl'].setText('')
            w._nav_select(2)
            pr.wait(2500)
            out.append('gen_enabled=%s tip=%r lbl=%r'
                       % (pb['gen_btn'].isEnabled(), pb['gen_btn'].toolTip(),
                          pb['gen_lbl'].text()))
            pr.snap('off_gen')
            try:
                QToolTip.showText(pb['gen_btn'].mapToGlobal(QPoint(24, 14)),
                                  pb['gen_btn'].toolTip(), pb['gen_btn'],
                                  pb['gen_btn'].rect(), 20000)
                pr.wait(600)
                pr.snap('off_tip', full=True)
                QToolTip.hideText()
            except Exception as _e:
                out.append('tooltip EXC %r' % (_e,))
            w._nav_select(4)
            pr.wait(2500)
            out.append('up_enabled=%s tip=%r state=%r lbl=%r'
                       % (pb['up_btn'].isEnabled(), pb['up_btn'].toolTip(),
                          pb['up_state_lbl'].text(), pb['up_lbl'].text()))
            pr.snap('off_up')
            out.append('restore -> %r' % (api('POST', '/api/comfy/start', {}, timeout=60),))
        except Exception as _e:
            out.append('ERR %r' % (_e,))
        try:
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('\n'.join(out) + '\n')
        except Exception:
            _err()

    QTimer.singleShot(0, _do_offline)

# 原分支 36：len(parts) == 2 and parts[0] == "unitprobe"
def _h_36_unitprobe(ctx, msg, parts):
    w, CLK_MEM_MAX, CLK_SM_MAX, _CLK_STAT = ctx.w, ctx.CLK_MEM_MAX, ctx.CLK_SM_MAX, ctx._CLK_STAT
    _DUR_STAT, _EN_STAT, _PW_STAT, _err = ctx._DUR_STAT, ctx._EN_STAT, ctx._PW_STAT, ctx._err
    xh_fmt_clk, xh_fmt_dur, xh_fmt_energy, xh_norm_power = ctx.xh_fmt_clk, ctx.xh_fmt_dur, ctx.xh_fmt_energy, ctx.xh_norm_power
    _upath = parts[1]
    def _do_unitprobe():
        try:
            w.showNormal(); w.activateWindow(); w._nav_select(0)
        except Exception:
            _err()
        def _run():
            try:
                ps = getattr(w, '_probe_status', {}) or {}
                b_pw, b_clk = dict(_PW_STAT), dict(_CLK_STAT)
                b_dur, b_en = dict(_DUR_STAT), dict(_EN_STAT)
                cases = []
                def _add(name, got, want):
                    cases.append({'case': name, 'got': got, 'want': want,
                                  'ok': (got == want)})
                _add('p10.79/lim115', xh_norm_power(10.79, 115.0), 10.79)
                _add('p41444.95/lim115', xh_norm_power(41444.95, 115.0), 41.44495)
                _add('p41444.95/limNone', xh_norm_power(41444.95, None), None)
                _add('p8986.39/lim0', xh_norm_power(8986.39, 0.0), None)
                _add('pNone/lim115', xh_norm_power(None, 115.0), None)
                _add('clk65535', xh_fmt_clk(65535, CLK_SM_MAX), '未知')
                _add('clk1305', xh_fmt_clk(1305, CLK_SM_MAX), '1305')
                _add('clkNone', xh_fmt_clk(None, CLK_SM_MAX), '未知')
                _add('clkmem12001', xh_fmt_clk(12001, CLK_MEM_MAX), '12001')
                # R34：时长/电量量纲向量（5071 分为实测值）
                _add('dur5071min', xh_fmt_dur(304260), '3天12时')
                _add('dur59s', xh_fmt_dur(59), '59秒')
                _add('dur61s', xh_fmt_dur(61), '1分01秒')
                _add('dur3599s', xh_fmt_dur(3599), '59分59秒')
                _add('dur3600s', xh_fmt_dur(3600), '1时00分')
                _add('durNone', xh_fmt_dur(None), '—')
                _add('en1319.19', xh_fmt_energy(1319.19), '1.32\u00A0kWh')
                _add('en999.4', xh_fmt_energy(999.4), '999.40\u00A0Wh')
                _add('en999.5', xh_fmt_energy(999.5), '1.00\u00A0kWh')
                _add('enNone', xh_fmt_energy(None), '—')
                # 同一函数落到首屏真实控件（不是另写一套展示逻辑）
                if ps.get('lbl_sm') is not None:
                    ps['lbl_sm'].setText(xh_fmt_clk(65535, CLK_SM_MAX))
                if ps.get('lbl_memclk') is not None:
                    ps['lbl_memclk'].setText(xh_fmt_clk(40000, CLK_MEM_MAX))
                if ps.get('gauge_power') is not None:
                    ps['gauge_power'].set_value(xh_norm_power(41444.95, None), None)
                if ps.get('lbl_energy') is not None:
                    ps['lbl_energy'].setText(xh_fmt_energy(1319.19))
                if ps.get('lbl_energy2') is not None:
                    ps['lbl_energy2'].setText(
                        '有载 %s\u00A0·\u00A0均 %sW'
                        % (xh_fmt_dur(304260), '15.6'))
                a_pw, a_clk = dict(_PW_STAT), dict(_CLK_STAT)
                payload = {
                    'cases': cases,
                    'pw_before': b_pw, 'pw_after': a_pw,
                    'pw_delta': {k: a_pw[k] - b_pw[k] for k in b_pw},
                    'clk_before': b_clk, 'clk_after': a_clk,
                    'clk_delta': {k: a_clk[k] - b_clk[k] for k in b_clk},
                    'dur_before': b_dur, 'dur_after': dict(_DUR_STAT),
                    'dur_delta': {k: _DUR_STAT[k] - b_dur[k] for k in b_dur},
                    'en_before': b_en, 'en_after': dict(_EN_STAT),
                    'en_delta': {k: _EN_STAT[k] - b_en[k] for k in b_en},
                    'ui': {
                        'lbl_sm': ps['lbl_sm'].text() if ps.get('lbl_sm') else None,
                        'lbl_memclk': ps['lbl_memclk'].text() if ps.get('lbl_memclk') else None,
                        'lbl_energy': ps['lbl_energy'].text() if ps.get('lbl_energy') else None,
                        'lbl_energy2': (ps['lbl_energy2'].text()
                                        if ps.get('lbl_energy2') else None),
                        'gauge_power_value': getattr(ps.get('gauge_power'), '_value', None),
                    },
                    'compact': {
                        'on': bool(getattr(getattr(w, '_cm', None), '_compact', False)),
                        'hits': dict(getattr(getattr(w, '_cm', None), '_hits', {})),
                        'last_h': getattr(getattr(w, '_cm', None), '_last_h', None),
                    },
                    'scale': float(os.environ.get('QT_SCALE_FACTOR', '1') or 1),
                }
                with open(_upath, 'w', encoding='utf-8') as _f:
                    json.dump(payload, _f, ensure_ascii=False, indent=1)
            except Exception:
                _err()
        QTimer.singleShot(900, _run)
        QTimer.singleShot(1350, lambda: Probe(w, _err).grab_to(_upath + '.png'))
    QTimer.singleShot(0, _do_unitprobe)

# 原分支 37：len(parts) == 3 and parts[0] == "theme"
def _h_37_theme(ctx, msg, parts):
    w, XH_ADV, _err, _xh_apply_theme = ctx.w, XH_ADV, ctx._err, ctx._xh_apply_theme
    _th, _tp = parts[1], parts[2]

    def _do_theme():
        try:
            _xh_apply_theme(w, _th)
        except Exception:
            _err()

        def _dump_theme():
            try:
                with open(_tp, 'w', encoding='utf-8') as _f:
                    _f.write(str(XH_ADV.get('theme')))
            except Exception:
                _err()
        # 主题切换要重排 QSS + 两处自绘缓存，给 600 ms 再回执
        QTimer.singleShot(600, _dump_theme)

    QTimer.singleShot(0, _do_theme)

# 原分支 38：len(parts) >= 3 and parts[0] == "perf" and parts[1] == "mark"
def _h_38_perf(ctx, msg, parts):
    w, _perf_mark = ctx.w, ctx._perf_mark
    _perf_mark(msg[len("perf mark"):].strip() or 'mark')

# 原分支 39：len(parts) == 3 and parts[0] == "perf" and parts[1] == "dump"
def _h_39_perf(ctx, msg, parts):
    w, _perf_dump = ctx.w, ctx._perf_dump
    _perf_dump(parts[2])

# 原分支 40：len(parts) == 2 and parts[0] == "xxtabonly"
def _h_40_xxtabonly(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    try:
        _xoi = int(parts[1])
    except Exception:
        return

    def _do_xxtabonly():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(6)
            _xt = getattr(w, '_xx_tabs', None)
            if _xt is not None and 0 <= _xoi < _xt.count():
                _rf = getattr(w, '_xx_refresh', None)
                if _rf is not None:
                    _rf()
                _xt.setCurrentIndex(_xoi)
                if _xt.currentIndex() != _xoi:   # refresh 若再次改页，补一次
                    _xt.setCurrentIndex(_xoi)
        except Exception:
            _err()
    QTimer.singleShot(0, _do_xxtabonly)

# 原分支 41：len(parts) in (3, 4) and parts[0] == "scroll"
def _h_41_scroll(ctx, msg, parts):
    w, _err, ux = ctx.w, ctx._err, ctx.ux
    try:
        _sp_i = int(parts[1]); _sp_w = parts[2].lower()
        _sp_n = int(parts[3]) if len(parts) == 4 else 0
    except Exception:
        return

    def _do_scroll():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(_sp_i)
            _pg = w._stack.widget(_sp_i)
            # R253/R254：惰性构建的配置子标签先补齐真控件，否则枚举不到 docBody。
            # 若目标是第 5 页第 n 份文档，就只建那一页（真实点击路径），
            # 建完把它当作唯一 docBody（索引 0）来滚。
            _n = _sp_n
            _ens1 = getattr(w, '_ensure_lazy_doc', None)
            if _sp_i == 5 and _ens1 is not None and 0 <= _sp_n < len(ux.LEGAL_VIEW_ITEMS):
                try:
                    _ens1(_sp_n)
                except Exception:
                    _err()
                _pg = w._stack.currentWidget()   # R255：二级页面是独立 stack 页
                _n = 0
            else:
                _ens = getattr(w, '_ensure_lazy_cfg', None)
                if _ens is not None:
                    try:
                        _ens()
                    except Exception:
                        _err()

            def _tab_of(_sa):
                # 最近一层 QTabWidget 及该控件所在的 tab 序号；
                # QStackedLayout 会随切页改子控件顺序，必须按 tab 序号定序。
                _cur = _sa
                while _cur is not None and _cur is not _pg:
                    _par = _cur.parentWidget()
                    if _par is None:
                        return None, -1
                    if isinstance(_par, QTabWidget):
                        for _ti in range(_par.count()):
                            _tw = _par.widget(_ti)
                            if _tw is not None and _tw.isAncestorOf(_sa):
                                return _par, _ti
                        return None, -1
                    _cur = _par
                return None, -1

            _ars = []
            if _pg is not None:
                for _sa in _pg.findChildren(QAbstractScrollArea):
                    if _sa.objectName() == 'docBody' or isinstance(_sa, QTextBrowser):
                        _k = 0
                    elif isinstance(_sa, QScrollArea):
                        _k = 1
                    else:
                        _k = 2
                    _tq, _tix = _tab_of(_sa)
                    _ars.append(((_k, _tix if _tix >= 0 else 999,
                                  int(_sa.y()), int(_sa.x())), _sa, _tq, _tix))
            _ars.sort(key=lambda _t: _t[0])
            if not (0 <= _n < len(_ars)):
                return
            _key, _sa, _tq, _tix = _ars[_n]
            if _tq is not None and _tix >= 0:
                _tq.setCurrentIndex(_tix)
            _vb = _sa.verticalScrollBar()
            _vb.setValue(int(_vb.maximum()) if _sp_w == 'bottom' else 0)
            _sa.viewport().update()
        except Exception:
            _err()
    QTimer.singleShot(0, _do_scroll)

# 原分支 42：len(parts) == 2 and parts[0] == "clickback"
def _h_42_clickback(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _cb_p = parts[1]

    def _do_clickback():
        try:
            _hit = None
            for _b in w.findChildren(QPushButton):
                if _b.isVisible() and _b.text().startswith('←'):
                    _hit = _b
                    break
            if _hit is not None:
                _hit.click()
            _d = {'clicked': bool(_hit is not None),
                  'nav': int(w._stack.currentIndex())}
            _ct2 = w.findChild(QTabWidget, 'cfgTop')
            if _ct2 is not None:
                _d['cfg'] = int(_ct2.currentIndex())
            with open(_cb_p, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()
    QTimer.singleShot(0, _do_clickback)

# 原分支 43：len(parts) == 2 and parts[0] == "consent"
def _h_43_consent(ctx, msg, parts):
    w, _err, ux = ctx.w, ctx._err, ctx.ux
    _cs_p = parts[1]

    def _do_consent():
        try:
            _p = ux.prefs()
            _d = {'stored': str(_p.s.value('legal/accepted', '')),
                  'expect': ux.CONSENT_VERSION,
                  'ok': bool(_p.consent_ok()),
                  'accepted_at': _p.consent_at(),
                  'exe': sys.executable}
            with open(_cs_p, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()
    QTimer.singleShot(0, _do_consent)

# 原分支 44：len(parts) == 2 and parts[0] == "pageid"
def _h_44_pageid(ctx, msg, parts):
    w, GlassCard, _err = ctx.w, ctx.GlassCard, ctx._err
    _pid_p = parts[1]

    def _do_pageid():
        try:
            _d = {'nav': int(w._stack.currentIndex())}
            _ct = w.findChild(QTabWidget, 'cfgTop')
            if _ct is not None:
                _d['cfg'] = [int(_ct.currentIndex()),
                             _ct.tabText(_ct.currentIndex())]
            _xt = getattr(w, '_xx_tabs', None)
            if _xt is not None:
                _d['xx'] = [int(_xt.currentIndex()),
                            _xt.tabText(_xt.currentIndex())]
                _d['xx_geo'] = [int(_xt.width()), int(_xt.height())]
                _d['xx_vis'] = bool(_xt.isVisible())
            _pg = w._stack.widget(int(w._stack.currentIndex()))
            if _pg is not None:
                _eff = _pg.graphicsEffect()
                _d['page'] = _pg.objectName() or _pg.__class__.__name__
                _d['page_geo'] = [int(_pg.width()), int(_pg.height())]
                _d['page_eff'] = _eff.__class__.__name__ if _eff is not None else None
                try:
                    _d['page_eff_opacity'] = float(_eff.opacity())
                except Exception:
                    _d['page_eff_opacity'] = None
            _d['visible'] = bool(w.isVisible())
            _d['minimized'] = bool(w.isMinimized())
            # C1-B3 Prefs 唯一入口：配置页实例必须就是 ux.prefs() 那一个
            _d['prefs_id'] = id(getattr(w, '_adv_prefs', None))
            try:
                _d['prefs_same'] = bool(getattr(w, '_adv_prefs', None) is ctx.ux.prefs())
            except Exception:
                _d['prefs_same'] = None
            try:
                _wh = w.windowHandle()
                _d['exposed'] = bool(_wh is not None and _wh.isExposed())
            except Exception:
                _d['exposed'] = None
            _d['size'] = [w.width(), w.height()]
            _d['scale'] = os.environ.get('QT_SCALE_FACTOR', '1')
            _cs = []
            for _c in w.findChildren(GlassCard):
                if _c.isVisible():
                    _cp = _c.mapTo(w, _c.rect().topLeft())
                    _cs.append([int(_cp.x()), int(_cp.y()),
                                int(_c.width()), int(_c.height())])
            _d['cards'] = _cs
            _fs = []
            for _pfw in w.findChildren(QWidget, 'pagefoot'):
                if _pfw.isVisible():
                    _fp = _pfw.mapTo(w, _pfw.rect().topLeft())
                    _fs.append([int(_fp.x()), int(_fp.y()),
                                int(_pfw.width()), int(_pfw.height())])
            _d['foot'] = _fs
            _bs = []
            for _b in w.findChildren(QPushButton):
                if _b.isVisible() and _b.objectName() != 'navitem':
                    _bp = _b.mapTo(w, _b.rect().topLeft())
                    _bs.append([_b.text(), int(_bp.x()), int(_bp.y()),
                                int(_b.width()), int(_b.height())])
            _d['btns'] = _bs
            if _pg is not None:
                _kids = []
                for _k in _pg.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
                    _kp = _k.mapTo(w, _k.rect().topLeft())
                    _kids.append([_k.objectName() or _k.__class__.__name__,
                                  int(_kp.x()), int(_kp.y()),
                                  int(_k.width()), int(_k.height())])
                _d['kids'] = _kids[:12]
            _rail = w.findChild(QFrame, 'navrail')
            if _rail is not None:
                _its = []
                for _b in _rail.findChildren(QPushButton):
                    if _b.objectName() != 'navitem':
                        continue
                    _g = _b.geometry()
                    _pt = _b.mapTo(w, _b.rect().topLeft())
                    _its.append({'t': _b.text() or _b.toolTip(),
                                 'x': int(_pt.x()), 'y': int(_pt.y()),
                                 'w': int(_g.width()), 'h': int(_g.height())})
                _d['rail'] = {'w': int(_rail.width()), 'items': _its}
            with open(_pid_p, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_pageid)

# 原分支 45：len(parts) == 2 and parts[0] == "tabdiag"
def _h_45_tabdiag(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    def _do_tabdiag():
        try:
            _o = []
            for _tw in w.findChildren(QTabWidget):
                _tb = _tw.tabBar()
                if _tb is None:
                    continue
                _f = _tb.font()
                _fm = _tb.fontMetrics()
                _it = {'tw': _tw.objectName() or 'QTabWidget',
                       'tw_geo': [int(_tw.width()), int(_tw.height())],
                       'bar': [int(_tb.x()), int(_tb.y()),
                               int(_tb.width()), int(_tb.height())],
                       'hint': [int(_tb.sizeHint().width()), int(_tb.sizeHint().height())],
                       'font': [_f.family(), round(_f.pointSizeF(), 2),
                                int(_f.weight()), round(_f.letterSpacing(), 2),
                                int(_fm.horizontalAdvance('隐私与日志')),
                                int(_fm.horizontalAdvance('对话'))],
                       'sheet': [len(_tb.styleSheet() or ''),
                                 (hash(_tb.styleSheet() or '') & 0xffffff)],
                       'icon0': [bool(_tb.tabIcon(0).isNull()),
                                 int(_tb.tabIcon(0).availableSizes()[0].width())
                                 if _tb.tabIcon(0).availableSizes() else -1],
                       'fm0': [int(_fm.horizontalAdvance(_tb.tabText(0))),
                               int(_tb.iconSize().width())],
                       'tabs': []}
                for _i in range(_tb.count()):
                    _r = _tb.tabRect(_i)
                    _it['tabs'].append([_tb.tabText(_i), int(_r.x()), int(_r.y()),
                                        int(_r.width()), int(_r.height())])
                _o.append(_it)
            with open(parts[1], 'w', encoding='utf-8') as _f2:
                json.dump({'tabbars': _o}, _f2, ensure_ascii=False, indent=1)
        except Exception:
            _err()
    QTimer.singleShot(0, _do_tabdiag)

# 原分支 46：len(parts) == 2 and parts[0] == "capdiag"
def _h_46_capdiag(ctx, msg, parts):
    w, _cap_colors, _err = ctx.w, ctx._cap_colors, ctx._err
    _cd_p = parts[1]
    _cd_rows = []

    def _cd_sample(tag):
        _r = {'t': tag}
        try:
            _r['visible'] = bool(w.isVisible())
            _r['minimized'] = bool(w.isMinimized())
            try:
                _wh = w.windowHandle()
                _r['exposed'] = bool(_wh is not None and _wh.isExposed())
            except Exception:
                _r['exposed'] = None
            _r['size'] = [w.width(), w.height()]
            _pm = w.grab()
            _r['null'] = bool(_pm.isNull())
            _img = _pm.toImage()
            _r['colors'] = _cap_colors(_img)
            _r['alpha_tl'] = _img.pixelColor(4, 4).alpha()
        except Exception:
            _err()
        _cd_rows.append(_r)

        def _dump():
            try:
                with open(_cd_p, 'w', encoding='utf-8') as _f:
                    json.dump(_cd_rows, _f, ensure_ascii=False, indent=1)
            except Exception:
                _err()

        if tag == 3000:
            _dump()

    for _d in (0, 300, 900, 1800, 3000):
        QTimer.singleShot(_d, lambda _t=_d: _cd_sample(_t))

# 原分支 47：len(parts) == 3 and parts[0] == "xxtab"
def _h_47_xxtab(ctx, msg, parts):
    w, _cap_grab, _err = ctx.w, ctx._cap_grab, ctx._err
    try:
        _xi = int(parts[1]); _xp = parts[2]
    except Exception:
        return

    def _do_xxtab():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(6)
            _xt = getattr(w, '_xx_tabs', None)
            if _xt is not None and 0 <= _xi < _xt.count():
                # R247：先 refresh 再 setCurrentIndex。refresh 内部会改
                # 对话/Agent 的内层堆栈，放在切页之后会把刚切好的页又顶掉。
                _rf = getattr(w, '_xx_refresh', None)
                if _rf is not None:
                    _rf()
                _xt.setCurrentIndex(_xi)
                if _xt.currentIndex() != _xi:   # refresh 若再次改页，补一次
                    _xt.setCurrentIndex(_xi)
            else:
                with open(_xp + '.err', 'w', encoding='utf-8') as _f:
                    _f.write('xxtab: no _xx_tabs or index %d out of range\n' % _xi)
        except Exception:
            _err()
        _cap_grab(w, _xp, tag='xxtab %d' % _xi)
    QTimer.singleShot(0, _do_xxtab)

# 原分支 48：len(parts) == 3 and parts[0] == "shot"
def _h_48_shot(ctx, msg, parts):
    w, _cap_grab, _err = ctx.w, ctx._cap_grab, ctx._err
    try:
        idx = int(parts[1]); path = parts[2]
    except Exception:
        return
    def _do_shot():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(idx)
        except Exception:
            _err()
        _cap_grab(w, path, tag='shot %d' % idx, wait=400)
    QTimer.singleShot(0, _do_shot)

# 原分支 49：len(parts) in (3, 4) and parts[0] == "wtree"
def _h_49_wtree(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    try:
        if len(parts) == 4:
            _wi = int(parts[1]); _wsub = int(parts[2]); _wp = parts[3]
        else:
            _wi = int(parts[1]); _wsub = None; _wp = parts[2]
    except Exception:
        return
    def _do_wtree():
        try:
            w.showNormal(); w.activateWindow()
            w._nav_select(_wi)
            if _wsub is not None:
                _ct = w.findChild(QTabWidget, 'cfgTop')
                if _ct is not None:
                    _ct.setCurrentIndex(_wsub)
        except Exception:
            _err()
        def _dump_wtree():
            try:
                _st = w.findChildren(QStackedWidget)
                try:
                    _cfg = w.findChild(QTabWidget, 'cfgTop')
                except Exception:
                    _cfg = None
                _rows = []
                for _x in w.findChildren(QWidget):
                    try:
                        if not _x.isVisible():
                            continue
                        _gp = _x.mapTo(w, QPoint(0, 0))
                        _f = _x.font()
                        _txt = ''
                        for _m in ('text', 'currentText', 'toPlainText'):
                            if hasattr(_x, _m):
                                try:
                                    _txt = str(getattr(_x, _m)())[:80]
                                    break
                                except Exception:
                                    pass
                        _rows.append({
                            'cls': type(_x).__name__,
                            'on': _x.objectName(),
                            'text': _txt,
                            'en': _x.isEnabled(),
                            'x': _gp.x(), 'y': _gp.y(),
                            'w': _x.width(), 'h': _x.height(),
                            'px': _f.pixelSize() or round(_f.pointSizeF() * 1.333, 1),
                            'ss': _x.styleSheet()[:200],
                        })
                    except Exception:
                        continue
                _pl = {'idx': _wi,
                       'stack': (_st[0].currentIndex() if _st else -1),
                       'cfg': (_cfg.currentIndex() if _cfg else -1),
                       'dpi': float(os.environ.get('QT_SCALE_FACTOR', '1') or 1),
                       'widgets': _rows}
                with open(_wp, 'w', encoding='utf-8') as _fh:
                    json.dump(_pl, _fh, ensure_ascii=False)
            except Exception:
                _err()
        QTimer.singleShot(900, _dump_wtree)
    QTimer.singleShot(0, _do_wtree)

# 原分支 50：len(parts) >= 4 and parts[0] == "burst"
def _h_50_burst(ctx, msg, parts):
    w, AnimatedNumberLabel, _NUM_FRAMES, _err = ctx.w, ctx.AnimatedNumberLabel, ctx._NUM_FRAMES, ctx._err
    try:
        prefix = parts[1]
        _n = int(parts[2]); _gap = int(parts[3])
        _old = parts[4] if len(parts) >= 6 else None
        _new = parts[5] if len(parts) >= 6 else None
    except Exception:
        return
    _lab = None
    try:
        for _l in w.findChildren(AnimatedNumberLabel):
            if _l.isVisible():
                _lab = _l
                break
    except Exception:
        _lab = None
    _rec = []
    _t0 = [time.perf_counter()]
    try:
        if getattr(w, '_poll_t1', None) is not None:
            w._poll_t1.stop()
    except Exception:
        _err()
    _brect = None
    _lrect = None
    _full = os.environ.get('XH_BURST_FULL', '') == '1'
    try:
        if _lab is not None:
            _lp = _lab.mapTo(w, QPoint(0, 0))
            _lrect = [_lp.x(), _lp.y(), _lab.width(), _lab.height()]
            if not _full:
                # 只抓数字控件四周 8px：grab 面积从 2000×1462 降到 ~320×112，
                # 单帧耗时 <20 ms，帧间隔才能压到 ≤200 ms。
                _brect = QRect(_lp.x() - 8, _lp.y() - 8,
                               _lab.width() + 16, _lab.height() + 16)
    except Exception:
        _brect = None
        _lrect = None

    def _burst_one(i):
        try:
            _el = (time.perf_counter() - _t0[0]) * 1000.0
            if _brect is not None:
                w.grab(_brect).save('%s_%02d.png' % (prefix, i))
            else:
                w.grab().save('%s_%02d.png' % (prefix, i))
            _rec.append([i, round(_el, 1),
                         getattr(_lab, '_shown', None) if _lab is not None else None])
            if i == 0 and _lab is not None and _new is not None:
                if _anim:
                    _lab.set_target(float(_new))
                else:
                    _lab.setText(_new)
        except Exception as _e:
            _rec.append([i, 'EXC %r' % _e])
        if i + 1 < _n:
            QTimer.singleShot(_gap, lambda: _burst_one(i + 1))
        else:
            try:
                with open(prefix + '.json', 'w', encoding='utf-8') as _f:
                    json.dump({'gap': _gap, 'frames': _rec,
                               'old': _old, 'new': _new,
                               'anim': _anim, 'dp': _dp,
                               'num_frames': _NUM_FRAMES,
                               'rect': _lrect,
                               'win': [w.width(), w.height()]}, _f)
            except Exception:
                _err()
            try:
                if getattr(w, '_poll_t1', None) is not None:
                    w._poll_t1.start()
            except Exception:
                _err()

    _anim = os.environ.get('XH_BURST_ANIM', '') == '1'
    _dp = int(os.environ.get('XH_BURST_DP', '0') or 0)
    if _lab is not None and _dp:
        _lab.set_formatter(lambda v, _d=_dp: ('%.*f%%' % (_d, v)))
    if _lab is not None and _old is not None:
        if _anim:
            _lab.set_target(float(_old))
        else:
            _lab.setText(_old)
    QTimer.singleShot(0, lambda: _burst_one(0))

def _h_51_invoke(ctx, msg, parts):
    """invoke <name> [out.json] [timeout_ms] [accept] —— 真点一次按钮的调试探针。

    只在 launcher 以 `XH_DEBUG_PROBE=1` 启动、注册了 `w._invoke_map` 时可用；
    正式运行态没有这张表 → 返回 unknown，不改变任何行为。
    点击后等请求落地：弹了 QMessageBox 就抓正文并关掉（无人值守验收不卡在模态框），
    否则一直等到 timeout（默认 15s，调用方可传小值）。不做「按钮变亮就提前返回」的猜测——
    页面轮询会周期性重设按钮可用态，早退会误报已完成。
    第 5 个参数 `accept`：点击前预埋一个 100ms 轮询的 QTimer，模态框一出现就按 Yes 关闭。
    专用于「槽函数自己 exec 模态框」的场景（如 stopall 的二次确认）——这种框在 slot 里
    同步 exec，点击调用不返回，主循环的 activeModalWidget 轮询根本轮不到。
    结果写 json（默认 invoke_<name>.json）：{button, clicked, modal_title, modal_text,
    enabled_after, waited_ms}。
    """
    w = ctx.w
    m = getattr(w, '_invoke_map', None) or {}
    name = parts[1]
    out_path = parts[2] if len(parts) >= 3 else ('invoke_%s.json' % name)
    tmo = int(parts[3]) if len(parts) >= 4 else 15000
    answer = len(parts) >= 5 and parts[4] == 'accept'
    b = m.get(name)
    if b is None:
        return 'invoke unknown/non-debug: %s' % name
    if not b.isEnabled():
        b.setEnabled(True)   # 离线门控会把按钮置灰，探针强开只为验证点击链路
    _seen = {}

    def _answer_modal():
        _mw = QApplication.activeModalWidget()
        if _mw is None:
            QTimer.singleShot(100, _answer_modal)
            return
        _seen['title'] = _mw.windowTitle()
        _seen['text'] = _mw.text() if hasattr(_mw, 'text') else ''
        _mw.done(QMessageBox.Yes)

    if answer:
        QTimer.singleShot(100, _answer_modal)
    b.click()
    modal_title = _seen.get('title', '')
    modal_text = _seen.get('text', '')
    _t0 = time.time()
    _end = _t0 + tmo / 1000.0
    while not _seen and time.time() < _end:
        QApplication.processEvents()
        _mw = QApplication.activeModalWidget()
        if _mw is not None and hasattr(_mw, 'text'):
            modal_title = _mw.windowTitle()
            modal_text = _mw.text()
            _mw.reject()
            break
        time.sleep(0.05)
    res = dict(button=name, clicked=True, modal_title=modal_title, modal_text=modal_text,
               enabled_after=bool(b.isEnabled()), waited_ms=int((time.time() - _t0) * 1000))
    try:
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, ensure_ascii=False)
    except OSError:
        ctx._err()
    return 'invoke %s -> %s' % (name, json.dumps(res, ensure_ascii=False))

# 原分支 52：exitdlg <path> —— 读退出面板（R4 验收：面板可见 + 列出服务名）
def _h_52_exitdlg(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _p = parts[1]

    def _do_exitdlg():
        try:
            _d = dict(getattr(w, '_shutdown_state', None) or {'visible': False})
            with open(_p, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_exitdlg)


# 原分支 53：exitforce —— 点面板上的「强制退出」（走 QPushButton.clicked 真信号）
def _h_53_exitforce(ctx, msg, parts):
    w = ctx.w
    _fn = getattr(w, '_shutdown_force', None)
    if _fn is not None:
        QTimer.singleShot(0, _fn)


# R5 分支 54：exitrecon <path> —— 读启动对账结果（infra.exitd.STATE + 状态页那行文本）
def _h_54_exitrecon(ctx, msg, parts):
    w, _err = ctx.w, ctx._err
    _p = parts[1]

    def _do_exitrecon():
        try:
            from infra import exitd as _exitd
            _d = dict(_exitd.STATE)
            _d['label'] = str(getattr(w, '_exit_reconcile', '') or '')
            with open(_p, 'w', encoding='utf-8') as _f:
                json.dump(_d, _f, ensure_ascii=False)
        except Exception:
            _err()

    QTimer.singleShot(0, _do_exitrecon)

ROUTES = [
    ('show', lambda msg, parts: msg == "show", _h_00_show),
    ('quit2', lambda msg, parts: msg == "quit2", _h_01_quit2),
    ('quit', lambda msg, parts: msg == "quit", _h_02_quit),
    ('shot', lambda msg, parts: len(parts) == 4 and parts[0] == "shot" and parts[1] == "5", _h_03_shot),
    ('nav', lambda msg, parts: len(parts) == 2 and parts[0] == "nav", _h_04_nav),
    ('sb', lambda msg, parts: len(parts) == 2 and parts[0] == "sb", _h_05_sb),
    ('edge', lambda msg, parts: len(parts) == 2 and parts[0] == "edge", _h_06_edge),
    ('shot', lambda msg, parts: len(parts) == 2 and parts[0] == "shot", _h_07_shot),
    ('bootinfo', lambda msg, parts: len(parts) == 2 and parts[0] == "bootinfo", _h_08_bootinfo),
    ('ftrace', lambda msg, parts: len(parts) == 3 and parts[0] == "ftrace", _h_09_ftrace),
    ('trayinfo', lambda msg, parts: len(parts) == 2 and parts[0] == "trayinfo", _h_10_trayinfo),
    ('cadstat', lambda msg, parts: len(parts) == 2 and parts[0] == "cadstat", _h_11_cadstat),
    ('alertstat', lambda msg, parts: len(parts) == 2 and parts[0] == "alertstat", _h_12_alertstat),
    ('chartna', lambda msg, parts: len(parts) == 2 and parts[0] == "chartna", _h_13_chartna),
    ('jobstbl', lambda msg, parts: len(parts) == 2 and parts[0] == "jobstbl", _h_14_jobstbl),
    ('traymenu', lambda msg, parts: len(parts) == 2 and parts[0] == "traymenu", _h_15_traymenu),
    ('wscan', lambda msg, parts: len(parts) == 2 and parts[0] == "wscan", _h_16_wscan),
    ('sztest', lambda msg, parts: len(parts) == 2 and parts[0] == "sztest", _h_17_sztest),
    ('snap', lambda msg, parts: len(parts) == 2 and parts[0] == "snap", _h_18_snap),
    ('deltest', lambda msg, parts: len(parts) == 2 and parts[0] == "deltest", _h_19_deltest),
    ('fitstat', lambda msg, parts: len(parts) == 2 and parts[0] == "fitstat", _h_20_fitstat),
    ('errtest', lambda msg, parts: len(parts) == 2 and parts[0] == "errtest", _h_21_errtest),
    ('errs', lambda msg, parts: len(parts) == 2 and parts[0] == "errs", _h_22_errs),
    ('dump', lambda msg, parts: len(parts) == 2 and parts[0] == "dump", _h_23_dump),
    ('节拍', lambda msg, parts: len(parts) >= 2 and parts[0] == "节拍", _h_24_x),
    ('setpoll', lambda msg, parts: len(parts) == 3 and parts[0] == "setpoll", _h_25_setpoll),
    ('paintwho', lambda msg, parts: len(parts) == 2 and parts[0] == "paintwho", _h_26_paintwho),
    ('adv', lambda msg, parts: len(parts) == 2 and parts[0] == "adv", _h_27_adv),
    ('kvinfo', lambda msg, parts: len(parts) == 2 and parts[0] == "kvinfo", _h_28_kvinfo),
    ('walk', lambda msg, parts: len(parts) == 2 and parts[0] == "walk", _h_29_walk),
    ('real', lambda msg, parts: len(parts) == 2 and parts[0] == "real", _h_30_real),
    ('status', lambda msg, parts: len(parts) == 2 and parts[0] == "status", _h_31_status),
    ('fail', lambda msg, parts: len(parts) == 2 and parts[0] == "fail", _h_32_fail),
    ('upfail', lambda msg, parts: len(parts) == 2 and parts[0] == "upfail", _h_33_upfail),
    ('scalechk', lambda msg, parts: len(parts) == 2 and parts[0] == "scalechk", _h_34_scalechk),
    ('offline', lambda msg, parts: len(parts) == 2 and parts[0] == "offline", _h_35_offline),
    ('unitprobe', lambda msg, parts: len(parts) == 2 and parts[0] == "unitprobe", _h_36_unitprobe),
    ('theme', lambda msg, parts: len(parts) == 3 and parts[0] == "theme", _h_37_theme),
    ('perf', lambda msg, parts: len(parts) >= 3 and parts[0] == "perf" and parts[1] == "mark", _h_38_perf),
    ('perf', lambda msg, parts: len(parts) == 3 and parts[0] == "perf" and parts[1] == "dump", _h_39_perf),
    ('xxtabonly', lambda msg, parts: len(parts) == 2 and parts[0] == "xxtabonly", _h_40_xxtabonly),
    ('scroll', lambda msg, parts: len(parts) in (3, 4) and parts[0] == "scroll", _h_41_scroll),
    ('clickback', lambda msg, parts: len(parts) == 2 and parts[0] == "clickback", _h_42_clickback),
    ('consent', lambda msg, parts: len(parts) == 2 and parts[0] == "consent", _h_43_consent),
    ('pageid', lambda msg, parts: len(parts) == 2 and parts[0] == "pageid", _h_44_pageid),
    ('tabdiag', lambda msg, parts: len(parts) == 2 and parts[0] == "tabdiag", _h_45_tabdiag),
    ('capdiag', lambda msg, parts: len(parts) == 2 and parts[0] == "capdiag", _h_46_capdiag),
    ('xxtab', lambda msg, parts: len(parts) == 3 and parts[0] == "xxtab", _h_47_xxtab),
    ('shot', lambda msg, parts: len(parts) == 3 and parts[0] == "shot", _h_48_shot),
    ('wtree', lambda msg, parts: len(parts) in (3, 4) and parts[0] == "wtree", _h_49_wtree),
    ('burst', lambda msg, parts: len(parts) >= 4 and parts[0] == "burst", _h_50_burst),
    ('invoke', lambda msg, parts: len(parts) in (2, 3, 4, 5) and parts[0] == "invoke", _h_51_invoke),
    ('exitdlg', lambda msg, parts: len(parts) == 2 and parts[0] == "exitdlg", _h_52_exitdlg),
    ('exitforce', lambda msg, parts: len(parts) == 2 and parts[0] == "exitforce", _h_53_exitforce),
    ('exitrecon', lambda msg, parts: len(parts) == 2 and parts[0] == "exitrecon", _h_54_exitrecon),
]

COMMANDS = {
    'show': ('_h_00_show'),
    'quit2': ('_h_01_quit2'),
    'quit': ('_h_02_quit'),
    'shot': ('_h_03_shot', '_h_07_shot', '_h_48_shot'),
    'nav': ('_h_04_nav'),
    'sb': ('_h_05_sb'),
    'edge': ('_h_06_edge'),
    'bootinfo': ('_h_08_bootinfo'),
    'ftrace': ('_h_09_ftrace'),
    'trayinfo': ('_h_10_trayinfo'),
    'cadstat': ('_h_11_cadstat'),
    'alertstat': ('_h_12_alertstat'),
    'chartna': ('_h_13_chartna'),
    'jobstbl': ('_h_14_jobstbl'),
    'traymenu': ('_h_15_traymenu'),
    'wscan': ('_h_16_wscan'),
    'sztest': ('_h_17_sztest'),
    'snap': ('_h_18_snap'),
    'deltest': ('_h_19_deltest'),
    'fitstat': ('_h_20_fitstat'),
    'errtest': ('_h_21_errtest'),
    'errs': ('_h_22_errs'),
    'dump': ('_h_23_dump'),
    '节拍': ('_h_24_x'),
    'setpoll': ('_h_25_setpoll'),
    'paintwho': ('_h_26_paintwho'),
    'adv': ('_h_27_adv'),
    'kvinfo': ('_h_28_kvinfo'),
    'walk': ('_h_29_walk'),
    'real': ('_h_30_real'),
    'status': ('_h_31_status'),
    'fail': ('_h_32_fail'),
    'upfail': ('_h_33_upfail'),
    'scalechk': ('_h_34_scalechk'),
    'offline': ('_h_35_offline'),
    'unitprobe': ('_h_36_unitprobe'),
    'theme': ('_h_37_theme'),
    'perf': ('_h_38_perf', '_h_39_perf'),
    'xxtabonly': ('_h_40_xxtabonly'),
    'scroll': ('_h_41_scroll'),
    'clickback': ('_h_42_clickback'),
    'consent': ('_h_43_consent'),
    'pageid': ('_h_44_pageid'),
    'tabdiag': ('_h_45_tabdiag'),
    'capdiag': ('_h_46_capdiag'),
    'xxtab': ('_h_47_xxtab'),
    'wtree': ('_h_49_wtree'),
    'burst': ('_h_50_burst'),
    'invoke': ('_h_51_invoke'),
    'exitdlg': ('_h_52_exitdlg'),
    'exitforce': ('_h_53_exitforce'),
    'exitrecon': ('_h_54_exitrecon'),
}
COMMAND_NAMES = tuple(COMMANDS)



def dispatch(ctx, msg):
    """按原 elif 顺序匹配；未命中与原实现一致，静默返回 None。"""
    parts = msg.split()
    for _name, when, fn in ROUTES:
        if when(msg, parts):
            return fn(ctx, msg, parts)
    return None

