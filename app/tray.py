# -*- coding: utf-8 -*-
"""运行时：托盘 / 关闭语义 / 退出收尾 / F5 刷新（C1-B2 第 8c 批）。

原位置：xinghong_launcher.py build_ui() L7776–8049（274 行；8b 搬完后的
L7415–7688），整段原样搬出：
  · 托盘图标 + 右键菜单（显示窗口 / 暂停刷新(勾) / 静音通知(勾) / 退出）；
  · show_window（Win32 ShowWindow+SetForegroundWindow 前台锁兜底）；
  · confirm_exit（POST /api/stopall 异步 3.3 s，超时走 _kill_children 再退）；
  · on_close 的 closeMode 三态：ask 弹框 / tray 隐藏降频 / exit 直接确认；
  · F5 _refresh_current（按当前页触发对应轮询）。

分层：app 层，只依赖 PySide6 / 标准库 / infra.config；launcher 的模块级状态
（_deco_* / _kill_children）与页面回调全部显式传参，禁止 import xinghong_launcher。
B3：静音门 / 退出方式改为直接读 infra.config（对象身份与搬迁前同一份）。

句柄：launcher 只解包 tray（供前面各页 `get_tray=lambda: tray` 的闭包）与
_refresh_current（w._refresh_current 用）；w._tray / w._tray_menu / w._tray_state /
w._show_window / w.closeEvent 在本模块内直接挂到主窗。
"""
import ctypes
import os
import time

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QLabel, QMenu,
                               QMessageBox, QPushButton, QStyle,
                               QSystemTrayIcon, QVBoxLayout)

from infra.config import XH_MUTE as _XH_MUTE, XH_EXIT_HOW as _XH_EXIT_HOW

from app.shutdown import build_shutdown

__all__ = ['build_tray_and_close']

