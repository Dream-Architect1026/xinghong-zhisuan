# -*- coding: utf-8 -*-
"""退出链路：默认「先退、后台收尾」，可选的「等它停完再退」（R5 分层退出）。

背景：`stop_all()` 在 LM Studio 常驻时实测 ~31 s。R4 的「看得见的等」解决的是
"看起来卡死"，但常态退出仍要盯着面板半分钟——用户否掉了：上限 40 s 只是安全网，
不该是日常。于是拆成两条路：

  默认（fast）：
    点确认 → 写 exit.log 的 pending 行（mode=detached、pending_after_exit=true）→
    detach 拉起 infra.exitd 守护进程 → 立刻关窗退出（目标 ≤2 s）。托管服务的收尾
    （等 LM Studio 退完、必要时强杀、补记真实结束时间）由守护进程在面板消失后完成。
    spawn 失败（进程创建不出来）就回落 wait 面板，绝不假装已经交给后台。

  等待（wait，用户在确认框里点「等它停完再退」）：
    R4 那条路原样保留——面板逐行列 KVMem / LM Studio / ComfyUI / 保活代理，
    8 s 补提示，可点「强制退出」，40 s 上限到点强杀；每次退出写 exit.log。

两条路都在 exit.log 留痕（mode=fast 的 pending 行 + 守护进程的完成行，或 wait 的
单行），exit_pending.json 供下次启动对账（infra.exitd.reconcile）。

分层：app 层；落盘走 infra.store.exit_log，拉起守护进程走 infra.exitd，禁止 import launcher。
探针：本模块把状态挂到 w._shutdown_state，强制按钮挂 w._shutdown_force，
      供 diag_cmds 的 exitdlg / exitforce 读取（自动化验收用）。
"""
import os
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from infra import exitd as _exitd
from infra import store as _store

__all__ = ['build_shutdown']

# 顺序即面板行序；key 与 /api/state 的字段一一对应
SERVICES = (('kv', 'KVMem'), ('lms', 'LM Studio'),
            ('comfy', 'ComfyUI'), ('proxy', '保活代理'))
HINT_AFTER = 8.0      # 秒：补「LM Studio 退出较慢」提示
CAP = 40.0            # 秒：上限，到点自动强杀
TICK_MS = 250


