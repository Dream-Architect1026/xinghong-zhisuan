# -*- coding: utf-8 -*-
"""运行时：轮询定时器 / 隐藏态告警通道 / 页面节拍与遮挡门控（C1-B2 第 8b 批）。

原位置：xinghong_launcher.py build_ui() L7381–7763（383 行），整段原样搬出：
  · /api/state 单总线（t2）与 10 个页面级定时器（t1/t4/t5/t11/t13/t14/t15/t16/t17）；
  · R38 隐藏态告警通道（t_alert，2 s 只拉 /api/alerts，气泡去重/静音/免打扰）；
  · 按页启停 apply_page_timers、隐藏降频 _apply_poll_cadence、节拍自检 _cadence_probe；
  · R07 最小化停表（_WinVisFilter）、R20-9 遮挡/离页 1 Hz 门控（_deco_gate）。

非机械差异（1 处，已在 _b2_8b_equal.py 声明）：
  `tray.showMessage(` → `get_tray().showMessage(`（tray 在 8c 才创建）。

分层：app 层，只依赖 PySide6 / 标准库 / infra.config；launcher 的模块级状态（_CAD_MEAS /
_DECO* / _deco_*）与页面回调全部显式传参，禁止 import xinghong_launcher。
B3：节拍表 / 成本日志 / 静音门改为直接读 infra.config（对象身份与搬迁前同一份）。

句柄：launcher 解包后仍用原来的 8 个名字（_poll_timers / t2 / poll_state /
apply_page_timers / _apply_poll_cadence / _hidden_alert / t_alert / _ALERT_ST）；
w._poll_timers、w._cadence_probe、w._alert_state、w._alert_timer、w._deco_gate
在本模块内直接挂到主窗，管道命令读到的仍是同一份对象。
"""
import ctypes
import ctypes.wintypes
import json
import time

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QSystemTrayIcon

from infra.config import XH_CADENCE, XH_COST, XH_MUTE as _XH_MUTE

__all__ = ['build_polling_timers']

