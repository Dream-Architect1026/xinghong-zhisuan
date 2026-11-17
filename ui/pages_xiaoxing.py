# -*- coding: utf-8 -*-
"""UI 页面：Tab「小星（本地模型助手）」（C1-B2 第 7 批）。

原位置：xinghong_launcher.py build_ui() L7655–8249（595 行），整段原样搬出：
  页内头（状态短句 + 徽章）/ 五个子页（对话 / Agent 搭建 / AI 配置 / 数据授权 /
  隐私与日志）/ _xx_refresh 门禁与徽章唯一刷新入口 / 保存与同意回调。

分层：ui 层，只依赖 PySide6 / ux_core（同为 ui 层）；launcher 的控件工厂（GlassCard）、
主题令牌（UI_C）、助手线程与模块级助手函数一律显式传参，禁止反向 import launcher。

单实例硬约束（B3 收口 Prefs 的前提）：
  本页**不构造** ux.Prefs / ux.SecretStore，全部用配置页建好的 adv_prefs / adv_secrets；
  页内别名 xx_prefs / xx_keys 只是同一对象的另一个名字。页面模块里只允许
  ui/pages_config.py 那一处构造（tools/check_prefs_keys.py 守这条线）。
  启动期 main 另有一处只读 ux.Prefs（R023 动画/主题初值），与页面句柄无关、不参与本页。

QSettings/密钥读写（全部走 adv/ 前缀，默认值见 docs/switches.md「B2-7 小星页 QSettings 表」）：
  aiEndpoint / aiModel / aiEnabled；aiAuthSession / aiAuthCurve / aiAuthDevice / aiAuthEvent；
  Key 只经 SecretStore（DPAPI）——xx_keys.load_key / save_key。
  隐私同意走 ux.Prefs 的 legal/accepted（consent_ok / consent_at）。

句柄：w._xx_refresh / w._xx_tabs 由本页自设（app/diag_cmds.py 的 xxtab、xxtabonly 读）；
xx_ask_consent 作为唯一返回值交给 launcher（main() 600 ms 定时器读）。
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit,
                               QPushButton, QStackedWidget, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

__all__ = ['build_page_xiaoxing']


def build_page_xiaoxing(*, stack, w, ux, _err, _perf_mark,
                        adv_prefs, adv_secrets, GlassCard, UI_C,
                        XX_AGENT_TOOLS, XxChatWorker, XxAgentWorker,
                        xx_agent_load, xx_agent_save,
                        xx_secret_register, xx_session_write, _consent_log):
    """Tab「小星」页面构建（C1-B2 第 7 批，从 build_ui 原样搬出）。

    参数名与 build_ui 内原标识符逐字一致（机械等价可逐行比对）；
    adv_prefs / adv_secrets 必须是配置页那一份实例，本函数内不得再构造。
    """

    # ===================== 小星（本地模型助手） =====================
    _perf_mark('tab6_b')
    # 命名口径：入口、页头、提示语一律「小星」，不出现「AI」；
    # 技术配置项（AI 配置 / AI 端点 / 模型名）保留「AI」，那里描述的是模型与接口。
    xx_prefs = adv_prefs            # 与进阶配置同一条 QSettings('X.H','XingHongPanel')
    xx_keys = adv_secrets           # Key 只走 SecretStore（DPAPI），不进明文配置

    xp = QWidget()
    xv = QVBoxLayout(xp)
    xv.setContentsMargins(16, 12, 16, 12)
    xv.setSpacing(10)
    # 48 px 页内头：左侧状态短句走 18 px/700 标题位（不另加标题），右侧徽章 11 px
    _xbadge = QLabel('')
    _xbadge.setAlignment(Qt.AlignCenter)
    _xhh, _xhrefs = ux.page_head(text='', badge=_xbadge)
    _xstat = _xhrefs['title']
    xv.addWidget(_xhh)

    _xtabs = QTabWidget()
    _xtabs.setDocumentMode(True)
    xv.addWidget(_xtabs, 1)

    # 卡片统一版式：圆角 10、1 px 描边 rgba(120,190,255,0.14)、无阴影
    _XX_CARD_QSS = ('QGroupBox#xxcard{background:rgba(255,255,255,0.035);'
                    ' border:1px solid rgba(120,190,255,0.14); border-radius:10px;}')

    def _xx_card_style(_c):
        _c.setObjectName('xxcard')
        _c.setStyleSheet(_XX_CARD_QSS)
        _c._radius_override = 10
        _c._border_override = QColor(120, 190, 255, 36)
        _c._no_shadow = True
        _c._invalidate()
        return _c

    def _xx_gate_page():
        """门禁/占位页：一张卡 + 三句（缺哪个开关 / 在哪开 / 开完会有什么）。"""
        _pg = QWidget()
        _bv = QVBoxLayout(_pg)
        _bv.setContentsMargins(16, 12, 16, 12)
        _bv.setSpacing(10)
        _card = _xx_card_style(GlassCard('小星'))
        _gv = QVBoxLayout(_card)
        _gv.setContentsMargins(16, 14, 16, 14)
        _gv.setSpacing(10)
        _lines = []
        for _ in range(3):
            _lb = QLabel('')
            _lb.setWordWrap(True)
            _gv.addWidget(_lb)
            _lines.append(_lb)
        _gv.addStretch(1)
        _bv.addWidget(_card)
        return _pg, _card, _lines

    _pg_gate_chat, _chat_card, _chat_lines = _xx_gate_page()
    # P2：对话区（堆叠：0=门禁卡，1=真实对话；未开启时整页仍是说明卡，入口不藏）
    _xx_msgs = []
    _chat_busy = {'v': False}
    _chat_worker = {'w': None}
    _pg_chat = QStackedWidget()
    _pg_chat.layout().setContentsMargins(0, 0, 0, 0)
    _pg_chat.layout().setSpacing(0)     # 边距只由内层页给（16,12,16,12），外层不叠加
    _pg_chat.addWidget(_pg_gate_chat)
    _chat_body = QWidget()
    _cbv = QVBoxLayout(_chat_body)
    _cbv.setContentsMargins(16, 12, 16, 12)
    _cbv.setSpacing(10)
    _chat_hist = QTextBrowser()
    _chat_hist.setObjectName('chatHist')
    _chat_hist.setStyleSheet(ux.DOC_BROWSER_QSS)
    _chat_hist.setHtml('<div style="font-size:13px;color:%s;">小星只在本机运行：'
                       '消息仅发送到「AI 配置」里填写的端点，不会上传到别处。</div>'
                       % ux.TOKENS['text_sub'])
    _cbv.addWidget(_chat_hist, 1)
    _chat_in = QPlainTextEdit()
    _chat_in.setPlaceholderText('问点什么…（Ctrl+Enter 发送）')
    _chat_in.setFixedHeight(72)
    _cbv.addWidget(_chat_in)
    _crow = QHBoxLayout()
    _chat_hint = QLabel('')
    _chat_hint.setObjectName('sub')
    _chat_hint.setStyleSheet('font-size:11px;')
    _chat_stop = QPushButton('停止')
    _chat_stop.setMinimumSize(96, 32)
    _chat_stop.setFixedHeight(32)
    _chat_stop.setEnabled(False)
    _chat_send = QPushButton('发送')
    _chat_send.setMinimumSize(120, 32)
    _chat_send.setFixedHeight(32)
    _chat_send.setStyleSheet(
        'QPushButton{background:rgba(56,226,255,0.18);'
        'border:1px solid rgba(56,226,255,0.45); border-radius:9px;'
        'padding:0 18px; font-weight:600; color:%s;}'
        'QPushButton:hover{background:rgba(56,226,255,0.28);}'
        'QPushButton:disabled{color:#64748b;background:rgba(255,255,255,0.05);}' % ux.TOKENS['text'])
    _crow.addWidget(_chat_hint)
    _crow.addStretch(1)
    _crow.addWidget(_chat_stop)
    _crow.addWidget(_chat_send)
    # 页脚行统一 56 px，按钮右对齐（次 96×32 / 主 120×32）
    _chat_foot = QWidget()
    _chat_foot.setObjectName('pagefoot')   # R247：主题态页脚真实底色
    _chat_foot.setFixedHeight(56)
    _crow.setContentsMargins(0, 0, 0, 0)
    _crow.setSpacing(10)
    _chat_foot.setLayout(_crow)
    _cbv.addWidget(_chat_foot)
    _pg_chat.addWidget(_chat_body)
    _xtabs.addTab(_pg_chat, '对话')

    def _xx_escape(_s):
        import html as _h
        return _h.escape(str(_s or '')).replace('\n', '<br>')

    def _xx_sys_prompt():
        _on = [k for k, v in (('会话统计', 'aiAuthSession'), ('采样窗口', 'aiAuthCurve'),
                              ('设备识别', 'aiAuthDevice'), ('事件原因', 'aiAuthEvent'))
               if bool(xx_prefs.get(v, False))]
        return ('你是星虹（XingHong）的本地助手「小星」。只围绕本机算力、设备与任务回答；'
                '不能发短信、不能打电话、不能修改应用设置、不能主动推送。'
                '已获授权的本地数据类别：%s；未列出的类别不要假装能读。'
                % ('、'.join(_on) if _on else '无'))

    def _xx_chat_write(_role, _text, _meta=None):
        try:
            xx_session_write(_role, _text, _meta)
        except Exception:
            _err()

    def _xx_chat_idle(_note=''):
        _chat_busy['v'] = False
        _chat_send.setEnabled(True)
        _chat_stop.setEnabled(False)
        if _note:
            _chat_hint.setText(_note)

    def _xx_chat_done(_res):
        try:
            if _res.get('ok'):
                _txt = _res.get('text') or ''
                _xx_msgs.append({'role': 'assistant', 'content': _txt})
                _chat_hist.append('<div style="margin:8px 0;color:%s;">小星：%s</div>'
                                  % (ux.TOKENS['text'], _xx_escape(_txt)))
                _xx_chat_write('assistant', _txt,
                               {'model': xx_prefs.get('aiModel', ''),
                                'ms': _res.get('ms', 0)})
                _xx_chat_idle('已回复（%d ms）' % int(_res.get('ms') or 0))
            else:
                _err_txt = _res.get('err') or '未知错误'
                if _err_txt == '已取消':
                    _chat_hist.append('<div style="margin:8px 0;color:%s;">已取消（本次回复未保存）。</div>'
                                      % ux.TOKENS['text_sub'])
                    _xx_chat_idle('已取消')
                    return
                _chat_hist.append('<div style="margin:8px 0;color:%s;">请求失败：%s</div>'
                                  % (ux.TOKENS['err'], _xx_escape(_err_txt)))
                _xx_chat_idle('失败（%d ms）：%s' % (int(_res.get('ms') or 0), _err_txt[:60]))
        except Exception:
            _err()
            _xx_chat_idle('')

    def _xx_chat_send():
        if _chat_busy['v'] or _xx_state() != 'on':
            return
        _txt = (_chat_in.toPlainText() or '').strip()
        if not _txt:
            return
        _ep = str(xx_prefs.get('aiEndpoint', '') or '').strip().rstrip('/')
        if not _ep:
            _chat_hint.setText('先在「AI 配置」填端点')
            return
        if not _ep.endswith('/chat/completions'):
            _ep += '/chat/completions'
        _chat_in.clear()
        _xx_msgs.append({'role': 'user', 'content': _txt})
        _chat_hist.append('<div style="margin:8px 0;color:%s;">你：%s</div>'
                          % (ux.TOKENS['text_sub'], _xx_escape(_txt)))
        _xx_chat_write('user', _txt)
        _key = ''
        try:
            _key = xx_keys.load_key() or ''
        except Exception:
            _key = ''
        xx_secret_register(_key)
        _msgs = [{'role': 'system', 'content': _xx_sys_prompt()}] + _xx_msgs[-20:]
        _w = XxChatWorker(_ep, _key, str(xx_prefs.get('aiModel', '') or ''), _msgs, 30, parent=w)
        _w.done.connect(_xx_chat_done)
        _chat_worker['w'] = _w
        _chat_busy['v'] = True
        _chat_send.setEnabled(False)
        _chat_stop.setEnabled(True)
        _chat_hint.setText('请求中…（30 s 硬超时，可点停止）')
        _w.start()

    def _xx_chat_stop():
        _w = _chat_worker['w']
        if _w is not None:
            _w.stop()
            _chat_hint.setText('正在中断…')

    class _ChatKey(QObject):
        """只拦 Ctrl+Enter：回车仍走 QPlainTextEdit 的换行默认行为。"""

        def eventFilter(self, _o, _e):
            if (_e.type() == QEvent.KeyPress
                    and _e.key() in (Qt.Key_Return, Qt.Key_Enter)
                    and bool(_e.modifiers() & Qt.ControlModifier)):
                _xx_chat_send()
                return True
            return False

    _chat_key = _ChatKey(w)
    _chat_in.installEventFilter(_chat_key)
    _chat_send.clicked.connect(_xx_chat_send)
    _chat_stop.clicked.connect(_xx_chat_stop)
    # C1-B2-7：原 `w._xx_chat_input / w._xx_chat_key` 两处写入全仓无读取
    # （_ChatKey 构造时已 parent=w，生命周期不依赖该属性）——与 B2-6 的
    # chart_win 同类处理：删，不把「写了没人读」的债带到 B3。
    # ---------------- Agent 搭建：步骤执行最小闭环（本地工具，非自主规划）----
    _pg_agent_gate, _agent_card, _agent_lines = _xx_gate_page()
    _ag_steps = xx_agent_load()
    _ag_busy = {'v': False}
    _ag_worker = {'w': None}
    _pg_agent = QStackedWidget()
    _pg_agent.layout().setContentsMargins(0, 0, 0, 0)
    _pg_agent.layout().setSpacing(0)
    _pg_agent.addWidget(_pg_agent_gate)

    _ag_body = QWidget()
    _agv = QVBoxLayout(_ag_body)
    _agv.setContentsMargins(16, 12, 16, 12)
    _agv.setSpacing(10)

    _ag_tip = QLabel('按步骤执行本地工具（非自主规划）：定义步骤 → 运行 → 看执行记录；'
                     '每步 10 s 超时，失败写明原因。')
    _ag_tip.setWordWrap(True)
    _ag_tip.setObjectName('sub')
    _ag_tip.setStyleSheet('font-size:11px;')
    _agv.addWidget(_ag_tip)

    _ag_row = QHBoxLayout()
    _ag_row.setSpacing(8)
    _ag_tool = QComboBox()
    for _n, _dsc in XX_AGENT_TOOLS:
        _ag_tool.addItem(_n)
        _ag_tool.setItemData(_ag_tool.count() - 1, _dsc, Qt.ToolTipRole)
    _ag_arg = QLineEdit()
    _ag_arg.setPlaceholderText('参数（可选，如 5）')
    _ag_add = QPushButton('添加步骤')
    _ag_add.setMinimumSize(120, 32)
    _ag_row.addWidget(_ag_tool, 1)
    _ag_row.addWidget(_ag_arg, 1)
    _ag_row.addWidget(_ag_add)
    _agv.addLayout(_ag_row)

    _ag_list = QListWidget()
    _ag_list.setMinimumHeight(84)
    _agv.addWidget(_ag_list, 1)

    _ag_ops = QHBoxLayout()
    _ag_ops.setSpacing(8)
    _ag_up = QPushButton('上移')
    _ag_del = QPushButton('删除')
    _ag_down = QPushButton('下移')
    for _b in (_ag_up, _ag_del, _ag_down):
        _b.setMinimumSize(96, 32)
    _ag_ops.addWidget(_ag_up)
    _ag_ops.addWidget(_ag_del)
    _ag_ops.addWidget(_ag_down)
    _ag_ops.addStretch(1)
    _agv.addLayout(_ag_ops)

    _ag_log = QTextBrowser()
    _ag_log.setMinimumHeight(96)
    _ag_log.setStyleSheet(ux.DOC_BROWSER_QSS)
    _ag_log.setHtml('<div style="font-size:13px;color:%s;">执行记录会显示在这里：'
                    '每步的工具、耗时、摘要或失败原因。</div>' % ux.TOKENS['text_sub'])
    _agv.addWidget(_ag_log, 1)

    _ag_foot = QHBoxLayout()
    _ag_foot.setSpacing(8)
    _ag_hint = QLabel('')
    _ag_hint.setObjectName('sub')
    _ag_hint.setStyleSheet('font-size:11px;')
    _ag_run = QPushButton('运行步骤')
    _ag_run.setMinimumSize(120, 32)
    _ag_run.setStyleSheet(
        'QPushButton{background:rgba(56,226,255,0.18);'
        'border:1px solid rgba(56,226,255,0.45); border-radius:9px;'
        'padding:0 18px; font-weight:600; color:%s;}'
        'QPushButton:hover{background:rgba(56,226,255,0.28);}'
        'QPushButton:disabled{color:#64748b;background:rgba(255,255,255,0.05);}'
        % ux.TOKENS['text'])
    _ag_foot.addWidget(_ag_hint)
    _ag_foot.addStretch(1)
    _ag_foot.addWidget(_ag_run)
    _agv.addLayout(_ag_foot)
    _pg_agent.addWidget(_ag_body)
    _xtabs.addTab(_pg_agent, 'Agent 搭建')

    def _ag_render():
        _ag_list.clear()
        for _i, _s in enumerate(_ag_steps):
            _ag_list.addItem('%d. %s%s' % (
                _i + 1, _s['tool'], (' · ' + _s['arg']) if _s['arg'] else ''))
        _ag_hint.setText(('共 %d 步' % len(_ag_steps)) if _ag_steps
                         else '还没有步骤：选工具后点「添加步骤」')

    def _ag_persist():
        try:
            xx_agent_save(_ag_steps)
        except Exception:
            _err()

    def _ag_do_add():
        _ag_steps.append({'tool': _ag_tool.currentText(), 'arg': _ag_arg.text().strip()})
        _ag_arg.clear()
        _ag_persist()
        _ag_render()

    def _ag_do_del():
        _i = _ag_list.currentRow()
        if 0 <= _i < len(_ag_steps):
            _ag_steps.pop(_i)
            _ag_persist()
            _ag_render()

    def _ag_move(_d):
        _i = _ag_list.currentRow()
        _j = _i + _d
        if 0 <= _i < len(_ag_steps) and 0 <= _j < len(_ag_steps):
            _ag_steps[_i], _ag_steps[_j] = _ag_steps[_j], _ag_steps[_i]
            _ag_persist()
            _ag_render()
            _ag_list.setCurrentRow(_j)

    def _ag_one(_i, _r):
        _ok = bool(_r.get('ok'))
        _col = ux.TOKENS['text'] if _ok else ux.TOKENS['err']
        _body = _xx_escape(_r.get('summary') if _ok else _r.get('err'))
        _ag_log.append('<div style="margin:6px 0;color:%s;">第 %d 步 %s · %d ms<br>%s%s</div>'
                       % (_col, _r.get('i', _i) + 1, _xx_escape(_r.get('tool')), _r.get('ms', 0),
                          ('失败：' if not _ok else ''), _body))

    def _ag_done(_res):
        _ag_busy['v'] = False
        _ag_run.setEnabled(True)
        _ag_add.setEnabled(True)
        _ag_del.setEnabled(True)
        _sts = _res.get('steps') or []
        _good = sum(1 for _x in _sts if _x.get('ok'))
        _ag_hint.setText('完成：%d/%d 步成功%s' % (
            _good, len(_sts), '' if _good == len(_sts) else '（失败原因见记录）'))

    def _ag_run_all():
        if _ag_busy['v'] or not _ag_steps:
            if not _ag_steps:
                _ag_hint.setText('先添加至少一步再运行')
            return
        _ag_log.clear()
        _ag_log.append('<div style="margin:6px 0;color:%s;">开始执行 %d 步…</div>'
                       % (ux.TOKENS['text_sub'], len(_ag_steps)))
        _ag_busy['v'] = True
        _ag_run.setEnabled(False)
        _ag_add.setEnabled(False)
        _ag_del.setEnabled(False)
        _ag_hint.setText('执行中…')
        _w = XxAgentWorker(_ag_steps, parent=w)
        _w.step.connect(_ag_one)
        _w.done.connect(_ag_done)
        _ag_worker['w'] = _w
        _w.start()

    _ag_add.clicked.connect(_ag_do_add)
    _ag_del.clicked.connect(_ag_do_del)
    _ag_up.clicked.connect(lambda: _ag_move(-1))
    _ag_down.clicked.connect(lambda: _ag_move(1))
    _ag_run.clicked.connect(_ag_run_all)
    _ag_render()
    # C1-B2-7：原 `w._xx_agent = {...}` 全仓无读取，同上删除。

    # ---------------- AI 配置（技术配置项，保留「AI」字样）----------------
    _pg_ai = QWidget()
    _aiv = QVBoxLayout(_pg_ai)
    _aiv.setContentsMargins(16, 12, 16, 12)
    _aiv.setSpacing(10)
    _ai_card = _xx_card_style(GlassCard('AI 配置'))
    _aiv.addWidget(_ai_card, 1)
    _afl = QFormLayout(_ai_card)
    _afl.setContentsMargins(16, 14, 16, 14)
    _afl.setSpacing(10)
    _ai_ep = QLineEdit(str(xx_prefs.get('aiEndpoint', 'http://127.0.0.1:1234/v1')))
    _ai_ep.setPlaceholderText('http://127.0.0.1:1234/v1')
    _ai_md = QLineEdit(str(xx_prefs.get('aiModel', '')))
    _ai_md.setPlaceholderText('留空则用 LM Studio 当前已加载的模型')
    _ai_ky = QLineEdit()
    _ai_ky.setEchoMode(QLineEdit.Password)
    _ai_ky.setPlaceholderText('本机服务通常不需要；填写后仅经 DPAPI 加密保存')
    _ai_en = QCheckBox('开启对话能力')
    _ai_en.setChecked(bool(xx_prefs.get('aiEnabled', False)))
    _afl.addRow('AI 端点', _ai_ep)
    _afl.addRow('模型名', _ai_md)
    _afl.addRow('API Key', _ai_ky)
    _afl.addRow('', _ai_en)
    _ai_note = QLabel('端点与模型名保存在本机配置；Key 走 SecretStore（DPAPI 当前用户加密），'
                      '不写日志、不进安装目录。对话与 Agent 步骤执行已接入本配置。')
    _ai_note.setWordWrap(True)
    _ai_note.setObjectName('sub')
    _ai_note.setStyleSheet('font-size:11px;')
    _afl.addRow('', _ai_note)
    _ai_foot = QHBoxLayout()
    _ai_foot.addStretch(1)
    _ai_save_b = QPushButton('保存')
    _ai_save_b.setMinimumSize(120, 32)
    _ai_save_b.setFixedHeight(32)
    _ai_save_b.setStyleSheet(
        'QPushButton{background:rgba(56,226,255,0.18);'
        'border:1px solid rgba(56,226,255,0.45); border-radius:9px;'
        'padding:0 18px; font-weight:600; color:%s;}'
        'QPushButton:hover{background:rgba(56,226,255,0.28);}' % UI_C['INK_0'])
    _ai_foot.addWidget(_ai_save_b)
    # 页脚行统一 56 px，主操作 120×32 右对齐
    _ai_footw = QWidget()
    _ai_footw.setObjectName('pagefoot')   # R247：主题态页脚真实底色
    _ai_footw.setFixedHeight(56)
    _ai_foot.setContentsMargins(0, 0, 0, 0)
    _ai_foot.setSpacing(10)
    _ai_footw.setLayout(_ai_foot)
    _aiv.addWidget(_ai_footw)
    _xtabs.addTab(_pg_ai, 'AI 配置')

    # ---------------- 数据授权（逐项可关）----------------
    _pg_auth = QWidget()
    _auv = QVBoxLayout(_pg_auth)
    _auv.setContentsMargins(16, 12, 16, 12)
    _auv.setSpacing(10)
    _auth_card = _xx_card_style(GlassCard('数据授权（逐项可关，关闭后不进入上下文）'))
    _auv.addWidget(_auth_card, 1)
    _auvl = QVBoxLayout(_auth_card)
    _auvl.setContentsMargins(16, 14, 16, 14)
    _auvl.setSpacing(10)
    _auth_cbs = {}
    for _k, _lb, _hint, _dflt in (
            ('aiAuthSession', '会话统计', '把本次会话的算力与运行概况交给小星参考', True),
            ('aiAuthCurve', '采样窗口', '允许引用最近 24 小时的高频曲线摘要', False),
            ('aiAuthDevice', '设备识别', '允许引用本机识别到的设备分组（不含序列号）', True),
            ('aiAuthEvent', '事件原因', '允许引用“为什么断开”这类本地判定结果', True)):
        _cb = QCheckBox(_lb)
        _cb.setChecked(bool(xx_prefs.get(_k, _dflt)))
        _h = QLabel(_hint)
        _h.setWordWrap(True)
        _h.setObjectName('sub')
        _h.setStyleSheet('font-size:11px; margin-left:24px;')
        _auvl.addWidget(_cb)
        _auvl.addWidget(_h)
        _auth_cbs[_k] = _cb
    _auvl.addStretch(1)
    _xtabs.addTab(_pg_auth, '数据授权')

    # ---------------- 隐私与日志 ----------------
    _pg_pv = QWidget()
    _pvv = QVBoxLayout(_pg_pv)
    _pvv.setContentsMargins(16, 12, 16, 12)
    _pvv.setSpacing(10)
    _pv_card = _xx_card_style(GlassCard('隐私与日志'))
    _pvv.addWidget(_pv_card, 1)
    _pvl = QVBoxLayout(_pv_card)
    _pvl.setContentsMargins(16, 14, 16, 14)
    _pvl.setSpacing(8)
    for _s in ('能力边界：小星不能发短信、不能打电话、不能修改应用设置、不能主动推送；'
               '只在你点击发送后，向本机配置的模型端点发起一次请求。',
               '数据位置：会话记录（接入后）写入 %LOCALAPPDATA%\\XingHongPanel\\ai_sessions'
               '（JSONL 明文，可自行删除）；运行日志为同目录下 .log，不含 Key 与作品内容。',
               'Key 存放：只经 SecretStore 加密（Windows DPAPI 当前用户），密文存于本机配置项'
               ' aiApiKeyEnc，换用户或换机无法解密；不写日志、不进安装目录。',
               '说明全文：《隐私说明》《用户协议》《使用说明》在「配置中心 → 隐私说明 / 用户协议 / 使用说明」。'):
        _lb = QLabel(_s)
        _lb.setWordWrap(True)
        _pvl.addWidget(_lb)
    _pv_st = QLabel('')
    _pv_st.setObjectName('sub')
    _pv_st.setWordWrap(True)
    _pvl.addWidget(_pv_st)
    _pvl.addStretch(1)
    _pv_foot = QHBoxLayout()
    _pv_foot.addStretch(1)
    _pv_ok_b = QPushButton('阅读并同意隐私说明')
    _pv_ok_b.setMinimumSize(120, 32)
    _pv_ok_b.setFixedHeight(32)
    _pv_foot.addWidget(_pv_ok_b)
    # 页脚行统一 56 px，主操作 120×32 右对齐（文案超出时按 sizeHint 撑开）
    _pv_footw = QWidget()
    _pv_footw.setObjectName('pagefoot')   # R247：主题态页脚真实底色
    _pv_footw.setFixedHeight(56)
    _pv_foot.setContentsMargins(0, 0, 0, 0)
    _pv_foot.setSpacing(10)
    _pv_footw.setLayout(_pv_foot)
    _pvl.addWidget(_pv_footw)
    _xtabs.addTab(_pg_pv, '隐私与日志')

    def _xx_state():
        if not xx_prefs.consent_ok():
            return 'noconsent'
        return 'on' if bool(xx_prefs.get('aiEnabled', False)) else 'off'

    def _xx_refresh():
        """门禁与徽章的唯一刷新入口：同意状态或开关变化后立即重算。"""
        _st = _xx_state()
        _pg_chat.setCurrentIndex(1 if _st == 'on' else 0)
        _badge = {'noconsent': ('待同意隐私说明', UI_C['WARN_2'], 'rgba(212,184,150,0.14)'),
                  'off': ('已同意 · 未开启', UI_C['INK_2'], 'rgba(255,255,255,0.07)'),
                  'on': ('已开启', UI_C['OK'], 'rgba(156,175,136,0.18)')}[_st]
        _xbadge.setText(_badge[0])
        _xbadge.setStyleSheet(
            'QLabel{border-radius:9px; padding:2px 8px; font-size:11px;'
            'color:%s; background:%s; border:1px solid %s;}' % (_badge[1], _badge[2], _badge[1]))
        _xstat.setText({'noconsent': '未同意隐私说明 · 不会向任何模型端点发起请求',
                        'off': '已同意隐私说明 · 对话能力未开启',
                        'on': '对话能力已开启 · 消息只写本机会话记录（不含 Key）'}[_st])
        _xstat.setVisible(True)
        if _st == 'noconsent':
            _chat_card.setTitle('小星 · 还差一步')
            _chat_lines[0].setText('缺哪个开关：尚未同意《隐私说明》。')
            _chat_lines[1].setText('在哪开：「小星 → 隐私与日志」页底部点「阅读并同意隐私说明」。')
            _chat_lines[2].setText('开完会有什么：同意后，开启「对话能力」即可在这里提问；'
                                   '在此之前不会向任何模型端点发起请求。')
        elif _st == 'off':
            _chat_card.setTitle('小星 · 还未开启')
            _chat_lines[0].setText('缺哪个开关：「开启对话能力」还是关闭状态。')
            _chat_lines[1].setText('在哪开：「小星 → AI 配置」页勾选「开启对话能力」，再点「保存」。')
            _chat_lines[2].setText('开完会有什么：这里会出现输入框与会话记录，'
                                   '小星只在你点击发送后调用本机端点。')
        else:
            _chat_card.setTitle('小星 · 已开启')
            _chat_lines[0].setText('已同意《隐私说明》，对话能力已开启。')
            _chat_lines[1].setText('消息只发送到「AI 配置」里填写的端点；会话记录写入本机 '
                                   'ai_sessions，不含 Key。')
            _chat_lines[2].setText('不会在后台自动请求；任何请求都需要你点击发送。')
        if _st == 'on':
            _pg_agent.setCurrentIndex(1)
            _agent_card.setTitle('Agent 搭建')
            _agent_lines[0].setText('按步骤执行本地工具的最小闭环：定义步骤 → 运行 → 看执行记录。')
            _agent_lines[1].setText('工具只有本地面板接口与本地会话统计；参数留空用默认值。')
            _agent_lines[2].setText('失败会写明 HTTP 状态码或异常原因，并标出是哪一步。')
        else:
            _pg_agent.setCurrentIndex(0)
            _agent_card.setTitle('Agent 搭建 · 还差一步')
            _agent_lines[0].setText('缺哪个开关：' + ('尚未同意《隐私说明》。' if _st == 'noconsent'
                                                      else '「开启对话能力」还是关闭状态。'))
            _agent_lines[1].setText('在哪开：' + ('「小星 → 隐私与日志」页点「阅读并同意隐私说明」。'
                                                   if _st == 'noconsent'
                                                   else '「小星 → AI 配置」页勾选后保存。'))
            _agent_lines[2].setText('开完会有什么：Agent 搭建解锁（与对话共用同一套模型配置）。')
        _pv_st.setText(('当前状态：已同意（%s）' % (xx_prefs.consent_at() or '时间未记录'))
                       if xx_prefs.consent_ok() else '当前状态：尚未同意《隐私说明》')
        _pv_ok_b.setText('已同意 · 重新阅读' if xx_prefs.consent_ok()
                         else '阅读并同意隐私说明')

    def _xx_save():
        try:
            xx_prefs.set('aiEndpoint', _ai_ep.text().strip())
            xx_prefs.set('aiModel', _ai_md.text().strip())
            xx_prefs.set('aiEnabled', bool(_ai_en.isChecked()))
            _mp = _ai_ky.text().strip()
            if _mp:
                try:
                    xx_keys.save_key(_mp)
                except Exception:
                    _err()
                _ai_ky.clear()
            for _k, _cb in _auth_cbs.items():
                xx_prefs.set(_k, bool(_cb.isChecked()))
            _xx_refresh()
            w._toast.show('小星配置已保存', 'ok')
        except Exception as _e:
            w._toast.show('保存失败：%s' % _e, 'warning')

    def _xx_consent():
        try:
            ux.ensure_consent(w, xx_prefs)
        except Exception:
            _err()
        _xx_refresh()

    _ai_save_b.clicked.connect(_xx_save)
    _ai_en.toggled.connect(lambda _=False: _xx_refresh())
    _pv_ok_b.clicked.connect(_xx_consent)
    for _k, _cb in _auth_cbs.items():
        _cb.toggled.connect(lambda _v, _key=_k: xx_prefs.set(_key, bool(_v)))
    _xx_refresh()
    w._xx_refresh = _xx_refresh
    w._xx_tabs = _xtabs
    stack.addWidget(xp)

    def xx_ask_consent():
        """启动 600 ms 后的隐私同意补检（原 main() 内 _xx_ask_consent）。

        C1-B2-7：原先这里 `_xp = ux.Prefs(...)` 另建了一个实例，写进 A、读从 B
        最难查；现在直接用本页的 xx_prefs——它就是配置页那份 adv_prefs。
        """
        try:
            _st = str(xx_prefs.s.value('legal/accepted', ''))
            if xx_prefs.consent_ok():
                _consent_log(_st, ux.CONSENT_VERSION, 'skip')
            else:
                _consent_log(_st, ux.CONSENT_VERSION, 'prompt')
                _ok = ux.ensure_consent(w, xx_prefs)
                _consent_log(str(xx_prefs.s.value('legal/accepted', '')), ux.CONSENT_VERSION,
                             'accepted' if _ok else 'rejected')
            if getattr(w, '_xx_refresh', None):
                w._xx_refresh()
        except Exception:
            _err()

    return {'xx_ask_consent': xx_ask_consent}