def build_shutdown(*, w, api, _kill_children, _on_exit):
    """返回 {'start': start(source, mode)}；同一时刻只允许一个面板。

    mode='fast'（默认）：交给后台守护进程后立刻退；mode='wait'：弹面板等停完。
    """

    S = {'dlg': None, 'state': None}

    def _read_state():
        """返回 {kv/lms/comfy/proxy: bool}；后端不可达时返回 None（调用方区分）。"""
        try:
            r = api('GET', '/api/state', timeout=3)
            if r and r[0] == 200:
                d = r[1] or {}
                return {'kv': bool((d.get('kv') or {}).get('alive')),
                        'lms': bool((d.get('lms') or {}).get('running')),
                        'comfy': bool((d.get('comfy') or {}).get('alive')),
                        'proxy': bool((d.get('proxy') or {}).get('running'))}
        except Exception:
            return None
        return None

    def _fast(source):
        """默认路径：把收尾交给 detach 出去的守护进程，然后立刻退。成功返回 True。"""
        t0 = time.time()
        try:
            ok = bool(_exitd.spawn({'src': source, 't0': t0, 'pid': os.getpid(),
                                    'at': time.strftime('%Y-%m-%d %H:%M:%S')}))
        except Exception:
            ok = False
        if not ok:
            # 守护进程都没拉起来就不能说「已交给后台」，回落面板由用户看着停。
            _store.startup_log('exit-detach-failed', src=source)
            return False
        _store.exit_log(source=source, t0=t0, t1=time.time(), services={},
                        forced=False, timeout=False, mode='detached',
                        pending=True, result='pending')
        try:
            _on_exit()
        except Exception:
            pass
        return True

    def start(source='close', mode='fast'):
        if S['dlg'] is not None:
            return
        if mode != 'wait' and _fast(source):
            return
        _wait(source)

    def _wait(source):
        if S['dlg'] is not None:
            return
        t0 = time.time()
        live = {'svc': {}, 'stop_done': False, 'stop_ok': False, 'bad': 0, 'quit': False,
                'ready': False}
        stopped_at = {}
        done = {'v': False}

        dlg = QDialog(w)
        dlg.setWindowTitle('正在停止托管服务…')
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setWindowFlag(Qt.WindowCloseButtonHint, False)
        dlg.setFixedWidth(400)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(18, 16, 18, 14)
        head = QLabel('正在停止托管服务，请稍候…')
        head.setStyleSheet('font-size:14px;font-weight:600;')
        v.addWidget(head)
        rows = {}
        for key, name in SERVICES:
            lb = QLabel('%s    检查中…' % name)
            lb.setStyleSheet('padding:2px 0;')
            v.addWidget(lb)
            rows[key] = lb
        hint = QLabel('')
        hint.setWordWrap(True)
        hint.setVisible(False)
        hint.setStyleSheet('color:#e0a94a;padding-top:4px;')
        v.addWidget(hint)
        el = QLabel('已用时 0.0 s')
        el.setStyleSheet('color:#8a9bb0;padding-top:2px;')
        v.addWidget(el)
        btn = QPushButton('强制退出')
        btn.setObjectName('danger')
        v.addWidget(btn)

        # 探针出口（diag_cmds: exitdlg / exitforce）
        w._shutdown_state = {'visible': True, 'src': source, 'rows': [], 'hint': '',
                             'elapsed': 0.0, 'done': False, 'forced': False,
                             'timeout': False}
        w._shutdown_force = btn.click
        S['dlg'], S['state'] = dlg, w._shutdown_state
        dlg.show()

        def _poll_loop():
            first = True
            while not live['quit']:
                snap = _read_state()
                if snap is None:
                    live['bad'] += 1
                else:
                    live['bad'] = 0
                    live['svc'] = snap
                    for k, on in snap.items():
                        if not on:
                            stopped_at.setdefault(k, time.time() - t0)
                if first:
                    # 先让面板拿到一次真实状态，再放 stopall 出去：
                    # 后端 stop 期间 /api/state 可能被服务锁挡住，晚一步就只能显示「检查中」。
                    live['ready'] = True
                    first = False
                time.sleep(0.5)

        def _post_stop():
            _w = time.time()
            while not live['ready'] and time.time() - _w < 2.0:
                time.sleep(0.05)
            try:
                api('POST', '/api/stopall', {}, timeout=CAP + 5)
                live['stop_ok'] = True
            except Exception:
                live['stop_ok'] = False
            finally:
                live['stop_done'] = True

        threading.Thread(target=_poll_loop, daemon=True).start()
        threading.Thread(target=_post_stop, daemon=True).start()

        def finish(mode):
            # mode: ok / forced / timeout
            if done['v']:
                return
            done['v'] = True
            live['quit'] = True
            timer.stop()
            forced, timeout = mode == 'forced', mode == 'timeout'
            if forced or timeout:
                # 强杀要保证树空：托管服务启动期会连着冒子进程。
                # 一刀 + 120 ms 后补一刀（收掉窗口期里新冒的），不再做第三刀——
                # 「强制退出」的 1 s 预算是硬指标，杀进程这段必须短。
                try:
                    _kill_children(os.getpid())
                except Exception:
                    pass
                time.sleep(0.12)
                try:
                    _kill_children(os.getpid())
                except Exception:
                    pass
            if S['state'] is not None:
                S['state'].update({'visible': False, 'done': True, 'forced': forced,
                                   'timeout': timeout, 'elapsed': time.time() - t0})
            _store.exit_log(source=source, t0=t0, t1=time.time(),
                            services=dict(stopped_at), forced=forced, timeout=timeout,
                            mode='wait', pending=False, result=mode)
            try:
                dlg.close()
            except Exception:
                pass
            S['dlg'] = None
            try:
                _on_exit()
            except Exception:
                pass

        btn.clicked.connect(lambda: finish('forced'))

        timer = QTimer(dlg)
        timer.setInterval(TICK_MS)

        def _tick():
            elapsed = time.time() - t0
            texts = []
            for key, name in SERVICES:
                on = live['svc'].get(key)
                if on is None:
                    st = '停止中…' if elapsed >= 1.0 else '检查中…'
                elif on:
                    st = '停止中…'
                else:
                    st = '已停止'
                rows[key].setText('%s    %s' % (name, st))
                texts.append((name, st))
            el.setText('已用时 %.1f s' % elapsed)
            if elapsed >= HINT_AFTER and not hint.isVisible():
                hint.setText('LM Studio 退出较慢，通常 30 秒内完成')
                hint.setVisible(True)
            if S['state'] is not None:
                S['state'].update({'rows': texts, 'hint': hint.text(),
                                   'elapsed': elapsed})
            # 完成：stopall 返回成功即停完（后端已确认「KVMem/LM Studio/ComfyUI 全停」）；
            # 只有 stopall 失败（超时/报错）时才回头看 /api/state，四服务全停或后端连续
            # 不可达约 2 s 也算停完。不能只靠 state：stopall 中途 state 会回跳（LM Studio
            # 卸载期 running 反复），只认 state 会被一次回跳拖到 40 s 上限。
            if live['stop_done']:
                vals = list(live['svc'].values())
                all_off = bool(vals) and not any(vals)
                fell_over = live['bad'] >= 4
                if live['stop_ok'] or not vals or all_off or fell_over:
                    finish('ok')
                    return
            if elapsed >= CAP:
                finish('timeout')

        timer.timeout.connect(_tick)
        timer.start()

    return {'start': start}
