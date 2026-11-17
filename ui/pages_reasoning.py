# -*- coding: utf-8 -*-
"""UI 页面：Tab「推理路线」（C1-B2 分层重构第 3 批）。

原位置：xinghong_launcher.py build_ui() 内 8362–8754 行，整段原样搬出。
分层：ui（与 xinghong_launcher.py 同层），只依赖 PySide6 / 标准库；
launcher 的控件工厂与私有工具全部由调用方显式传参，禁止 import launcher。
"""
import json

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

__all__ = ['build_page_reasoning']

def build_page_reasoning(*, stack, w, api, qwait, err, perf_mark, nav_icon, state_dot,
                         UI_C, GlassCard, TwoColList, KVModelCard, LogDialog,
                         get_cf_start_b, get_cf_stop_b, get_btn_interrupt,
                         get_gen_btn, get_vgen_btn, get_up_btn,
                         get_gen_lbl, get_vgen_lbl):
    """Tab「推理路线」页面构建（C1-B2 第 3 批，从 build_ui 原样搬出）。

    依赖全部显式传入。跨页按钮（生图/视频/超分/中断）在本页构建时尚未创建，
    以 get_* 取值函数传入——与原闭包一样在轮询回调执行时才求值。
    返回值只暴露 launcher 后续确实引用的句柄（刷新注册表 / 退场清理 / _probe_status）。
    """
    perf_mark('tab1_b')
    rt = QWidget()
    rv = QVBoxLayout(rt)
    rv.setContentsMargins(4, 8, 4, 4)
    leftcol = QVBoxLayout()
    g_kv = GlassCard('KVMem 路线 · 端口 1234')
    g_kv.set_glyph('routes')
    kvwrap = QVBoxLayout(g_kv)
    kvwrap.setContentsMargins(12, 12, 12, 8)
    kvwrap.setSpacing(8)
    # 头部：状态 chip + 运行日志按钮 + 互斥提示
    kvhead = QHBoxLayout()
    kv_chip_lbl = QLabel('—')
    kv_chip_lbl.setObjectName('sub')
    # R08（P1-2）：断流文案较长，原来单行被右侧按钮切掉半句（做法读不全）。
    # 允许换行并留出与按钮的间距。
    kv_chip_lbl.setWordWrap(True)
    kv_log_b = QPushButton('运行日志 / 进度')
    kv_log_b.setIcon(nav_icon('list', 14, UI_C['INK_1']))
    kv_log_b.setIconSize(QSize(14, 14))
    kvhead.setSpacing(8)
    kvhead.addWidget(kv_chip_lbl, 1)
    kvhead.addWidget(kv_log_b)
    kvwrap.addLayout(kvhead)
    kv_hint = QLabel('启动一个会自动停掉 LM Studio，两者互斥')
    kv_hint.setObjectName('sub')
    kvwrap.addWidget(kv_hint)
    # 两张模型卡：flash64 / qwen9bq4 左右各半
    kvcards_row = QHBoxLayout()
    kvcards_row.setSpacing(8)
    kv_cards = {}
    # kv_opts 从 /api/state 读取，先给空，poll 时重建一次
    kv_cards['flash64'] = KVModelCard('flash64', 'C1', [])
    kv_cards['qwen9bq4'] = KVModelCard('qwen9bq4', 'C2', [])
    kvcards_row.addWidget(kv_cards['flash64'], 1)
    kvcards_row.addWidget(kv_cards['qwen9bq4'], 1)
    kvwrap.addLayout(kvcards_row)
    leftcol.addWidget(g_kv)
    # LM Studio：模型以可选列表展示（XH-8），保活代理并入本卡顶部（XH-7）
    g_lms = GlassCard('LM Studio 路线 · 与 KVMem 互斥')
    g_lms.set_glyph('gear')
    lmsv = QVBoxLayout(g_lms)
    lmsv.setContentsMargins(12, 12, 12, 8)
    lmsv.setSpacing(8)
    # R29：服务状态行补 10px 状态点（与 KVMem 头的状态 chip 同口径）
    lms_lbl = QLabel('—')
    lms_state_dot = QLabel()
    lms_state_dot.setFixedSize(10, 10)
    lms_state_dot.setPixmap(state_dot(UI_C['IDLE'], 10))
    lms_state_row = QWidget()
    _lsr = QHBoxLayout(lms_state_row)
    _lsr.setContentsMargins(0, 0, 0, 0)
    _lsr.setSpacing(8)
    _lsr.addWidget(lms_state_dot)
    _lsr.addWidget(lms_lbl, 1)
    lmsv.addWidget(lms_state_row)

    def _set_lms_state(txt, col):
        lms_lbl.setText(txt)
        lms_state_dot.setPixmap(state_dot(col, 10))
    # 保活代理：一行小控件并入（不再单独占卡）
    px_row = QHBoxLayout()
    px_lbl = QLabel('保活代理 —')
    px_lbl.setObjectName('sub')
    px_start_b = QPushButton('启动代理')
    px_stop_b = QPushButton('停止代理')
    # R29：按钮族补 14px 线稿（与 R24/R28 同口径，尺寸/文案/事件不变）
    for _b, _g in ((px_start_b, 'power'), (px_stop_b, 'stop')):
        _b.setIcon(nav_icon(_g, 14, UI_C['INK_1']))
        _b.setIconSize(QSize(14, 14))
    px_row.addWidget(px_lbl, 1)
    px_row.addWidget(px_start_b)
    px_row.addWidget(px_stop_b)
    lmsv.addLayout(px_row)
    # 服务启停
    lms_start_b = QPushButton('启动服务')
    lms_stop_b = QPushButton('停止服务')
    bench_b = QPushButton('快速测速')
    for _b, _g in ((lms_start_b, 'power'), (lms_stop_b, 'stop'), (bench_b, 'scan')):
        _b.setIcon(nav_icon(_g, 14, UI_C['INK_1']))
        _b.setIconSize(QSize(14, 14))
    srv_row = QHBoxLayout()
    srv_row.addWidget(lms_start_b)
    srv_row.addWidget(lms_stop_b)
    srv_row.addWidget(bench_b)
    lmsv.addLayout(srv_row)
    # 模型列表：每行可单选，选中后点「加载选中模型」
    # R20-10c：改两列网格（IconMode 2×3），6 条一屏全显示、内部无滚动条
    lms_list = TwoColList()
    lms_list.setMinimumHeight(88)  # R017：压缩页高，推理路线整页免滚动
    lmsv.addWidget(lms_list, 1)
    lms_load_b = QPushButton('加载选中模型')
    lms_unload_b = QPushButton('卸载模型')
    for _b, _g in ((lms_load_b, 'folder'), (lms_unload_b, 'eject')):
        _b.setIcon(nav_icon(_g, 14, UI_C['INK_1']))
        _b.setIconSize(QSize(14, 14))
    load_row = QHBoxLayout()
    load_row.addWidget(lms_load_b)
    load_row.addWidget(lms_unload_b)
    lmsv.addLayout(load_row)
    leftcol.addWidget(g_lms)
    rv.addLayout(leftcol, 1)
    # 「最近任务」区块已删除（XH-6）；R009：不套外层滚动，模型列表内部滚动
    # R017（领导）：推理路线内容超高被压缩致元素重叠——改 AsNeeded 滚动（正常窗口不出现）
    stack.addWidget(rt)

    lms_models = []
    _rt_err = [0]   # R07：/api/state 连续失败计数（推理路线断流提示）
    _rt_ok = [0]    # R07：是否成功拿到过模型配置（区分冷启动与真断流）

    def poll_routes():
        qwait(lambda: api('GET', '/api/state'), _routes_done, key='state')

    def _routes_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                _rt_err[0] += 1
                if _rt_ok[0] and _rt_err[0] >= 2:
                    kv_chip_lbl.setText(
                        '断流：连续 %d 次读不到面板服务（/api/state 无响应）。'
                        '请到「状态」页确认托管服务是否在线；仍无数据可重启面板后按 F5 刷新'
                        % _rt_err[0])
                    _set_lms_state('服务状态未知（面板无响应）', UI_C['ERR'])
                elif not _rt_ok[0]:
                    kv_chip_lbl.setText('正在等待面板服务就绪：还没有拿到模型配置，请稍候…')
                return
            _rt_err[0] = 0
            _rt_ok[0] = 1
            _code, d = r
            if not isinstance(d, dict):
                kv_chip_lbl.setText('断流：面板返回了无法解析的响应（不是 JSON）。'
                                    '常见原因是 8899 端口被其他程序占用；'
                                    '请关闭占用程序后重启面板，或按 F5 重试')
                _set_lms_state('服务状态未知（面板响应异常）', UI_C['ERR'])
                return
            profiles = d.get('profiles', [])
            kv_opts = list(d.get('kvOpts', []) or [])
            # 首次拿到 kvOpts 时重建两个卡的 KV-K / KV-V 下拉
            for card in kv_cards.values():
                _ctk_w = card.widgets['ctk']
                cur_opts = [_ctk_w.itemText(i) for i in range(_ctk_w.count())]
                if kv_opts and kv_opts != cur_opts:
                    for k in ('ctk', 'ctv'):
                        card.widgets[k].clear()
                        card.widgets[k].addItems(kv_opts)
                # R07：量化项为空时下拉是空白的，必须说明原因
                for k in ('ctk', 'ctv'):
                    card.widgets[k].setToolTip(
                        '' if kv_opts else '量化选项未就绪（面板未返回 kvOpts），按 F5 刷新')
            # 按 profile 填充对应卡
            _got = set()
            for prof in profiles:
                pid = prof.get('id')
                if pid in kv_cards:
                    kv_cards[pid].populate(prof)
                    _got.add(pid)
            # R07：面板没返回的模型卡不能只剩一排空白下拉
            for _pid, _card in kv_cards.items():
                if _pid not in _got:
                    _card.name_lbl.setText('未配置：面板未返回该模型')
                    _card.miss_lbl.setText('')
                    _card.widgets['ctk'].setToolTip('未配置（面板未返回该模型），按 F5 刷新')
                    _card.widgets['ctv'].setToolTip('未配置（面板未返回该模型），按 F5 刷新')
            # 头部状态 chip
            kv = d.get('kv', {})
            if kv.get('ready'):
                kv_chip = UI_C['OK']
                kv_txt = 'KVMem 运行中 · 就绪'
            elif kv.get('alive'):
                kv_chip = UI_C['WARN_2']
                kv_txt = 'KVMem 启动中…'
            else:
                kv_chip = UI_C['INK_2']
                kv_txt = 'KVMem 已停止'
            kv_chip_lbl.setText(
                '<span style="color:%s">●</span> %s' % (kv_chip, kv_txt))
            lms_d = d.get('lms', {})
            _lms_run = bool(lms_d.get('running'))
            _set_lms_state('服务 %s ｜ 已加载 %s' % (
                '运行' if _lms_run else '停止',
                '、'.join(lms_d.get('loaded', []) or ['无'])),
                UI_C['OK'] if _lms_run else UI_C['IDLE'])
            lst = lms_d.get('list') or ''
            models = [ln.split()[0] for ln in lst.splitlines()
                      if ln and not ln.startswith(('You have', 'LLM', '-')) and ' ' in ln]
            if models and models != lms_models:
                lms_models[:] = models
                lms_list.clear()
                # 每行显示模型名；已加载的模型加标记
                loaded = lms_d.get('loaded', []) or []
                for m in models:
                    tag = '  （已加载）' if m in loaded else ''
                    lms_list.addItem(m + tag)
            elif not models and lms_models:
                # R15-X16：LMS 空态——列表清空并给出原因提示，不再留白
                lms_models[:] = []
                lms_list.clear()
                lms_list.addItem('（未检测到模型，请确认 LM Studio 已扫描模型目录）')
            # X2：运行态按钮门控（未运行→停止/卸载/释放置灰；无 busy→中断置灰）
            _lms_run = bool(lms_d.get('running'))
            _lms_loaded = bool(lms_d.get('loaded'))
            lms_start_b.setEnabled(not _lms_run and not _lms_loaded)
            lms_stop_b.setEnabled(bool(_lms_run or _lms_loaded))
            lms_load_b.setEnabled(bool(_lms_run and not _lms_loaded))
            lms_unload_b.setEnabled(bool(_lms_loaded))
            px = d.get('proxy', {})
            _px_on = bool(px.get('alive') or px.get('running'))
            px_start_b.setEnabled(not _px_on)
            px_stop_b.setEnabled(bool(_px_on))
            cf = d.get('comfy', {})
            _cf_on = bool(cf.get('alive') or cf.get('ready'))
            _cf_busy = bool(cf.get('busy'))
            get_cf_start_b().setEnabled(not _cf_on)
            get_cf_stop_b().setEnabled(bool(_cf_on))
            try:
                get_gen_btn().setEnabled(bool(_cf_on and not _cf_busy))
                get_vgen_btn().setEnabled(bool(_cf_on and not _cf_busy))
                get_up_btn().setEnabled(bool(_cf_on and not _cf_busy))
                get_btn_interrupt().setEnabled(bool(_cf_on and _cf_busy))
                # 按钮灰着必须说明原因：ComfyUI 离线时生图/视频页原来只有一个灰按钮，
                # 用户会以为程序坏了（第六轮真机走查）。
                _off_hint = 'ComfyUI 离线：先到「状态」页启动，等变在线再回来'
                if _cf_on:
                    _btn_tip = ('ComfyUI 正在执行其他任务，完成后再试' if _cf_busy else '')
                else:
                    _btn_tip = _off_hint
                for _b in (get_gen_btn(), get_vgen_btn(), get_up_btn()):
                    _b.setToolTip(_btn_tip)
                for _l in (get_gen_lbl(), get_vgen_lbl()):
                    _cur = (_l.text() or '').strip()
                    if not _cf_on and not _cur:
                        _l.setText(_off_hint)
                    elif _cf_on and _cur == _off_hint:
                        _l.setText('')
            except Exception:
                err()
        except Exception:
            err()

    # poll_jobs 已随「最近任务」区块删除（XH-6；/api/jobs 本就返回 not found）

    # 启停/卸载类端点在后端可能持续数十秒（lms server start 最长 60 s、停止要杀进程树），
    # 客户端默认 8 s 会在后端仍在执行时提前判超时，用户看到"失败"再点就撞上"正在启动"。
    _DO_TIMEOUT = {'/api/lms/start': 90, '/api/lms/stop': 90, '/api/lms/unload': 120,
                   '/api/proxy/start': 45, '/api/proxy/stop': 45}

    def do(path):
        # R007（D-05）：服务启停/加载/代理等按钮原在 UI 线程同步 POST（8s 冻结），改后台
        def work():
            try:
                return api('POST', path, {}, timeout=_DO_TIMEOUT.get(path, 8))
            except Exception as e:
                return (0, {'msg': str(e)})

        def done(r):
            _c, d = r
            if isinstance(d, dict) and d.get('msg'):
                QMessageBox.information(w, '结果', str(d.get('msg')))

        qwait(work, done)

    def do_bench():
        bench_b.setEnabled(True)
        bench_b.setText('测速中…')
        bench_b.setEnabled(False)

        def done(r):
            bench_b.setEnabled(True)
            bench_b.setText('快速测速')
            if isinstance(r, tuple):
                QMessageBox.information(w, '测速', str(r))
                return
            QMessageBox.information(w, '测速', json.dumps(r, ensure_ascii=False)[:600])

        qwait(lambda: api('POST', '/api/bench', {}, timeout=120), done)

    def wire_kv_card(card):
        def save_card():
            # X2：save_card 展示后端 msg（不再静默丢结果）
            def work():
                try:
                    return api('POST', '/api/save', card.values(), timeout=30)
                except Exception as e:
                    return (0, {'msg': str(e)})

            def done(r):
                card.save_b.setEnabled(True)
                card.save_b.setText('保存参数')
                _c, d = r
                if isinstance(d, dict) and d.get('msg'):
                    QMessageBox.information(w, '保存结果', str(d.get('msg')))

            card.save_b.setEnabled(False)
            card.save_b.setText('保存中…')
            qwait(work, done)

        def start_card():
            # 与网页 saveProf(startAfter) 一致：先保存参数，再启动
            card.start_b.setEnabled(False)
            card.start_b.setText('启动中…')

            def work():
                try:
                    api('POST', '/api/save', card.values(), timeout=30)
                    return api('POST', '/api/kv/start', {'id': card.pid}, timeout=60)
                except Exception as e:
                    return (0, {'msg': str(e)})

            def done(r):
                # X2：启动失败不再卡"启动中"，恢复按钮并如实报错
                card.start_b.setEnabled(True)
                card.start_b.setText('启动此模型')
                _c, d = r
                if isinstance(d, dict) and d.get('msg'):
                    QMessageBox.information(w, '启动结果', str(d.get('msg')))

            qwait(work, done)

        def stop_card():
            card.stop_b.setEnabled(False)
            card.stop_b.setText('停止中…')

            def work():
                try:
                    return api('POST', '/api/kv/stop', {}, timeout=30)
                except Exception as e:
                    return (0, {'msg': str(e)})

            def done(r):
                card.stop_b.setEnabled(True)
                card.stop_b.setText('停止')
                _c, d = r
                if isinstance(d, dict) and d.get('msg'):
                    QMessageBox.information(w, '停止结果', str(d.get('msg')))

            qwait(work, done)

        card.start_b.clicked.connect(start_card)
        card.save_b.clicked.connect(save_card)
        card.stop_b.clicked.connect(stop_card)

    for _card in kv_cards.values():
        wire_kv_card(_card)

    lms_start_b.clicked.connect(lambda: do('/api/lms/start'))
    lms_stop_b.clicked.connect(lambda: do('/api/lms/stop'))
    def lms_load_selected():
        it = lms_list.currentItem()
        if it is None:
            QMessageBox.information(w, '提示', '请先在列表中选中一个模型')
            return
        m = it.text().split('  （已加载）')[0].strip()
        # X11：模型加载（可能数分钟）移入后台线程，主线程置"加载中…"不再冻结
        lms_load_b.setEnabled(False)
        lms_load_b.setText('加载中…')

        def work():
            try:
                return api('POST', '/api/lms/load', {'model': m}, timeout=600)
            except Exception as e:
                return (0, {'msg': str(e)})

        def done(r):
            lms_load_b.setEnabled(True)
            lms_load_b.setText('加载选中模型')
            _c, d = r
            if isinstance(d, dict) and d.get('msg'):
                QMessageBox.information(w, '加载结果', str(d.get('msg')))

        qwait(work, done)
    lms_load_b.clicked.connect(lms_load_selected)
    lms_unload_b.clicked.connect(lambda: do('/api/lms/unload'))
    px_start_b.clicked.connect(lambda: do('/api/proxy/start'))
    px_stop_b.clicked.connect(lambda: do('/api/proxy/stop'))
    bench_b.clicked.connect(do_bench)

    def open_kv_log():
        dlg = LogDialog(w, 'KVMem 运行日志 / 进度', '/api/log/kv', api)
        dlg.exec()

    kv_log_b.clicked.connect(open_kv_log)
    # R07 自检探针：状态页/推理路线页的「无数据、断流、断流后恢复」走查入口。
    # 只存引用；正常使用不受影响。

    perf_mark('tab1_e')

    return {
        'poll_routes': poll_routes,
        'routes_done': _routes_done,
        'kv_chip_lbl': kv_chip_lbl,
        'lms_lbl': lms_lbl,
        'kv_cards': kv_cards,
    }