def build_polling_timers(*, w, api, qwait, stack, R10,
                         _err, _cost, _perf_mark, _xh_cadence, _cad_hit,
                         _cad_rows, _CAD_MEAS, _DECO, _DECO_PAUSED,
                         _DECO_WHY, _deco_deco_widgets, _deco_set_paused,
                         _deco_resume_frame, get_tray, _timer_hook, drawn,
                         _sv_done, _routes_done, _comfy_done, _cfg_done,
                         poll_metrics, poll_sv, poll_routes, poll_comfy,
                         poll_outputs, poll_energy, poll_jobs_tbl, poll_health,
                         poll_cfg, poll_cfg_logs, poll_vwall, poll_uwall,
                         poll_up):
    """轮询定时器 / 隐藏态告警通道 / 页面节拍门控（C1-B2 第 8b 批）。

    参数名与 build_ui 内原标识符逐字一致（MECHANICAL_EQUAL 逐行比对）；
    get_tray 为懒取（tray 在 8c 才创建，与 page 模块同一取法）。
    """

    # ===== polling timers =====
    # R007（XH-14）：/api/state 四路定时器（3s/4s/5s/8s）合并为单总线——
    # 一次取数分发四方，页面间状态不再各刷各的。
    def poll_state():
        qwait(lambda: api('GET', '/api/state'), _state_done, key='state')

    def _state_done(r):
        for cb in (_sv_done, _routes_done, _comfy_done, _cfg_done):
            _c0 = (time.perf_counter(), time.thread_time())
            try:
                cb(r)
            except Exception:
                _err()
            _cost('cb:%s:state' % getattr(cb, '__name__', '?'), _c0)

    # R10（pollslow）：轮询降频/合并。t2 是跨页常驻总线（/api/state 一次取数分发四方），
    # 它 5 s 一拍且每次走 tasklist/netstat 子进程缓存，是稳态 CPU 的主要来源之一；
    # 页面级轮询再 ×3，数据新鲜度仍远快于人的感知（10–45 s）。
    # R20-8：所有有效毫秒只从 _xh_cadence() 读，本段不再出现 ×2/×1000/_PK。
    _CAD = _xh_cadence()
    XH_CADENCE.clear(); XH_CADENCE.update(_CAD)
    t1 = QTimer(w)
    w._poll_t1 = t1          # R20-5：_xh_apply_adv 通过该属性改节拍（创建前不存在，故用 getattr 守卫）
    t1.timeout.connect(poll_metrics)
    t1.timeout.connect(lambda: _cad_hit('t1'))
    t1.start(_CAD['t1'])
    # R02（25 轮）：服务端采样本就是 2 s 一拍，1 s 轮询有一半是空转（含一次全量/增量的
    # HTTP 往返与曲线判断）。改为 2 s，数据新鲜度不变，轮询开销减半。
    # R03：`nometrics` 仅用于归因——把 metrics 轮询拉到 60 s，测剩余底噪（已并入真源）。
    t2 = QTimer(w)
    t2.timeout.connect(poll_state)
    t2.timeout.connect(lambda: _cad_hit('t2'))
    t2.start(_CAD['t2'])
    # R08：状态页自检需要在注入「无数据/断流」时暂停真实轮询，否则注入会被
    # 2 s/5 s 的真实拍覆盖回正常态（上一轮 nodata/lost 图因此看不出文案）。
    try:
        w._probe_status['t_metrics'] = t1
        w._probe_status['t_state'] = t2
    except Exception:
        _err()
    t4 = QTimer(w)
    t4.timeout.connect(poll_outputs)
    t4.timeout.connect(lambda: _cad_hit('t4'))
    t4.start(_CAD['t4'])
    t5 = QTimer(w)
    t5.timeout.connect(poll_up)
    t5.timeout.connect(lambda: _cad_hit('t5'))
    t5.start(_CAD['t5'])
    t11 = QTimer(w)
    t11.timeout.connect(poll_energy)
    t11.timeout.connect(lambda: _cad_hit('t11'))
    t11.start(_CAD['t11'])
    t13 = QTimer(w)
    t13.timeout.connect(poll_jobs_tbl)
    t13.timeout.connect(lambda: _cad_hit('t13'))
    t13.start(_CAD['t13'])
    t14 = QTimer(w)
    t14.timeout.connect(poll_health)
    t14.timeout.connect(lambda: _cad_hit('t14'))
    t14.start(_CAD['t14'])
    t15 = QTimer(w)
    t15.timeout.connect(poll_cfg_logs)
    t15.timeout.connect(lambda: _cad_hit('t15'))
    t15.start(_CAD['t15'])  # R02：4→6 s；日志子页仅为读尾查看，6 s 刷新肉眼无差
    t16 = QTimer(w)
    t16.timeout.connect(poll_vwall)
    t16.timeout.connect(lambda: _cad_hit('t16'))
    t16.start(_CAD['t16'])
    t17 = QTimer(w)
    t17.timeout.connect(poll_uwall)
    t17.timeout.connect(lambda: _cad_hit('t17'))
    t17.start(_CAD['t17'])
    for f in (poll_metrics, poll_state, poll_outputs, poll_up, poll_energy,
              poll_jobs_tbl, poll_health, poll_cfg_logs, poll_vwall, poll_uwall):
        QTimer.singleShot(300, f)

    # X3：轮询定时器按页启停。t2(poll_state) 是托管服务状态 chip 的基础慢轮询，
    # 跨页保留；其余定时器仅当前页运行。错峰 offset 由各 timer 原间隔天然错开。
    _page_of = {0: {'t1', 't11'},
                1: set(),
                2: {'t4', 't5'},
                3: {'t16'},
                4: {'t17'},
                5: {'t13', 't14', 't15'}}
    _poll_timers = {
        't1': t1, 't2': t2, 't4': t4, 't5': t5, 't11': t11,
        't13': t13, 't14': t14, 't15': t15, 't16': t16, 't17': t17,
    }
    w._poll_timers = _poll_timers    # R20-8：运行时旋钮/节拍自检都读这一份

    # R38：隐藏态专用告警通道。后端采样是 2 s 一拍（sample_interval），但隐藏期 t1 拉到
    # 20 s 才拉一次全量增量，若告警只能搭 t1 的车，最晚要等 20 s 才弹。这里单独拉
    # /api/alerts（约 300 B，只读 HIST[-1]，不扫目录/不算分/不探端口），间隔取 2 s：
    # 再快也不能早于后端出新点，2 s 即为该通道的延迟下限。
    t_alert = QTimer(w)
    t_alert.setInterval(2000)
    _ALERT_ST = {'polls': 0, 'pops': 0, 'errors': 0, 'last_ts': None,
                 'last_rows': None, 'last_rtt_ms': None, 'last_pop': None,
                 'seen': {}}

    def _alert_poll():
        try:
            if not w.isHidden() or not t_alert.isActive():
                return
            _ALERT_ST['_t0'] = time.perf_counter()
            qwait(lambda: api('GET', '/api/alerts'), _alert_done, key='alerts')
        except Exception:
            _err()

    def _alert_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                _ALERT_ST['errors'] += 1
                return
            _code, a = r
            if not isinstance(a, dict):
                return
            _ALERT_ST['polls'] += 1
            _t0 = _ALERT_ST.pop('_t0', None)
            if _t0 is not None:
                _ALERT_ST['last_rtt_ms'] = int((time.perf_counter() - _t0) * 1000)
            _rows = a.get('alerts') or []
            _ts = float(a.get('ts') or 0)
            _now = time.time()
            _ALERT_ST['last_ts'] = _ts
            _ALERT_ST['last_rows'] = len(_rows)
            _cur, _new = {}, []
            for _it in _rows:
                _k = str(_it.get('key') or '')
                _lv = str(_it.get('level') or '')
                _cur[_k] = _lv
                if _ALERT_ST['seen'].get(_k) != _lv:
                    _new.append(_it)
            _ALERT_ST['seen'] = _cur
            if not _new:
                return
            _np2 = getattr(w, '_notify_prefs', None) or {}
            if not _np2.get('enabled', True):
                return
            _lv2 = 'err' if any(x.get('level') == 'err' for x in _new) else 'warn'
            if (_np2.get('dnd') and _lv2 != 'err') or _XH_MUTE.get('on'):
                return
            _msg = '；'.join('%s %s' % (x.get('name'), x.get('msg')) for x in _new)
            try:
                get_tray().showMessage('星虹告警', _msg,
                                 QSystemTrayIcon.Critical if _lv2 == 'err'
                                 else QSystemTrayIcon.Warning, 8000)
            except Exception:
                _err()
            _ALERT_ST['pops'] += 1
            _ALERT_ST['last_pop'] = dict(
                at=_now, sample_ts=_ts, msg=_msg,
                latency_ms=(int((_now - _ts) * 1000) if _ts else None),
                rtt_ms=_ALERT_ST.get('last_rtt_ms'))
        except Exception:
            _err()

    t_alert.timeout.connect(_alert_poll)

    def _hidden_alert(on):
        """R38：隐藏开始/结束时的唯一开关。隐藏首拍立刻探一次，不等 2 s。"""
        try:
            if on:
                if not t_alert.isActive():
                    t_alert.start()
                    QTimer.singleShot(0, _alert_poll)
            else:
                t_alert.stop()
        except Exception:
            _err()

    def _apply_poll_cadence(hidden):
        """R37-1：把隐藏/常规倍率一次性写到全部轮询定时器（只改间隔，不改启停）。

        隐藏只降频不停表：t1 的 since 增量、t11 的采样都靠「持续有拍」保连续，
        一旦 stop 再 start，隐藏期就成断档。启停仍由 apply_page_timers 管。
        """
        _c = _xh_cadence(hidden=hidden)
        for _nm, _tm in _poll_timers.items():
            try:
                _tm.setInterval(int(_c[_nm]))
            except Exception:
                _err()

    _perf_mark('tab6_e')
    _page_polls = {
        0: (poll_metrics, poll_sv, poll_energy),
        1: (poll_routes,),
        2: (poll_comfy, poll_outputs),
        3: (poll_vwall,),
        4: (poll_up, poll_uwall),
        5: (poll_cfg, poll_jobs_tbl, poll_health, poll_cfg_logs),
    }

    def apply_page_timers(idx):
        if 'freeze' in R10:
            # R10 诊断位：停掉全部轮询，只留后端线程，用来测「UI 轮询」这部分的总开销
            _pause_polls()
            return
        active = _page_of.get(idx, set())
        # R36-F6：t1（/api/metrics 采样）改为跨页常驻，与 t2 同等待遇。原来离开状态页
        # 就停表，采样段被整段丢掉；回页即便靠 since 补数，也会在服务端 HIST 回绕时
        # 留下断档。采样常驻后曲线数据连续，隐藏期只是不渲染（见 _metrics_done 门）。
        if idx == 0:
            drawn[0] = False      # 回页首拍强制整帧重绘，避免停在离页前的旧帧
        for name, tm in _poll_timers.items():
            if name in ('t2', 't1'):
                # R1-X01：t2 跨页基础轮询——隐藏期被停后恢复即启动，防状态芯片冻结
                if not tm.isActive():
                    tm.start()
                continue
            tm.stop()
            if name in active:
                tm.start()
        # 当前页立即刷新一次
        for f in _page_polls.get(idx, ()):
            try:
                f()
            except Exception:
                _err()
        # R20-9：切页即判装饰停/起（把 1 s 检测节拍提前到切页事件上）
        try:
            _deco_gate_check()
        except Exception:
            _err()
    _timer_hook['fn'] = apply_page_timers

    def _cadence_probe(path, secs=180.0):
        """`节拍` 自检：强制全部定时器按真源起拍，采 secs 秒后写三列报告。"""
        _CAD_MEAS.clear()
        _c = _xh_cadence()
        for _nm, _tm in _poll_timers.items():
            try:
                _tm.stop()
                _tm.setInterval(_c[_nm])
                _tm.start()
            except Exception:
                _err()

        def _finish():
            try:
                _rows = _cad_rows(_poll_timers)
                _fail = any(not _r['ok'] for _r in _rows.values())
                _d = {'rows': _rows, 'fail': _fail, 'window_s': secs,
                      'cadence': XH_CADENCE.copy()}
                with open(path, 'w', encoding='utf-8') as _f:
                    json.dump(_d, _f, ensure_ascii=False, indent=1)
            except Exception:
                _err()
            try:
                apply_page_timers(stack.currentIndex())
            except Exception:
                _err()

        QTimer.singleShot(int(secs * 1000), _finish)

    w._cadence_probe = _cadence_probe

    # R07（性能）：窗口最小化/隐藏时停掉全部轮询定时器，恢复可见按当前页续跑。
    # 改前最小化 30 s 实测仍占 14.06% CPU——挂在后台还在每 2–15 s 打接口、触发玻璃卡重绘。
    def _pause_polls():
        for _tm in _poll_timers.values():
            _tm.stop()

    class _WinVisFilter(QObject):
        def eventFilter(self, _o, _e):
            if _e.type() == QEvent.WindowStateChange:
                if w.isMinimized() or not w.isVisible():
                    _pause_polls()
                    _deco_set_paused(True, 'min')
                else:
                    apply_page_timers(stack.currentIndex())
                    # R20-9：恢复即补一帧；apply_page_timers 已立即发请求（事件驱动，不等 2s 拍）
                    _deco_set_paused(False, 'min')
                    _deco_resume_frame()
                    if XH_COST:
                        _cost('deco:evt:restore:min',
                              (time.perf_counter(), time.thread_time()))
            return False

    _win_vis = _WinVisFilter(w)
    w._win_vis_filter = _win_vis
    w.installEventFilter(_win_vis)

    # ── R20-9：遮挡 / 切页离开判定（1 Hz 轮询） ────────────────────────────
    # 遮挡没有可订阅的事件 → 1 s 判一次「窗口中心点属于谁的根窗口」+ IsWindowVisible。
    # 恢复也是这 1 s 检出 → 立即 start() 并补画一帧（依据=检测节拍，不是数据拍）。
    def _win_covered():
        try:
            h = int(w.winId())
            u = ctypes.windll.user32
            if not u.IsWindowVisible(h):
                return True
            rc = ctypes.wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(rc))
            pt = ctypes.wintypes.POINT()
            pt.x = (rc.left + rc.right) // 2
            pt.y = (rc.top + rc.bottom) // 2
            hit = u.WindowFromPoint(pt)
            if not hit:
                return True
            return int(u.GetAncestor(hit, 2)) != h    # GA_ROOT
        except Exception:
            _err()
            return False

    def _deco_page_of(_dw):
        """装饰控件所属页面索引；不在 stack 内（导航栏品牌点）返回 -1。"""
        _p = _dw.parentWidget()
        _top = None
        while _p is not None:
            if _p is stack:
                return stack.indexOf(_top) if _top is not None else -1
            _top = _p
            _p = _p.parentWidget()
        return -1

    def _deco_gate_check():
        try:
            if 'nodecostop' in R10:        # 诊断位：保留旧行为做对照
                return
            if w.isMinimized() or not w.isVisible():
                if not _DECO_PAUSED[0]:
                    _deco_set_paused(True, 'min')
                return
            if _win_covered():
                if not _DECO_PAUSED[0]:
                    _deco_set_paused(True, 'cover')
                    # 遮挡期请求照发（XH_COST 如实计数），但不再往被盖住的窗口上刷帧：
                    # 冻结整窗更新，恢复时一次 enable+update 补最新值。
                    try:
                        w.setUpdatesEnabled(False)
                    except Exception:
                        _err()
                return
            # 切页离开 = 当前页不是任何装饰控件的宿主页（按页归属判定，不按控件可见性：
            # 窄窗 compact 会隐藏条，但页仍是装饰页，不能把导航栏呼吸点一起冻住）
            _idx = stack.currentIndex()
            _avail = False
            for _dw in _deco_deco_widgets():
                try:
                    if _deco_page_of(_dw) == _idx:
                        _avail = True
                        break
                except RuntimeError:
                    continue
            if not _avail:
                # 切页离开：当前页没有任何装饰消费者（导航栏品牌点不算内容装饰）
                if XH_COST:
                    try:
                        _now = time.time()
                        if _now - float(_DECO.get('_away_log_t') or 0) >= 5.0:
                            _DECO['_away_log_t'] = _now
                            _pgs = []
                            for _dw in _deco_deco_widgets():
                                _pgs.append('%s@%d' % (type(_dw).__name__,
                                                       _deco_page_of(_dw)))
                            _cost('deco:away:page=%d:n=%d:%s'
                                  % (_idx, len(_pgs), '|'.join(_pgs)),
                                  (time.perf_counter(), time.thread_time()))
                    except Exception:
                        _err()
                if not _DECO_PAUSED[0]:
                    _deco_set_paused(True, 'away')
                return
            if _DECO_PAUSED[0]:
                _why_old = _DECO_WHY[0] or '?'
                if _why_old == 'cover':
                    try:
                        w.setUpdatesEnabled(True)
                        w.update()
                    except Exception:
                        _err()
                _deco_set_paused(False, _why_old)
                _deco_resume_frame()
        except Exception:
            _err()

    _deco_gate = QTimer(w)
    _deco_gate.setInterval(1000)
    _deco_gate.timeout.connect(_deco_gate_check)
    _deco_gate.start()
    w._deco_gate = _deco_gate

    return {'timers': _poll_timers, 't2': t2, 'poll_state': poll_state,
            'apply_page_timers': apply_page_timers,
            'cadence': _apply_poll_cadence, 'hidden_alert': _hidden_alert,
            't_alert': t_alert, 'alert_state': _ALERT_ST}
