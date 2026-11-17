# -*- coding: utf-8 -*-
"""UI 页面：Tab「状态」（C1-B2 分层重构第 4 批，与 Probe 收口同批）。

原位置：xinghong_launcher.py build_ui() 内 7407–8365 行，整段原样搬出。
分层：ui（与 xinghong_launcher.py 同层），只依赖 PySide6 / 标准库；
launcher 的控件工厂与私有工具全部由调用方显式传参，禁止 import launcher。
_safe_post 是跨页工具（状态页 3 处 + 生图页 _comfy_power），已上移到 launcher 模块级，
本页按入参接收，不在此重复定义。
"""
import os
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from infra.config import XH_ADV, XH_PROBE, XH_PROFILE
from infra import exitd as _exitd

__all__ = ['build_page_status']

def build_page_status(*, stack, w, api, qwait, _err, _perf_mark, _nav_icon,
                      _boot_mark, _flow_strip_rebuild_soon, _safe_post, _t,
                      fade_in, fnum, make_plot, pg, smooth_xy,
                      GlassCard, Gauge, FlowProgressBar, AnimatedNumberLabel,
                      ReflowGrid, UI_C, ACCENT, ACCENT2, TXT3, R10,
                      CLK_SM_MAX, CLK_MEM_MAX,
                      _BOOT, _FLOW_STRIP_ON,
                      xh_fmt_clk, xh_fmt_dur, xh_fmt_energy, xh_norm_power):
    """Tab「状态」页面构建（C1-B2 第 4 批，从 build_ui 原样搬出）。

    依赖全部显式传入，参数名与 build_ui 内原标识符逐字一致（机械等价可逐行比对）。
    返回值只暴露 launcher 后续确实引用的句柄：_probe_status 组装、
    t_metrics/t_state 定时器、推理页回调、R37 指标缓冲与绘图内部量。
    """
    # ============ Tab 1: 状态 ============
    _perf_mark('tab0_b')
    st = QWidget()
    sv = QVBoxLayout(st)
    sv.setContentsMargins(4, 8, 4, 4)
    _stat_cards = []

    _stat_bars = []
    def stat_card(title, c1=ACCENT, c2=ACCENT2, glyph=None):
        g = GlassCard(title)
        # R25-W1：状态页八卡补语义线稿（与导航同一套笔触）
        if glyph:
            g.set_glyph(glyph)
        # R015（领导）：96px——原 64 未含标题区 30，数值纵向被裁半
        g.setMinimumHeight(96)
        gv = QVBoxLayout(g)
        # QGroupBox 样式已 margin-top:30 预留标题区，此处只需少量呼吸边距
        gv.setContentsMargins(12, 12, 12, 8)   # R25-W4：水平内边距与中排三卡统一 12
        big = AnimatedNumberLabel('—')
        # R31：objectName 由 _apply_num_style 管理（big / big_empty），此处不再固定为 big
        big.setAlignment(Qt.AlignCenter)
        big.set_formatter(lambda v: '%.1f%%' % v)
        bar = FlowProgressBar(c1, c2)
        gv.addWidget(big)
        gv.addWidget(bar)
        _stat_cards.append(g)
        _stat_bars.append((g, gv, big, bar))
        return big, bar, g

    mem_big, mem_bar, g_mem = stat_card('显存占用', glyph='memory')
    gpu_big, gpu_bar, g_gpu = stat_card('GPU 利用率', glyph='gpu')
    cpu_big, cpu_bar, g_cpu = stat_card('CPU 占用', glyph='cpu')
    ram_big, ram_bar, g_ram = stat_card('内存占用', glyph='ram')
    # 响应式：宽窗 4 卡一行，窄窗自动 2×2（不横向溢出）
    cards_grid = ReflowGrid([(g, 170, 1) for g in _stat_cards])
    sv.addWidget(cards_grid)
    # R20-4：四条流光并成一个不透明条带（一拍一区）；布局定稿后建，重排时由守卫重建
    if _FLOW_STRIP_ON:
        _flow_strip_rebuild_soon(0)
    # 四张状态卡错落淡入，结束后挂回阴影
    for i, gc in enumerate((g_mem, g_gpu, g_cpu, g_ram)):
        fade_in(gc, duration=260, delay=45 * i, restore_shadow=True)

    g_temp = GlassCard('温度与功耗')
    g_temp.set_glyph('temp')                 # R25-W1
    g_temp.setMinimumHeight(94)
    gtv = QHBoxLayout(g_temp)
    gtv.setContentsMargins(12, 12, 12, 8)
    gtv.setSpacing(8)
    gauge_temp = Gauge('温度', '°C', UI_C['WARN'], UI_C['WARN_2'], 100.0)
    gauge_power = Gauge('功耗', 'W', UI_C['OK'], UI_C['OK_DIM'], 140.0)
    gtv.addWidget(gauge_temp)
    gtv.addWidget(gauge_power)
    # 紧凑模式（小窗口）：用一行小文字替代两个圆形仪表
    lbl_tp_compact = QLabel('—')
    lbl_tp_compact.setAlignment(Qt.AlignCenter)
    lbl_tp_compact.setStyleSheet(_t('font-size:15px; font-weight:700; color:@INK_0@;'))
    lbl_tp_compact.hide()
    gtv.addWidget(lbl_tp_compact, 1)
    # 功耗统计：GlassCard，整卡网页版绿色基调（tint），数字用绿色；内容垂直居中
    g_energy = GlassCard('功耗统计', tint=(156, 175, 136, 24))
    g_energy.set_glyph('power')              # R25-W1
    g_energy.setMinimumHeight(94)
    g_energy.play_enter = getattr(g_energy, 'play_enter', None)
    ev = QVBoxLayout(g_energy)
    ev.setContentsMargins(12, 12, 12, 8)
    ev.setSpacing(4)
    ev.addStretch(1)
    energy_range = QComboBox()
    energy_range.addItems(['全时段', '今日', '本周', '本月'])
    # R023-M2：记住上次能耗范围（回填先于 poll_energy 连接）
    _eprefs = QSettings('X.H', 'XingHongPanel')
    try:
        energy_range.setCurrentIndex(max(0, min(3, int(_eprefs.value('energyRange', 0)))))
    except Exception:
        energy_range.setCurrentIndex(0)
    energy_range.currentIndexChanged.connect(
        lambda i: _eprefs.setValue('energyRange', int(i)))
    ev.addWidget(energy_range)
    lbl_energy = QLabel('—')
    lbl_energy.setAlignment(Qt.AlignCenter)
    lbl_energy.setStyleSheet(_t('font-size:30px; font-weight:700; color:@OK@;'))  # R25-W2：字重与 #big 同族
    ev.addWidget(lbl_energy)
    lbl_energy2 = QLabel('')
    lbl_energy2.setObjectName('sub')
    lbl_energy2.setAlignment(Qt.AlignCenter)
    lbl_energy2.setWordWrap(True)
    ev.addWidget(lbl_energy2)
    ev.addStretch(1)
    # GPU 时钟（算力指标）：内容垂直居中对齐
    g_clk = GlassCard('GPU 时钟')
    g_clk.set_glyph('clock')                 # R25-W1
    g_clk.setMinimumHeight(94)
    clv = QVBoxLayout(g_clk)
    clv.setContentsMargins(12, 12, 12, 8)
    clv.setSpacing(4)
    clv.addStretch(1)
    lbl_sm = QLabel('—')
    lbl_sm.setObjectName('big')
    lbl_sm.setAlignment(Qt.AlignCenter)
    lbl_sm.setStyleSheet('font-size:22px;')   # R25-W2：20→22，补齐 30/28>22>16>12 阶梯
    lbl_sm_cap = QLabel('核心时钟 MHz')
    lbl_sm_cap.setObjectName('sub')
    lbl_sm_cap.setAlignment(Qt.AlignCenter)
    lbl_memclk = QLabel('—')
    lbl_memclk.setAlignment(Qt.AlignCenter)
    lbl_memclk.setStyleSheet('font-size:15px; color:%s;' % TXT3)  # R25-W2：非 token #9fb4c8 → TXT3
    lbl_memclk_cap = QLabel('显存时钟 MHz')
    lbl_memclk_cap.setObjectName('sub')
    lbl_memclk_cap.setAlignment(Qt.AlignCenter)
    clv.addWidget(lbl_sm)
    clv.addWidget(lbl_sm_cap)
    clv.addSpacing(8)   # R21：间距 token 归一到 8（GPU 时钟两组读数之间，原 6）
    clv.addWidget(lbl_memclk)
    clv.addWidget(lbl_memclk_cap)
    clv.addStretch(1)
    # 响应式：宽窗 温度功耗(2)/能耗(1)/时钟(1) 一行；窄窗自动换行不溢出
    mid_grid = ReflowGrid([(g_temp, 200, 2), (g_energy, 160, 1), (g_clk, 160, 1)])
    sv.addWidget(mid_grid)
    # 自检探针（R07）：供管道 kvinfo 导出同行卡片几何，核对底边是否齐平
    w._cards_geo = {'stat': list(_stat_cards), 'temp': g_temp,
                    'energy': g_energy, 'clock': g_clk, 'mid_grid': mid_grid}

    g_chart = GlassCard('实时性能曲线')
    g_chart.set_glyph('chart')               # R25-W1
    cv = QVBoxLayout(g_chart)
    cv.setContentsMargins(12, 12, 12, 8)     # R25-W4：水平内边距与八卡统一 12
    cv.setSpacing(8)
    # 托管服务状态行（合并自原「托管服务」独立卡）
    # X12：状态标签与操作按钮拆上下两行——标签独占一行可换行，按钮组右对齐，
    # 4 个状态 chip 完整可读不再相互挤压
    sv_row_w = QWidget()
    sv_row = QVBoxLayout(sv_row_w)
    sv_row.setContentsMargins(0, 0, 0, 0)
    sv_row.setSpacing(8)
    lbl_sv = QLabel('正在读取托管服务状态…')
    lbl_sv.setObjectName('sub')
    lbl_sv.setWordWrap(True)
    # R5：上次退出对账（infra.exitd.reconcile 写好的那句话）。没有就整行不显示——
    # 这是诚实告知，不是报错：默认退出会把停服收尾交给后台守护进程。
    lbl_exit = QLabel(str(getattr(_exitd, 'STATE', {}).get('msg') or ''))
    lbl_exit.setWordWrap(True)
    lbl_exit.setStyleSheet('color:%s;padding:2px 0;' % UI_C['WARN'])
    lbl_exit.setVisible(bool(lbl_exit.text()))
    w._exit_reconcile = lbl_exit.text()
    btn_free = QPushButton('一键释放显存')
    btn_interrupt = QPushButton('中断生成')
    btn_stopall = QPushButton('停止全部服务')
    btn_stopall.setObjectName('danger')
    # R30：曲线卡三枚操作按钮补 14px 线稿
    for _b, _g in ((btn_free, 'memory'), (btn_interrupt, 'stop'),
                   (btn_stopall, 'power')):
        _b.setIcon(_nav_icon(_g, 14, UI_C['INK_1']))
        _b.setIconSize(QSize(14, 14))
    sv_row.addWidget(lbl_sv)
    sv_row.addWidget(lbl_exit)
    btns_sv = QHBoxLayout()
    btns_sv.setSpacing(8)
    btns_sv.addStretch(1)
    btns_sv.addWidget(btn_free)
    btns_sv.addWidget(btn_interrupt)
    btns_sv.addWidget(btn_stopall)
    sv_row.addLayout(btns_sv)
    cv.addWidget(sv_row_w)
    # R007（XH-01）：图例与缩放工具并为一行，提示降为 tooltip，消除三行小字堆叠
    chart_tool_w = QWidget()
    chart_tool = QHBoxLayout(chart_tool_w)
    chart_tool.setContentsMargins(0, 0, 0, 0)
    chart_tool.setSpacing(8)
    legend_inline = QLabel(
        _t('<span style="color:@WARN@">●</span> 显存　'
        '<span style="color:@INK_2@">●</span> GPU　'
        '<span style="color:@OK@">●</span> 功耗　'
        '<span style="color:@WARN_2@">●</span> 温度　'
        '<span style="color:@SERIES_MEM@">●</span> 内存　'
        '<span style="color:@SERIES_CPU@">●</span> CPU'
        # R36-F3：操作提示从 plot 的 Qt tooltip 挪到图例行内联——tooltip 是浮在光标上的
        # 独立窗口，悬停曲线时正好压住读数浮层、还会盖到左侧 Y 轴刻度，读不出数。
        '<span style="color:@INK_2@">　·　滚轮缩放 / 拖拽平移</span>'))
    legend_inline.setStyleSheet('font-size:12px; padding:2px 0;')
    btn_back_live = QPushButton('回到实时')
    btn_reset_zoom = QPushButton('重置缩放')
    # R30：图例行两枚工具按钮补 14px 线稿
    for _b, _g in ((btn_back_live, 'clock'), (btn_reset_zoom, 'expand')):
        _b.setIcon(_nav_icon(_g, 14, UI_C['INK_1']))
        _b.setIconSize(QSize(14, 14))
    chart_tool.addWidget(legend_inline, 1)
    chart_tool.addWidget(btn_back_live)
    chart_tool.addWidget(btn_reset_zoom)
    cv.addWidget(chart_tool_w)
    # R07：曲线无数据 / 采集中断时的显式说明（原来是一块纯空白图，用户以为程序坏了）
    lbl_chart_state = QLabel('')
    lbl_chart_state.setObjectName('sub')
    lbl_chart_state.setWordWrap(True)
    lbl_chart_state.hide()
    cv.addWidget(lbl_chart_state)

    plot = make_plot()
    # R008（N7）：操作提示保留，但改为图例行内联文字（见 legend_inline）。
    # R36-F3：原 plot.setToolTip 会以光标浮窗覆盖读数浮层与 Y 轴刻度，已移除。
    # 领导要求：曲线区不要底色（透明，与 GlassCard 背景融合）
    plot.getViewBox().setBackgroundColor(None)
    # 仅保留横向网格（现代仪表盘风格）、极淡；坐标轴隐于背景
    for axname in ('bottom', 'left'):
        _ax = plot.getAxis(axname)
        _ax.setPen(pg.mkPen(QColor(175, 185, 208, 140)))
        _ax.setTextPen(QColor(159, 170, 184))
    plot.showGrid(x=False, y=True, alpha=0.10)
    plot.setYRange(0, 100)
    plot.setLabel('left', '')
    plot.setMouseEnabled(x=True, y=False)
    plot.setMenuEnabled(False)
    # 性能：峰值降采样 + 视口裁剪，降低每秒 setData 绘制量
    plot.setDownsampling(auto=True, mode='peak')
    plot.setClipToView(True)
    # 允许曲线区收缩到较小高度（小窗口适配），保证底部时间轴仍在视口内。
    plot.setMinimumHeight(70)

    # 右侧独立温度轴（℃）——R007（XH-03）：去橙色改中性灰蓝（曲线保留橙色辨识），
    # 量程改为滞回自适应（原每秒 setYRange 导致右轴整列每秒重排）
    plot.showAxis('right')
    rax = plot.getAxis('right')
    rax.setPen(pg.mkPen(QColor(150, 175, 205, 155)))
    rax.setTextPen(QColor(150, 175, 205))
    rax.setLabel('温度 °C', color=UI_C['INK_2'])
    temp_vb = pg.ViewBox()
    plot.scene().addItem(temp_vb)
    rax.linkToView(temp_vb)
    temp_vb.setXLink(plot)
    temp_vb.setYRange(0, 100)
    temp_vb.setMouseEnabled(x=False, y=False)
    temp_vb.setBackgroundColor(None)

    def _sync_temp_views():
        temp_vb.setGeometry(plot.getViewBox().sceneBoundingRect())
        temp_vb.linkedViewChanged(plot.getViewBox(), temp_vb.XAxis)
    plot.getViewBox().sigResized.connect(_sync_temp_views)

    # R007（XH-02）：每序列先建 glow 底线（宽 7、低透明）再建主线，setData 同步双线；
    # 主要序列加顶部渐变面积（QBrush 包裹 QLinearGradient，规避 mkBrush 陷阱）。
    glow_curves = {}

    def mkcurve(name, hexcol, fill_alpha=0.0, width=2.2, glow=True, dash=False):
        if glow and 'noglow' not in XH_PROBE:
            gcol = QColor(hexcol)
            gcol.setAlpha(40)
            glow_curves[name] = plot.plot(pen=pg.mkPen(gcol, width=7))
        _pen = pg.mkPen(hexcol, width=width)
        if dash:
            # R38-19：WARN 系列改虚线——该色与 OK 在红色盲模拟下 ΔE00 仅 3.0，
            # 只靠色相分不开；加线型编码后两条同轴线在色弱下仍可区分。
            _pen.setStyle(Qt.CustomDashLine)
            _pen.setDashPattern([4.0, 2.5])
        c = plot.plot(pen=_pen, name=name)
        if fill_alpha > 0:
            # R008（N4）：恒定低透明面积（渐变坐标写死 0-100 值域，曲线低位时不可感知）
            fc = QColor(hexcol)
            fc.setAlpha(int(fill_alpha * 165))
            c.setFillLevel(0)
            c.setBrush(QBrush(fc))
        return c

    # 6 序列（莫兰迪化，保留区分度）：低饱和、灰调
    curves = {
        '显存%': mkcurve('显存%', UI_C['WARN'], fill_alpha=0.12, dash=True),
        'GPU%': mkcurve('GPU%', UI_C['INK_2'], fill_alpha=0.14),
        '功耗%': mkcurve('功耗%', UI_C['OK'], fill_alpha=0.18),
        '温度℃': None,  # 温度单独走 temp_vb
        '内存%': mkcurve('内存%', UI_C['SERIES_MEM'], fill_alpha=0.10),
        'CPU%': mkcurve('CPU%', UI_C['SERIES_CPU'], fill_alpha=0.14),
    }
    temp_curve = pg.PlotDataItem(pen=pg.mkPen(UI_C['WARN_2'], width=2.4), name='温度℃')
    temp_vb.addItem(temp_curve)
    cv.addWidget(plot, 1)
    sv.addWidget(g_chart, 1)

    # 小窗口高度紧凑模式：隐藏托管服务行、压缩各卡最小高度，保证曲线不被裁切
    class _CompactMode(QObject):
        def __init__(self):
            super().__init__()
            self._compact = False
            # R33：条件档位自证——记录分支切换次数与最近一次客户区高
            self._hits = {'on': 0, 'off': 0}
            self._last_h = None
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Resize:
                self._last_h = ev.size().height()
                self._apply(ev.size().height() < 540)
            return False
        def _apply(self, compact):
            if compact == self._compact:
                return
            self._compact = compact
            self._hits['on' if compact else 'off'] += 1
            sv_row_w.setVisible(not compact)
            # 温度与功耗：紧凑时用小文字、隐藏圆形仪表、收紧边距
            gauge_temp.setVisible(not compact)
            gauge_power.setVisible(not compact)
            lbl_tp_compact.setVisible(compact)
            gtv.setContentsMargins(12, 0 if compact else 8, 12, 0 if compact else 8)
            # 功耗卡：紧凑时隐藏范围选择与副文字、数字缩小
            energy_range.setVisible(not compact)
            lbl_energy2.setVisible(not compact)
            lbl_energy.setStyleSheet(_t('font-size:%dpx; font-weight:800; color:@OK@;')
                                     % (18 if compact else 28))
            ev.setContentsMargins(12, 0 if compact else 8, 12, 0 if compact else 8)
            # 时钟卡：紧凑时只留核心时钟数值、隐藏说明与显存时钟
            lbl_sm_cap.setVisible(not compact)
            lbl_memclk_cap.setVisible(not compact)
            lbl_memclk.setVisible(not compact)
            lbl_sm.setStyleSheet('font-size:%dpx;' % (16 if compact else 20))
            clv.setContentsMargins(12, 0 if compact else 8, 12, 0 if compact else 8)
            # 状态卡：紧凑时隐藏进度条、数字缩小、标题区压缩、强制压缩高度
            for g, gv, big, bar in _stat_bars:
                bar.setVisible(not compact)
                g._compact_title = compact
                gv.setContentsMargins(8, 0 if compact else 4, 8, 0 if compact else 8)
                big._compact = compact
                big._apply_num_style()   # R31：字号/空值态统一由 QSS 选择器决定
                # 紧凑：标题区从 30 压到 20，给数字留出竖向空间
                g.setStyleSheet('QGroupBox { background:transparent; border:none;'
                                ' margin-top:%dpx; padding:0 6px 6px 6px; }'
                                % (20 if compact else 30))
                g.setMinimumHeight(50 if compact else 64)
                g.setMaximumHeight(56 if compact else 16777215)
                g._cache = None
                g.updateGeometry()
            for g in (g_temp, g_energy, g_clk):
                g.setMinimumHeight(52 if compact else 94)
                g.setMaximumHeight(58 if compact else 16777215)
                g.updateGeometry()
            plot.setMinimumHeight(70 if compact else 120)
            cards_grid.updateGeometry()
            mid_grid.updateGeometry()
    _cm = _CompactMode()
    w.installEventFilter(_cm)
    w._cm = _cm

    # 十字光标 + 读数（含累计能耗），对齐网页 cbTip（index.html:892-898）
    vline = pg.InfiniteLine(angle=90, movable=False,
                           pen=pg.mkPen(QColor(175, 185, 208, 90), width=1))
    hline = pg.InfiniteLine(angle=0, movable=False,
                           pen=pg.mkPen(QColor(175, 185, 208, 60), width=1))
    plot.addItem(vline, ignoreBounds=True)
    plot.addItem(hline, ignoreBounds=True)
    readout = pg.TextItem(color=UI_C['INK_1'], anchor=(1, 0),
                          fill=pg.mkBrush(QColor(12, 22, 40, 220)))
    plot.addItem(readout, ignoreBounds=True)
    readout.hide()

    # 保存最近一次各序列 (ts -> value) 与累计能耗，供光标读数
    last_series = {k: [] for k in curves}
    last_na = []      # R36-F5：每个采样点的「无效序列」集合，读数按 — 显示而不是 0.0
    cum_wh = []

    def nearest_idx(ts):
        data = last_series['显存%']
        if not data:
            return None
        best, bd = 0, None
        for i, (t, v) in enumerate(data):
            d = abs(t - ts)
            if bd is None or d < bd:
                bd, best = d, i
        return best

    # 托管服务状态已合并到上方「实时性能曲线」卡片顶部
    # R009 布局C：状态页不套外层滚动（一页无滚动条），曲线卡 stretch=1 占满剩余高度
    _sa0 = st
    stack.addWidget(_sa0)

    # 曲线默认显示窗口（秒），P1 配置中心可改；0=全部
    chart_win = 180
    follow_live = True
    last_tmax = [0.0]  # 上次跟随窗口的右端时间，仅在秒边界变化时重设，避免回跳
    temp_ymax = [60.0]   # 温度右轴量程滞回
    BUF_CAP = [1200]     # 每序列原始缓冲上限（XH-2）；由「图表点数」即时调整
    SERIES_KEYS = ('显存%', 'GPU%', '功耗%', '温度℃', '内存%', 'CPU%')

    # 增量缓冲：统一存解析后的点（以 wall 为键），仅追加新点
    rows_buf = []
    # R02（25 轮）：/api/metrics 全量返回约 290 KB/次（1 Hz，约 30 ms 服务端 CPU 且随运行时长增长）。
    # 改为首帧全量、之后带 since 只取增量；服务重启/回跳（返回空或末点早于缓冲尾）时自动回退全量。
    last_ts = [0.0]
    empty_n = [0]
    _m_err = [0]     # R07：/api/metrics 连续失败计数（断流提示）
    _m_ok = [0]      # R07：是否成功拿到过数据（区分冷启动与真断流）
    drawn = [False]  # 增量模式下无新点则跳过曲线重绘（2 s 采样 → 实际绘制频率减半）
    _cd = [0]        # R03：曲线渲染节拍计数（每 2 拍渲染一帧）
    _prof = {'n': 0, 'sum': 0.0, 'max': 0.0}   # R04：进程内计时累加器
    _prof_t0 = [0.0]

    def _parse_point(pt):
        t = float(pt.get('wall') or pt['ts'])
        mt_raw = pt.get('memTotal')
        mu_raw = pt.get('memUsed')
        rt_raw = pt.get('ramTotal')
        ru_raw = pt.get('ramUsed')
        mt = float(mt_raw or 8151)
        mu = float(mu_raw or 0)
        rt = float(rt_raw or 1)
        ru = float(ru_raw or 0)
        pl_raw = pt.get('powerLimit')
        try:
            pl = float(pl_raw) if pl_raw is not None else None
        except (TypeError, ValueError):
            pl = None
        # R33：量程缺失（历史 69.41% 为 0）或可疑时不当作量程用，更不拿 140 冒充。
        if pl is not None and not (20.0 < pl <= 1000.0):
            pl = None
        # R32/R33：历史/实时同流混用 W 与 mW；归一后仍越物理区间 → None（未知）
        pw = xh_norm_power(pt.get('power'), pl)
        # R36-F5：记录本点哪些序列是「后端没上报」而不是「真的是 0」。
        # 曲线落点维持原口径（本节不改渲染），读数浮层据此显示 —，不再把脏值写成 0.0。
        na = []
        if not mt_raw or mu_raw is None:
            na.append('显存%')
        if pt.get('gpuUtil') is None:
            na.append('GPU%')
        if pw is None or not pl:
            na.append('功耗%')
        if pt.get('temp') is None:
            na.append('温度℃')
        if not rt_raw or ru_raw is None:
            na.append('内存%')
        if pt.get('cpu') is None:
            na.append('CPU%')
        return {
            't': t, 'pw': pw, 'na': na,
            '显存%': mu / mt * 100 if mt else 0,
            'GPU%': float(pt.get('gpuUtil') or 0),
            '功耗%': (pw / pl * 100) if (pw is not None and pl) else 0,
            '温度℃': float(pt.get('temp') or 0),
            '内存%': ru / rt * 100 if rt else 0,
            'CPU%': float(pt.get('cpu') or 0),
        }

    def _attach_cum(rows):
        acc = 0.0
        prev = None
        for r in rows:
            if prev is not None and r['pw'] is not None and prev[1] is not None:
                dt = r['t'] - prev[0]
                if dt >= 0:
                    acc += (r['pw'] + prev[1]) / 2 * dt / 3600
            r['cum'] = acc
            prev = (r['t'], r['pw'])
        return rows

    def _rebuild(h):
        rows = []
        for pt in h:
            try:
                rows.append(_parse_point(pt))
            except (TypeError, ValueError):
                _err()
                continue
        if len(rows) > BUF_CAP[0]:
            rows = rows[-BUF_CAP[0]:]
        rows_buf[:] = _attach_cum(rows)

    def _merge(h):
        """增量合并：服务端每次返回全量 history，跳过旧点、仅追加 wall>末尾 的新点；
        仅当新数据末点早于当前末尾（服务重启/回跳）时才全量重建。"""
        parsed = []
        for pt in h:
            try:
                parsed.append(_parse_point(pt))
            except (TypeError, ValueError):
                _err()
                continue
        if not parsed:
            return
        if not rows_buf:
            rows_buf[:] = _attach_cum(parsed[-BUF_CAP[0]:])
            return
        last = rows_buf[-1]['t']
        # 回跳：新数据最末点比当前末尾还早 → 全量重建
        if parsed[-1]['t'] < last:
            rows_buf[:] = _attach_cum(parsed[-BUF_CAP[0]:])
            return
        new_rows = [r for r in parsed if r['t'] > last]
        if not new_rows:
            return
        acc = rows_buf[-1]['cum']
        prev = (rows_buf[-1]['t'], rows_buf[-1]['pw'])
        for r in new_rows:
            dt = r['t'] - prev[0]
            if dt >= 0 and r['pw'] is not None and prev[1] is not None:
                acc += (r['pw'] + prev[1]) / 2 * dt / 3600
            r['cum'] = acc
            prev = (r['t'], r['pw'])
        rows_buf.extend(new_rows)
        if len(rows_buf) > BUF_CAP[0]:
            del rows_buf[:-BUF_CAP[0]]

    def _chart_state(txt, kind='sub'):
        """R07：曲线区状态说明。无数据/断流时必须有可见文字，不能留一块空白图。"""
        try:
            if not txt:
                lbl_chart_state.hide()
                lbl_chart_state.setText('')
                return
            _c = UI_C['WARN_2'] if kind == 'warn' else UI_C['INK_2']
            # R20-3：颜色没变不重设 QSS（每拍都设会触发 StyleChange → 守卫整页复核）
            if getattr(lbl_chart_state, '_xh_col', None) != _c:
                lbl_chart_state.setStyleSheet(
                    'font-size:12px; color:%s; padding:2px 0;' % _c)
                lbl_chart_state._xh_col = _c
            lbl_chart_state.setText(txt)
            lbl_chart_state.show()
        except Exception:
            _err()

    def poll_metrics():
        # 网络请求放到后台线程，避免阻塞主线程导致卡顿
        if rows_buf and last_ts[0] > 0:
            qwait(lambda: api('GET', '/api/metrics?since=%.2f' % last_ts[0]),
                  _metrics_done, key='metrics')
        else:
            qwait(lambda: api('GET', '/api/metrics'), _metrics_done, key='metrics')

    def _metrics_done(r):
        # R04：进程内计时——记录相邻两拍的到达间隔（ms）。间隔稳定在 ~2000 ms 说明
        # 事件循环未被本拍处理阻塞；间隔拉长则说明本拍处理拖住了 UI 线程。
        if XH_PROFILE:
            _now = time.perf_counter()
            if _prof_t0[0] > 0:
                _dt = (_now - _prof_t0[0]) * 1000
                _prof['n'] += 1
                _prof['sum'] += _dt
                _prof['max'] = max(_prof['max'], _dt)
                if _prof['n'] % 10 == 0:
                    _ln = ('tick=%d avg=%.1fms max=%.1fms last=%.1fms rows=%d\n'
                           % (_prof['n'], _prof['sum'] / _prof['n'],
                              _prof['max'], _dt, len(rows_buf)))
                    try:
                        with open(XH_PROFILE, 'a', encoding='utf-8') as _f:
                            _f.write(_ln)
                    except Exception:
                        _err()
            _prof_t0[0] = _now
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                _m_err[0] += 1
                if _m_ok[0] and _m_err[0] >= 3:
                    _chart_state('数据采集中断：面板服务 /api/metrics 连续 %d 次无响应。'
                                 '请到「配置 → 运行日志」确认面板服务是否在运行；'
                                 '仍无数据时重启面板后再回来。' % _m_err[0], 'warn')
                elif not _m_ok[0]:
                    # 冷启动：内嵌后端还在起监听，此时报「断流」会误导用户
                    _chart_state('正在启动面板服务：还没有拿到性能数据，请稍候…')
                return
            _m_err[0] = 0
            _m_ok[0] = 1
            _code, d = r
            if not isinstance(d, dict):
                _chart_state('数据格式异常：面板返回了无法解析的性能数据，'
                             '曲线暂不可用。请重启面板后再回来。', 'warn')
                return
            s = d.get('sys', {})
            if not isinstance(s, dict) or not s:
                _chart_state('数据格式异常：面板返回里没有性能字段（sys）。'
                             '8899 可能被其他程序占用，请关闭占用程序后重启面板。', 'warn')
                return
            if 'data_ready' not in _BOOT['marks']:
                # R36-N1：首屏真实数据落地；下一拍事件循环空闲即视为「可交互」
                # （能点、能切页——事件循环不再被启动期构建阻塞）。
                _boot_mark('data_ready')
                QTimer.singleShot(0, lambda: _boot_mark('interactive'))
            ram_tot = float(s.get('ramTotal') or 1)
            mem_tot = float(s.get('memTotal') or 8151)
            # 显存占用 = memUsed/memTotal（修正 B-05：原误用 memUtil 带宽利用率）
            m_util = float(s.get('memUsed') or 0) / mem_tot * 100 if mem_tot else 0
            ram_u = float(s.get('ramUsed') or 0) / ram_tot * 100 if ram_tot else 0
            mem_big.set_target(m_util)
            mem_bar.setValue(int(m_util))
            gpu_big.set_target(s.get('gpuUtil'))
            gpu_bar.setValue(int(float(s.get('gpuUtil') or 0)))
            cpu_big.set_target(s.get('cpu'))
            cpu_bar.setValue(int(float(s.get('cpu') or 0)))
            ram_big.set_target(ram_u)
            ram_bar.setValue(int(ram_u))
            pl_raw = s.get('powerLimit')
            try:
                pl = float(pl_raw) if pl_raw is not None else None
            except (TypeError, ValueError):
                pl = None
            # R33：量程缺失/可疑 → None，不用 140 之类的默认值冒充量程
            if pl is not None and not (20.0 < pl <= 1000.0):
                pl = None
            _pw_v = s.get('power')
            # R32/R33：与曲线同口径，仪表/紧凑行都先用归一的 W 值；无法判定 → None
            if _pw_v is not None:
                _pw_v = xh_norm_power(_pw_v, pl)
            _tp_v = s.get('temp')
            gauge_power.set_value(_pw_v, pl)
            gauge_temp.set_value(_tp_v, 100.0)
            # R15：文本未变就不 setText —— QLabel.setStyleSheet 富文本每次都会重排
            _tp_txt = (_t('<span style="color:@WARN_2@">%s °C</span>　·　'
                       '<span style="color:@OK@">%s W</span>')
                       % ('—' if _tp_v is None else ('%.0f' % float(_tp_v)),
                          '—' if _pw_v is None else ('%.1f' % float(_pw_v))))
            if getattr(lbl_tp_compact, '_last_html', None) != _tp_txt:
                lbl_tp_compact._last_html = _tp_txt
                lbl_tp_compact.setText(_tp_txt)
            # 曲线：增量缓冲（XH-2），1s 均匀刷新
            h = d.get('history', [])
            if not rows_buf:
                _rebuild(h)
            else:
                _merge(h)
            if h:
                empty_n[0] = 0
                last_ts[0] = max(float(x.get('wall') or x.get('ts') or 0) for x in h)
            elif last_ts[0] > 0:
                # 服务端 2 s 采样、客户端 1 s 轮询，空增量属正常；连续 5 拍全空才判定
                # 服务重启（ts 归零，since 永远取不到），下一拍退回全量请求。
                empty_n[0] += 1
                if empty_n[0] >= 5:
                    last_ts[0] = 0.0
                    empty_n[0] = 0
            # R07：没有采样点时必须给可见说明（不能留一块空白图）；有数据即恢复正常
            if not rows_buf:
                _chart_state('暂无数据：还没有采集到性能采样点。'
                             '请确认面板服务在运行；数据到手后曲线会自动出现。')
            else:
                _chart_state('')
            # R36-F6：指标采样已改为跨页常驻（见 apply_page_timers），离开状态页期间
            # 只累积数据、不渲染曲线；回页时由 apply_page_timers 置 drawn[0]=False
            # 触发整帧重绘，曲线因此连续、无 40 s 断档。
            if (rows_buf and 'nochart' not in XH_PROBE and (h or not drawn[0])
                    and stack.currentIndex() == 0):
                # R03：曲线渲染是首页最大开销（14 条 setData 各触发一次场景重绘）。
                # ① 2 s 采样下每 2 拍（4 s）渲染一帧，仍是近实时；② 整帧批处理，
                # 14 次 setData 合并为一次重绘。
                _cd[0] += 1
                _force = not drawn[0]
                drawn[0] = True
                # R04：180 s 长窗口实测 3.79%（R03 为 8.54% 短窗口虚高）；再降一档到 3 拍
                # （6 s）一帧，配合批处理把曲线层占比压到可忽略，同时保持近实时观感。
                # R20-9：规格 12.0±0.2 s 一帧（2 s 采样 × 6 拍）。此前 3 拍=6 s 是未达规格，
                # 不是「旧规格作废」；chartslow 仅作取证对照（18 s）。
                _cbeats = int(os.environ.get('XH_CHART_BEATS', '6') or 6)
                if _force or (_cd[0] % (9 if 'chartslow' in R10 else _cbeats) == 0):

                    def _draw_frame():
                        # R15：帧耗时取证（仅 XH_PROFILE 非空时写；正常启动零开销）
                        _ft0 = time.perf_counter() if XH_PROFILE else 0.0
                        if XH_PROFILE:
                            try:
                                with open(XH_PROFILE, 'a', encoding='utf-8') as _pf:
                                    _pf.write('chartdraw\t%.4f\n' % _ft0)
                            except Exception:
                                _err()
                        _fn = [0]
                        # 跟随态：仅对可见窗口（chart_win+余量）平滑；回看态才全量
                        if follow_live:
                            t_now = rows_buf[-1]['t']
                            lo = t_now - (chart_win + 30)
                            sl = [r for r in rows_buf if r['t'] >= lo]
                        else:
                            sl = rows_buf
                        _fn[0] = len(sl)
                        # 十字光标读数用全量缓冲（索引对齐）
                        for k in SERIES_KEYS:
                            last_series[k] = [[r['t'], r[k]] for r in rows_buf]
                        last_na[:] = [tuple(r.get('na') or ()) for r in rows_buf]
                        for k in SERIES_KEYS:
                            if k == '温度℃':
                                continue
                            c = curves.get(k)
                            if c is None:
                                continue
                            xs = [r['t'] for r in sl]
                            ys = [min(100.0, max(0.0, r[k])) for r in sl]
                            if xs:
                                # 「曲线平滑」设置：原始档不做插值细分，形状保持折线
                                _sub = 2 if XH_ADV.get('smooth', '平滑') == '平滑' else 1
                                dx, dy = smooth_xy(xs, ys, sub=_sub)  # 仅平滑可见窗口
                                c.setData(dx, dy)
                                g = glow_curves.get(k)
                                if g is not None:
                                    g.setData(dx, dy)
                            else:
                                c.setData([], [])
                                g = glow_curves.get(k)
                                if g is not None:
                                    g.setData([], [])
                        # 温度走右侧 ViewBox，Y 轴滞回自适应
                        txs = [r['t'] for r in sl]
                        tys = [max(0.0, r['温度℃']) for r in sl]
                        if txs:
                            tdx, tdy = smooth_xy(txs, tys, sub=2)
                            temp_curve.setData(tdx, tdy)
                            tmaxv = max(tys)
                            _target = max(60.0, tmaxv * 1.2)
                            if _target > temp_ymax[0] * 1.12 or _target < temp_ymax[0] * 0.55:
                                temp_ymax[0] = _target
                                temp_vb.setYRange(0, _target, padding=0)
                        else:
                            temp_curve.setData([], [])
                        cum_wh[:] = [[r['t'], r['cum']] for r in rows_buf]
                        # 仅在秒边界变化时重设跟随窗口，避免回跳/闪烁
                        if follow_live:
                            tmax = rows_buf[-1]['t']
                            if tmax != last_tmax[0]:
                                last_tmax[0] = tmax
                                plot.blockSignals(True)
                                if chart_win and tmax > chart_win:
                                    plot.setXRange(tmax - chart_win, tmax, padding=0)
                                else:
                                    plot.enableAutoRange(axis='x')
                                plot.blockSignals(False)
                        if XH_PROFILE and _ft0:
                            try:
                                with open(XH_PROFILE, 'a', encoding='utf-8') as _f:
                                    _f.write('chart t=%.3f ms=%.2f pts=%d\n'
                                             % (time.time(),
                                                (time.perf_counter() - _ft0) * 1000, _fn[0]))
                            except Exception:
                                _err()

                    plot.setUpdatesEnabled(False)
                    try:
                        _draw_frame()
                    finally:
                        plot.setUpdatesEnabled(True)
                    if XH_PROFILE:
                        # R15：把「真正落到屏幕」的那次绘制也同步测出来（本来就会画，
                        # 只是提前到计时区内），用于把 setData 与 paint 两段分开归因。
                        try:
                            _tp = time.perf_counter()
                            plot.repaint()
                            with open(XH_PROFILE, 'a', encoding='utf-8') as _f:
                                _f.write('chartpaint ms=%.2f\n'
                                         % ((time.perf_counter() - _tp) * 1000))
                        except Exception:
                            _err()
        except Exception:
            _err()

    def on_view_changed(vb, ranges):
        nonlocal follow_live
        if not last_series['显存%']:
            return
        tmax = last_series['显存%'][-1][0]
        right = ranges[0][1]
        _span = max(1.0, ranges[0][1] - ranges[0][0])
        # R015-B2：原阈值含 tmax(epoch 秒) 导致恒真、无法回看；改为按视口跨度判定
        follow_live = abs(right - tmax) < max(5.0, _span * 0.05)

    plot.getViewBox().sigRangeChanged.connect(on_view_changed)

    def back_to_live():
        nonlocal follow_live
        follow_live = True
        if last_series['显存%']:
            tmax = last_series['显存%'][-1][0]
            if chart_win and tmax > chart_win:
                plot.setXRange(tmax - chart_win, tmax, padding=0)

    def reset_zoom():
        nonlocal follow_live
        follow_live = True
        plot.enableAutoRange()
        plot.setYRange(0, 100)
        back_to_live()

    btn_back_live.clicked.connect(back_to_live)
    btn_reset_zoom.clicked.connect(reset_zoom)

    def on_mouse_moved(pos):
        # R36-F3：判界只用 ViewBox——原用整个 plot（含左侧 Y 轴刻度带），鼠标停在
        # 刻度上仍算「在图内」，浮层锚点贴到轴刻度上、被图表左边界裁掉半截。
        if not plot.getViewBox().sceneBoundingRect().contains(pos):
            readout.hide()
            vline.hide()
            hline.hide()
            return
        mp = plot.getViewBox().mapSceneToView(pos)
        idx = nearest_idx(mp.x())
        if idx is None:
            return
        vline.setPos(mp.x())
        hline.setPos(mp.y())
        lines = []
        for k in ('显存%', 'GPU%', '功耗%', '温度℃', '内存%', 'CPU%'):
            data = last_series[k]
            if idx < len(data):
                # R36-F5：该点没有上报 → 显示 —（不再把脏值当 0.0 展示）
                _dirty = idx < len(last_na) and k in last_na[idx]
                lines.append('%s %s' % (k, '—' if _dirty else ('%.1f' % data[idx][1])))
        if idx < len(cum_wh):
            lines.append('累计能耗 %s' % xh_fmt_energy(cum_wh[idx][1]))
        readout.setText('\n'.join(lines))
        # 固定在视图右上角（anchor=(1,0)：文字向左生长），与左侧 Y 轴刻度彻底解耦，
        # 也不会被图表左边界裁切；右侧只留 2% 边距。
        _vr = plot.viewRange()
        _spanx = _vr[0][1] - _vr[0][0]
        _spany = _vr[1][1] - _vr[1][0]
        readout.setPos(_vr[0][1] - _spanx * 0.02, _vr[1][1] - _spany * 0.02)
        readout.show()
        vline.show()
        hline.show()

    plot.scene().sigMouseMoved.connect(on_mouse_moved)

    def _chart_na_probe():
        """R37-2b：只读。把 last_na 里最近一个非空点经真实 on_mouse_moved 端点跑一遍，
        返回光标读数浮层文本——用于证明「后端缺 vram 字段 → 显存% 显示 —」分支生效。"""
        _res = {'rows': len(last_na), 'na_rows': [], 'readout': None,
                'readout_visible': False}
        _idx = None
        for _i in range(len(last_na) - 1, -1, -1):
            if last_na[_i]:
                _idx = _i
                break
        _res['na_rows'] = [{'i': _i, 't': last_series['显存%'][_i][0],
                            'na': list(last_na[_i])}
                           for _i in range(len(last_na))
                           if last_na[_i] and _i < len(last_series['显存%'])][-8:]
        if _idx is not None and _idx < len(last_series['显存%']):
            from PySide6.QtCore import QPointF
            _t = last_series['显存%'][_idx][0]
            _pt = plot.getViewBox().mapViewToScene(QPointF(_t, 50.0))
            plot.scene().sigMouseMoved.emit(_pt)
            _res['idx'] = _idx
            _res['t'] = _t
            _res['readout'] = readout.textItem.toPlainText()
            _res['readout_visible'] = bool(readout.isVisible())
        return _res

    RK = ['all', 'today', 'week', 'month']

    def poll_energy():
        rk = RK[energy_range.currentIndex()]
        qwait(lambda: api('GET', '/api/energy?range=' + rk), _energy_done, key='energy')

    def _energy_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, d = r
            # R34：保留原始缺失去向，交给格式化函数给「—」，不再 `or 0` 猜成 0
            wh = d.get('wh')
            dur = d.get('dur')
            avg = d.get('avg')
            n = int(d.get('n') or 0)
            # X17：数字与单位间用不间断空格，W 不单独折行
            # R15：同文案不 setText（能耗卡 50 s 一拍，避免无谓重排/重绘）
            _e1 = xh_fmt_energy(wh)
            if getattr(lbl_energy, '_last_txt', None) != _e1:
                lbl_energy._last_txt = _e1
                lbl_energy.setText(_e1)
            # R34：首屏副行原本「有载 5071分」——分钟越界；统一走 xh_fmt_dur 逐级进位
            _e2 = '有载 %s\u00A0·\u00A0均 %sW' % (
                xh_fmt_dur(dur), fnum(avg, 1) if avg is not None else '—')
            if getattr(lbl_energy2, '_last_txt', None) != _e2:
                lbl_energy2._last_txt = _e2
                lbl_energy2.setText(_e2)
        except Exception:
            _err()

    # 磁盘轮询已移除（XH-5）；GPU 时钟在 _sv_done 中更新

    def poll_sv():
        qwait(lambda: api('GET', '/api/state'), _sv_done, key='state')

    _sv_err = [0]   # R07：托管服务状态连续失败计数
    _sv_ok = [0]

    def _sv_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                _sv_err[0] += 1
                if _sv_ok[0] and _sv_err[0] >= 2:
                    lbl_sv.setText('托管服务状态读取失败（连续 %d 次）：面板服务无响应。'
                                   '请到「配置 → 运行日志」检查面板服务，或重启面板后按 F5 刷新'
                                   % _sv_err[0])
                elif not _sv_ok[0]:
                    lbl_sv.setText('正在启动：等待面板服务就绪…')
                return
            _sv_err[0] = 0
            _sv_ok[0] = 1
            _code, d = r
            if not isinstance(d, dict):
                lbl_sv.setText('托管服务状态读取失败：面板响应不是 JSON。'
                               '常见原因是 8899 端口被其他程序占用；请关闭占用程序后重启面板')
                return
            # port1234 是布尔（KVMem 是否在跑）；lms/comfy/proxy 是 dict
            p1234 = d.get('port1234')
            kv_run = p1234.get('running') if isinstance(p1234, dict) else bool(p1234)
            lms = d.get('lms') or {}
            lms_run = lms.get('running') if isinstance(lms, dict) else False
            cf = d.get('comfy') or {}
            cf_on = bool(cf.get('alive') or cf.get('ready'))
            cf_ready = not cf_on and bool(cf.get('present'))
            proxy = d.get('proxy') or {}
            proxy_run = bool(proxy.get('running'))
            # X2：无 Comfy 在线→释放置灰；无 busy 任务→中断置灰
            btn_free.setEnabled(bool(cf_on))
            btn_interrupt.setEnabled(bool(cf_on and cf.get('busy')))
            # R15-X06：全停状态（无任何服务在跑）→「停止全部服务」置灰，且飞行中不干扰
            if not getattr(w, '_stopall_flight', False):
                btn_stopall.setEnabled(bool(kv_run or lms_run or cf_on or proxy_run))
            okc = UI_C['OK']
            badc = UI_C['INK_3']
            # R15：同文案不再 setText（富文本重排会带出整卡重绘）
            _sv_txt = ('<span style="color:%s">●</span> KVMem %s　<span style="color:%s">●</span> LM Studio %s　'
                       '<span style="color:%s">●</span> ComfyUI %s　<span style="color:%s">●</span> 保活代理 %s'
                       % (okc if kv_run else badc, '运行' if kv_run else '停止',
                          okc if lms_run else badc, '运行' if lms_run else '停止',
                          okc if cf_on else badc,
                          ('在线' if cf_on else ('离线 · 模型已就位' if cf_ready else '离线')),
                          okc if proxy_run else badc, '运行' if proxy_run else '停止'))
            if getattr(lbl_sv, '_last_html', None) != _sv_txt:
                lbl_sv._last_html = _sv_txt
                lbl_sv.setText(_sv_txt)
            # GPU 核心/显存时钟（XH-5 替换磁盘卡）——X3：仅在状态页可见时刷新
            if stack.currentIndex() == 0:
                sysinfo = d.get('sys') or {}
                # R33（量纲审计 BUG-C）：越界/缺失时钟显示「未知」，不显示 65535 之类错数
                _t = xh_fmt_clk(sysinfo.get('clkSm'), CLK_SM_MAX)
                if getattr(lbl_sm, '_last_txt', None) != _t:
                    lbl_sm._last_txt = _t
                    lbl_sm.setText(_t)
                _tm = xh_fmt_clk(sysinfo.get('clkMem'), CLK_MEM_MAX)
                if getattr(lbl_memclk, '_last_txt', None) != _tm:
                    lbl_memclk._last_txt = _tm
                    lbl_memclk.setText(_tm)
        except Exception:
            _err()

    def act_free():
        btn_free.setEnabled(False)
        # 后端 cf_free 最坏 ≈40s（unload 15s + sleep 4 + taskkill 15s + sleep 5），默认 8s 会假超时
        qwait(lambda: _safe_post('/api/comfy/free', timeout=60),
              lambda _r: btn_free.setEnabled(True))

    def act_interrupt():
        btn_interrupt.setEnabled(False)
        # 后端自身 urlopen 就给了 8s，客户端必须略大于它，否则先于后端超时
        qwait(lambda: _safe_post('/api/comfy/interrupt', timeout=15),
              lambda _r: btn_interrupt.setEnabled(True))

    def act_stopall():
        if QMessageBox.question(w, '确认', '停止全部托管服务（KVMem/LM Studio/ComfyUI/代理）？') == QMessageBox.Yes:
            btn_stopall.setEnabled(False)
            w._stopall_flight = True  # R15-X06：飞行标志，_sv_done 置灰同步不干扰
            # 后端 stop_all = kv_stop(≤25s) + lms + cf_stop(≤100s) + proxy，最坏 ≈130s
            qwait(lambda: _safe_post('/api/stopall', timeout=180),
                  lambda _r: (setattr(w, '_stopall_flight', False),
                              btn_stopall.setEnabled(True)))

    btn_free.clicked.connect(act_free)
    btn_interrupt.clicked.connect(act_interrupt)
    btn_stopall.clicked.connect(act_stopall)
    energy_range.currentIndexChanged.connect(poll_energy)

    _perf_mark('tab0_e')

    return {
        'BUF_CAP': BUF_CAP,
        '_chart_na_probe': _chart_na_probe,
        '_metrics_done': _metrics_done,
        '_sv_done': _sv_done,
        'back_to_live': back_to_live,
        'btn_free': btn_free,
        'btn_interrupt': btn_interrupt,
        'btn_stopall': btn_stopall,
        'cpu_big': cpu_big,
        'gpu_big': gpu_big,
        'mem_big': mem_big,
        'ram_big': ram_big,
        'lbl_chart_state': lbl_chart_state,
        'lbl_energy': lbl_energy,
        'lbl_energy2': lbl_energy2,
        'lbl_memclk': lbl_memclk,
        'lbl_sm': lbl_sm,
        'lbl_sv': lbl_sv,
        'lbl_tp_compact': lbl_tp_compact,
        'gauge_power': gauge_power,
        'plot': plot,
        'curves': curves,
        'glow_curves': glow_curves,
        'temp_curve': temp_curve,
        'rows_buf': rows_buf,
        'drawn': drawn,
        'last_ts': last_ts,
        'poll_energy': poll_energy,
        'poll_metrics': poll_metrics,
        'poll_sv': poll_sv,
        'chart_win': chart_win,
    }