def build_tray_and_close(*, w, api, on_select, stack, _geo, _t, PANEL_TITLE,
                         _err, _kill_children, _xh_cadence,
                         _deco_set_paused, _deco_resume_frame,
                         _poll_timers, t2, poll_state, apply_page_timers,
                         _apply_poll_cadence, _hidden_alert, t_alert, _ALERT_ST,
                         rows_buf, last_ts, _chart_na_probe, cfg_jobs_tbl,
                         poll_metrics, poll_sv, poll_routes, poll_comfy,
                         poll_outputs, poll_vwall, poll_up, poll_uwall,
                         poll_cfg, poll_jobs_tbl, poll_health):
    """托盘图标 / 菜单 / 关闭三态（ask·tray·exit）/ F5 刷新（C1-B2 第 8c 批）。

    参数名与 build_ui 内原标识符逐字一致（MECHANICAL_EQUAL 逐行比对）。
    """

    _ti = w.windowIcon()
    tray = QSystemTrayIcon(_ti if not _ti.isNull()
                           else w.style().standardIcon(QStyle.SP_ComputerIcon))
    # R20：QAction 必须挂到菜单上（缺 parent 时 Python 侧引用释放后动作被销毁，
    # 托盘右键菜单变空，build32 实测 actions 2→0）。
    # R36-N3：菜单为 显示窗口 / 暂停刷新(勾) / 静音通知(勾) / 分隔 / 退出。
    _menu_qss = (_t("QMenu{background:rgba(18,26,38,0.96);"
                 "border:1px solid rgba(120,190,255,0.28);border-radius:14px;padding:8px;}"
                 "QMenu::item{padding:8px 16px;border-radius:8px;color:@INK_0@;}"
                 "QMenu::item:selected{background:rgba(90,170,255,0.22);}"
                 "QMenu::item:checked{color:@ACCENT_SOFT@;}"
                 "QMenu::separator{height:1px;background:rgba(255,255,255,0.10);"
                 "margin:4px 8px;}"))
    tm = QMenu(w)
    tm.setStyleSheet(_menu_qss)
    show_act = QAction('显示窗口', tm)
    pause_act = QAction('暂停刷新', tm)
    pause_act.setCheckable(True)
    mute_act = QAction('静音通知', tm)
    mute_act.setCheckable(True)
    exit_act = QAction('退出（停止托管服务）', tm)
    tm.addAction(show_act)
    tm.addAction(pause_act)
    tm.addAction(mute_act)
    tm.addSeparator()
    tm.addAction(exit_act)
    tray.setContextMenu(tm)
    tray.setToolTip(PANEL_TITLE)

    def show_window():
        w.showNormal()
        w.raise_()
        w.activateWindow()
        # R36-N3：从别的前台应用点托盘时，Qt 的 activateWindow 常被系统前台锁拦下，
        # 窗口只闪任务栏。做法：Win32 ShowWindow(SW_RESTORE)+SetForegroundWindow。
        try:
            _hw = int(w.winId())
            _u = ctypes.windll.user32
            _u.ShowWindow(_hw, 9)
            _u.SetForegroundWindow(_hw)
        except Exception:
            _err()
        # X3：托盘隐藏降频后，恢复时回到常规轮询节奏；R15-X14：按当前页重启全部定时器
        # R20-8：恢复值同样读唯一真源（此前这里是 4000*_PK，与启动 5000*_PK 不一致）
        # R37-1：先整体还原间隔再启停，否则 apply_page_timers 会把隐藏期的 ×10 间隔
        # 原样 start 回来（隐藏只降频不停表，间隔就是唯一的状态载体）。
        _apply_poll_cadence(False)
        _hidden_alert(False)   # R38：恢复显示即停告警通道，回到页内 t14 节奏
        if not t2.isActive():
            t2.start()  # R1-X01：隐藏时被 stop，恢复必须显式启动
        try:
            poll_state()  # 隐藏期 t2 被拉长，恢复瞬间补一拍，状态芯片不等人
        except Exception:
            _err()
        apply_page_timers(stack.currentIndex())
        _deco_set_paused(False, 'resume')
        _deco_resume_frame()

    # R36-N3：暂停刷新/静音通知两个勾项的真实状态 + 弹出前回填
    _tray_state = {'pause': False, 'mute': False}

    def _set_pause(on):
        _tray_state['pause'] = bool(on)
        try:
            if on:
                for _tm2 in _poll_timers.values():
                    _tm2.stop()
            else:
                apply_page_timers(stack.currentIndex())
        except Exception:
            _err()

    def _set_mute(on):
        _tray_state['mute'] = bool(on)
        _XH_MUTE['on'] = bool(on)

    def _sync_menu_checks():
        for _a, _k in ((pause_act, 'pause'), (mute_act, 'mute')):
            _a.blockSignals(True)
            _a.setChecked(bool(_tray_state[_k]))
            _a.blockSignals(False)

    pause_act.toggled.connect(_set_pause)
    mute_act.toggled.connect(_set_mute)
    tm.aboutToShow.connect(_sync_menu_checks)

    def _on_tray_activated(reason):
        # R36-N3：左键（Trigger）与双击都开主界面；右键菜单由 setContextMenu(tm) 承担。
        # R38：实测弹过告警气泡后，Windows 会把 Trigger 补发一次（隐藏后 12~23 s 主窗自己
        #      弹出）。只有光标真落在托盘图标上才算用户点击；几何无效时退回旧行为。
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            _info = {'t': time.time(), 'reason': int(reason), 'hit': None}
            _hit = True
            try:
                from PySide6.QtGui import QCursor
                _g = tray.geometry()
                _p = QCursor.pos()
                _info['rect'] = [_g.x(), _g.y(), _g.width(), _g.height()]
                _info['cursor'] = [_p.x(), _p.y()]
                if _g.isValid() and _g.width() > 4:
                    _hit = bool(_g.adjusted(-8, -8, 8, 8).contains(_p))
                _info['hit'] = bool(_hit)
            except Exception:
                _err()
            _ALERT_ST.setdefault('activations', []).append(_info)
            if not _hit:
                return
            show_window()

    tray.activated.connect(_on_tray_activated)
    # R38：点气泡正文 = 明确要看，直接开窗（不再依赖 Trigger）
    try:
        tray.messageClicked.connect(show_window)
    except Exception:
        _err()
    tray.show()
    # R20：tray/tm 均为无 parent 的局部对象，build_ui 返回后会被 GC（图标消失、
    # 右键菜单空）。挂到主窗属性上做显式保活（QAction 必须显式指定 parent）。
    w._tray = tray
    w._tray_menu = tm
    w._tray_state = _tray_state
    w._show_window = show_window   # R37-1：管道 `show`/第二次启动也走同一条恢复路径
    # R37-1：把 metrics 增量缓冲暴露给 `cadstat`，用于证明「降频期不断拍、恢复后不漏数」
    w._metrics_buf = rows_buf
    w._metrics_last_ts = last_ts
    # R37-2：只读探针——F5 的 last_na 读数分支、F2 的 cfg1_jobs 表格文本
    w._chart_na_probe = _chart_na_probe
    w._jobs_tbl = cfg_jobs_tbl
    # R38：告警通道自证——polls/pops/延迟，供管道 `alertstat` 导出
    w._alert_state = _ALERT_ST
    w._alert_timer = t_alert

    settings = _geo  # X19：统一同一组 QSettings，不再出现第二组

    _EXIT_OK = {'v': False}   # R38-5：已确认退出 → closeEvent 不再二次弹「退出」确认
    _EXIT_HOW = _XH_EXIT_HOW   # 正常退出方式（模块级共享），写进 _graceful_exit.json 供看门狗区分崩溃

    def _exit_now():
        """面板判定可以退了才真关：_EXIT_OK 已置位，closeEvent 直接 accept。"""
        try:
            w.close()
        finally:
            QApplication.quit()

    _sd = build_shutdown(w=w, api=api, _kill_children=_kill_children,
                         _on_exit=_exit_now)

    def confirm_exit(source='close'):
        """确认退出；确认后交给退出链路并返回 False（closeEvent 先不关窗）。

        两个「退出」按钮（R5 分层退出）：
          · 「退出并停止服务」（默认，AcceptRole）→ 立刻退，托管服务交给后台守护进程收尾；
          · 「等它停完再退」→ R4 那条看得见的等（面板 / 进度 / 强制退出 / 40 s 上限）。
        返回 True 只表示「已确认且无需等服」（当前不再出现）；真退出由回调 _exit_now 完成。
        """
        if _EXIT_OK['v']:
            return True
        # X23：退出确认框默认按钮为「否」，回车不默认停服
        box = QMessageBox(w)
        box.setWindowTitle('退出')
        box.setText('退出并停止全部托管服务？')
        box.setInformativeText('默认立即退出，托管服务在后台停止；也可等它停完再退。')
        b_yes = box.addButton('退出并停止服务', QMessageBox.AcceptRole)
        b_wait = box.addButton('等它停完再退', QMessageBox.ActionRole)
        b_no = box.addButton('取消', QMessageBox.RejectRole)
        box.setDefaultButton(b_no)
        box.exec()
        cb = box.clickedButton()
        if cb is None or cb is b_no:
            return False
        _EXIT_OK['v'] = True
        # R5：默认 fast（≤2 s 退出，守护进程收尾）；用户点「等它停完再退」才走面板。
        _sd['start'](source, 'wait' if cb is b_wait else 'fast')
        return False

    def on_close(event):
        _geo.setValue('geometry', w.saveGeometry())
        _geo.setValue('lastTab', stack.currentIndex())
        # R38-5：托盘「退出」已确认过一次，收尾阶段不再走 tray/ask 分支重复询问
        if _EXIT_OK['v']:
            event.accept()
            return
        m = settings.value('closeMode', 'ask')
        if m == 'tray':
            w.hide()
            event.ignore()
            # X3：托盘隐藏统一降频（隐藏页不轮询，跨页基础轮询降一半）
            # R15-X14：隐藏时停全部轮询定时器（含跨页 t2），恢复时按当前页重启
            # R20-8：隐藏倍率只在 _xh_cadence(hidden=True) 里乘一次
            t2.setInterval(_xh_cadence(hidden=True)['t2'])
            _hidden_alert(True)   # R38：tray 分支同样要留告警通道
            for _nm, _tm in _poll_timers.items():
                _tm.stop()
            return
        if m == 'exit':
            if confirm_exit():
                event.accept()
            else:
                event.ignore()
            return
        dlg = QDialog(w)
        dlg.setWindowTitle('关闭 ' + PANEL_TITLE)
        dv = QVBoxLayout(dlg)
        dv.setContentsMargins(16, 16, 16, 12)
        dv.addWidget(QLabel('关闭窗口时希望：'))
        remember = QCheckBox('记住我的选择')
        b_tray = QPushButton('最小化到托盘（服务继续运行）')
        b_exit = QPushButton('退出应用（停止托管服务）')
        b_exit.setObjectName('danger')
        dv.addWidget(b_tray)
        dv.addWidget(b_exit)
        dv.addWidget(remember)

        def pick(val, store):
            if remember.isChecked():
                settings.setValue('closeMode', store)
            dlg.done(val)

        b_tray.clicked.connect(lambda: pick(1, 'tray'))
        b_exit.clicked.connect(lambda: pick(2, 'exit'))
        dlg.exec()
        if dlg.result() == 1:
            w.hide()
            event.ignore()
            # R37-1：ask 分支此前只 hide 不降频，隐藏后 t1/t2/t11 仍按常速空转。
            # 与 tray 分支同源：只降频不停表——t1 的 since 增量采集与 t11 的采样
            # 连续性都依赖「持续有拍」，停表会在隐藏期留下断档。
            _apply_poll_cadence(True)
            _hidden_alert(True)   # R38：隐藏态独立告警通道（2 s，只拉 /api/alerts）
            return
        elif confirm_exit():
            event.accept()
        else:
            event.ignore()

    w.closeEvent = on_close
    show_act.triggered.connect(show_window)

    def _menu_exit(*_):
        _EXIT_HOW['v'] = 'menu_exit'
        confirm_exit('menu')

    exit_act.triggered.connect(_menu_exit)
    try:
        lt = int(_geo.value('lastTab', 0))
        if 0 <= lt < stack.count():
            on_select(lt)
    except Exception:
        _err()

    def _refresh_current():
        """R007（D-08）F5：按当前页触发对应轮询。"""
        fns = {0: (poll_metrics, poll_sv),
               1: (poll_routes,),
               2: (poll_comfy, poll_outputs),
               3: (poll_vwall,),
               4: (poll_up, poll_uwall),
               5: (poll_cfg, poll_jobs_tbl, poll_health)}
        for f in fns.get(stack.currentIndex(), ()):
            try:
                f()
            except Exception:
                _err()

    return {'tray': tray, 'refresh': _refresh_current}
