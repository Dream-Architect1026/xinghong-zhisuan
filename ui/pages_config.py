# -*- coding: utf-8 -*-
"""UI 页面：Tab「配置中心」（C1-B2 第 6 批）。

原位置：xinghong_launcher.py build_ui() 内两段，整段原样搬出：
  · L7627–8327：配置中心四标签（数据设置 / 生成记录 / 运行日志 / 进阶配置）
    + 「说明与协议」二级页面（R255）+ 惰性构建钩子（R253）；
  · L8925–9210：数据设置回调 / CSV 导出 / 配置页四条轮询（poll_cfg / poll_jobs_tbl /
    poll_health / poll_cfg_logs）。第二段原排在「小星」页之后，仅书写顺序如此；
    两段之间无数据依赖，搬入后按「构建 → 回调/轮询」排列，执行语义不变。

分层：ui 层，只依赖 PySide6 / 标准库 / ux_core（同为 ui 层）；launcher 的控件工厂、
私有工具、主题令牌、状态快照一律显式传参，禁止反向 import launcher。

导航逻辑一行未动（硬约束）：
  · 「说明与协议」入口在 ux.AdvPanel 内（进阶配置分组下一行），点击 → on_legal →
    w._xh_open_legal()；_build_legal_page / _xh_open_legal / _xh_close_legal 原样保留；
  · 返回时停在进入前的 stack 页 + cfg_top 分区 + 每个 QAbstractScrollArea 的滚动值
    （_legal_back 三元组，逐字保留）；
  · 页头只显示协议版本（consent_version_line），产品版本不在本页显示——
    ux.legal_page(...) 的 page_head(meta=...) 未改。

QSettings 语义一行未动：本页直接读写 closeMode / export_dir（QSettings('X.H','XingHongPanel')）；
进阶配置一律走 ux.Prefs('X.H','XingHongPanel') 的 adv/* 键（默认值取自 ux.DEFAULTS）。
逐项清单见 docs/switches.md「B2-6 配置中心 QSettings 读写表」。

返回值只暴露 launcher 后续确实引用的句柄：小星页复用 adv_prefs / adv_secrets；
t13/t14/t15、apply_page_timers、_refresh_current 用四条轮询；_state_done 用 _cfg_done；
管道 shot 5 / cfg1_jobs 用 cfg_top / cfg_jobs_tbl；子页切换刷新用 cfg_sub_changed。
"""
import json
import os
import re
import subprocess
import sys

from PySide6.QtCore import QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractScrollArea, QComboBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from infra.config import XH_ADV, XH_CADENCE
import pathcfg as _pathcfg

__all__ = ['build_page_config']


def build_page_config(*, stack, w, api, qwait, _err, _perf_mark,
                      _t, _nav_icon, _style_form_labels, _state_dot, get_bytes,
                      ux, xh_time, page_cap, _cfg_tab_now,
                      BUF_CAP, rows_buf, back_to_live,
                      _xh_cadence, _xh_apply_theme,
                      CANONICAL_DIR, UI_C, CARD_BG, CARD_BD, R_CARD, R_INNER,
                      TXT2, TXT3, TXT4, SCROLL_V, SCROLL_H):
    """Tab「配置中心」页面构建（C1-B2 第 6 批，从 build_ui 原样搬出）。

    参数名与 build_ui 内原标识符逐字一致（机械等价可逐行比对）。
    `page_cap` / `_cfg_tab_now` 是页头联动用的 launcher 控件与共享列表：
    本页只按原代码写入（cfg 子标签名），页头 _sync_head 的判定逻辑留在 launcher。
    """

    # ============ Tab 6: 配置中心（四标签，对齐网页 cfgModal） ============
    _perf_mark('tab5_b')
    ct = QWidget()
    cv2 = QVBoxLayout(ct)
    cv2.setContentsMargins(4, 8, 4, 4)
    # R036（领导）：取消"常规设置"父层——原子页整体提升为顶层项，单级 Tab 承载
    # （原 cfgTop[常规设置 → cfgInner[数据设置/生成记录/运行日志]] 压平为
    #   cfgTop[数据设置/生成记录/运行日志/进阶配置/使用说明/隐私说明/用户协议]）
    # R253：惰性构建开关。on（默认）= 进阶配置面板 + 三个文档页在首次点开对应子标签
    # 时才构建（实测这两处占配置中心构建耗时 96%，且启动时都不可见）；legacy = 启动时全建。
    # 两条路径在同一个包里，A/B 用同一个 exe 改环境变量即可。
    _LAZY_CFG = (os.environ.get('XH_LAZY_PAGES', 'on').strip().lower() != 'legacy')
    cfg_top = QTabWidget()
    cfg_top.setObjectName('cfgTop')
    cfg_top.setDocumentMode(True)
    # 顶层项较多：禁滚动箭头、宽度不足时文字省略——不出横向滚动条
    cfg_top.setUsesScrollButtons(False)
    cfg_top.setElideMode(Qt.ElideRight)
    cv2.addWidget(cfg_top)

    # R23-B1：配置中心 7 个子页的页级标题 = 「配置中心 · 当前 Tab」
    _LAZY_HOOK = [None]      # 构建函数定义在下方；首个 addTab 会立刻发 currentChanged(0)
    def _cfg_tab_changed(i):
        try:
            _cfg_tab_now[0] = cfg_top.tabText(i)
            if stack.currentIndex() == 5:
                page_cap.setText(_cfg_tab_now[0])
            # R253：切到还没构建的子标签时先建（不可见时构建，成本不落在切页那帧）
            if _LAZY_CFG and _LAZY_HOOK[0] is not None:
                _LAZY_HOOK[0](i)
        except Exception:
            _err()
    cfg_top.currentChanged.connect(_cfg_tab_changed)

    # ---- 子标签 1：数据设置 ----
    p0 = QWidget()
    p0h = QHBoxLayout(p0)
    p0h.setContentsMargins(12, 12, 12, 8)
    p0h.setSpacing(12)
    # 左列：参数表单 + 存储位置 + 导出按钮
    p0_left = QFrame()
    p0_left.setStyleSheet(
        'QFrame { background: %s;'
        'border:1px solid %s; border-radius:%dpx; }' % (CARD_BG, CARD_BD, R_CARD))
    p0v = QVBoxLayout(p0_left)
    p0v.setContentsMargins(16, 16, 16, 12)
    p0v.setSpacing(8)
    p0f = QFormLayout()
    set_iv = QComboBox()
    set_iv.addItems(['1 秒', '2 秒', '3 秒', '5 秒'])
    set_win = QComboBox()
    set_win.addItems(['1 分钟', '3 分钟', '10 分钟', '全部'])
    set_store = QComboBox()
    set_store.addItems(['永久保存 · 默认', '限时保留'])
    set_rh = QComboBox()
    set_rh.addItems(['6 小时', '12 小时', '24 小时', '72 小时'])
    set_rh.setEnabled(False)
    p0f.addRow('采样间隔（曲线精度）', set_iv)
    p0f.addRow('曲线显示窗口', set_win)
    p0f.addRow('数据存储方式', set_store)
    p0f.addRow('监控保留时长', set_rh)
    p0v.addLayout(p0f)
    # 存储位置：改为可点击的「打开目录」按钮（不再是一坨纯文本路径）
    # 目录一律由本机路径配置推导，仓库内不含绝对路径
    DIR_TARGETS = {
        '监控数据': _pathcfg.data_dir(),
        '生图输出': _pathcfg.comfy_out('photo'),
        '生视频输出': _pathcfg.comfy_out('video'),
    }
    dir_row = QHBoxLayout()
    dir_row.setSpacing(8)
    dir_cap = QLabel('存储位置：')
    dir_cap.setObjectName('sub')
    dir_row.addWidget(dir_cap)

    def open_folder(p):
        def _do():
            try:
                if not p:
                    w._toast.show('未配置目录（见 paths.json 或环境变量）')
                elif os.path.exists(p):
                    subprocess.Popen(['explorer', p])
                else:
                    w._toast.show('目录不存在：%s' % p)
            except Exception:
                _err()
        return _do

    for dname, dpath in DIR_TARGETS.items():
        db = QPushButton(dname)
        db.setObjectName('ghost')
        db.setCursor(Qt.PointingHandCursor)
        db.setMinimumWidth(92)
        db.setFixedHeight(35)           # R24-V2：34→35，与 primary 同高
        db.setToolTip('打开' + dname + '目录')
        db.clicked.connect(open_folder(dpath))
        dir_row.addWidget(db)
    dir_row.addStretch(1)
    p0v.addLayout(dir_row)
    # R018（领导）：关闭行为入设置——最小化到托盘 / 退出应用
    _cmrow = QHBoxLayout()
    _cmrow.setSpacing(8)
    _cmcap = QLabel('关闭行为：')
    _cmrow.addWidget(_cmcap)
    _cm = QComboBox()
    _cm.addItems(['每次询问', '最小化到托盘', '退出应用'])
    _cm.setMaximumWidth(130)
    _xsets = QSettings('X.H', 'XingHongPanel')
    _cm.setCurrentIndex({'ask': 0, 'tray': 1, 'exit': 2}.get(
        str(_xsets.value('closeMode', 'ask')), 0))

    def _on_close_mode(i):
        _xsets.setValue('closeMode', ('ask', 'tray', 'exit')[i])
    _cm.currentIndexChanged.connect(_on_close_mode)
    _cmrow.addWidget(_cm)
    _cmrow.addStretch(1)
    p0v.addLayout(_cmrow)
    set_save_b = QPushButton('保存设置')
    set_csv1 = QPushButton('监控 CSV')
    set_csv1.setToolTip('导出监控 CSV')
    set_csv2 = QPushButton('任务 CSV')
    set_csv2.setToolTip('导出任务 CSV')
    set_csv3 = QPushButton('功耗 CSV')
    set_csv3.setToolTip('导出功耗日 CSV')
    set_dir_b = QPushButton('存储路径')
    set_dir_b.setToolTip('选择 CSV/导出默认存储路径')
    # R017（领导）：5 钮一行挤压成段 → 拆两行分组，留呼吸间距
    set_row_a = QHBoxLayout()
    set_row_a.setSpacing(8)
    for b in (set_save_b, set_csv1, set_csv2):
        b.setMinimumWidth(84)
        b.setMaximumWidth(120)
        set_row_a.addWidget(b)
    set_row_a.addStretch(1)
    p0v.addLayout(set_row_a)
    set_row_b = QHBoxLayout()
    set_row_b.setSpacing(8)
    for b in (set_csv3, set_dir_b):
        b.setMinimumWidth(84)
        b.setMaximumWidth(120)
        set_row_b.addWidget(b)
    set_row_b.addStretch(1)
    p0v.addLayout(set_row_b)

    _xprefs = QSettings('X.H', 'XingHongPanel')

    def _pick_export_dir_xh():
        _cur = str(_xprefs.value('export_dir', '') or DATA_DIR)
        d = QFileDialog.getExistingDirectory(w, '选择导出存储路径', _cur)
        if d:
            _xprefs.setValue('export_dir', d)
            w._toast.show('导出存储路径已更新：%s' % d, 'ok')  # R15-X17：statusBar 改 toast
    set_dir_b.clicked.connect(_pick_export_dir_xh)

    # 设置说明（填补左列空白，解释每项设置）
    help_box = QFrame()
    help_box.setStyleSheet(
        'QFrame { background: %s;'
        'border:1px solid %s; border-radius:%dpx; }' % (CARD_BG, CARD_BD, R_CARD))
    hv = QVBoxLayout(help_box)
    hv.setContentsMargins(12, 12, 12, 8)
    hv.setSpacing(4)
    help_title = QLabel('设置说明')
    help_title.setStyleSheet('font-size:15px; font-weight:700; color:%s;'
                             'background:transparent; border:none;' % TXT3)
    hv.addWidget(help_title)
    help_items = [
        ('采样间隔', '曲线精度，间隔越小数据越精细、占用越大'),
        ('显示窗口', '状态页曲线展示的时间范围'),
        ('存储方式', '永久保存全部数据，或限时保留自动清理'),
        ('保留时长', '限时保留模式下，超出周期的数据将被清理'),
    ]
    for name, desc in help_items:
        hrow = QHBoxLayout()
        hn = QLabel(name)
        hn.setStyleSheet('color:%s; font-weight:600; min-width:56px;'
                         'background:transparent; border:none;' % TXT2)
        hd = QLabel(desc)
        hd.setStyleSheet('color:%s; background:transparent; border:none;' % TXT4)
        hd.setWordWrap(True)
        hrow.addWidget(hn)
        hrow.addWidget(hd, 1)
        hv.addLayout(hrow)
    p0v.addWidget(help_box)
    p0v.addStretch(1)
    p0h.addWidget(p0_left, 5)

    # ---- 右列：数据存储概览（占满高度，真实读取文件大小/行数）----
    ov_box = QFrame()
    ov_box.setStyleSheet(
        'QFrame { background: %s;'
        'border:1px solid %s; border-radius:%dpx; }' % (CARD_BG, CARD_BD, R_CARD))
    ov_grid = QGridLayout(ov_box)
    ov_grid.setContentsMargins(16, 16, 16, 12)
    ov_grid.setHorizontalSpacing(16)
    ov_grid.setVerticalSpacing(8)
    ov_title = QLabel('数据存储概览')
    ov_title.setStyleSheet('font-size:15px; font-weight:700; color:%s;'
                           'background:transparent; border:none;' % TXT3)
    # R28-V19：与生图/视频/放大页的卡标题线稿同口径（18px、1.6px 笔触）
    ov_title_icon = QLabel()
    ov_title_icon.setFixedSize(18, 18)
    ov_title_icon.setPixmap(_nav_icon('chart', 18, UI_C['INK_2']).pixmap(18, 18))
    ov_title_icon.setStyleSheet('background:transparent; border:none;')
    # X11：补刷新按钮（行数/大小统计移入后台线程，先显"统计中…"）
    ov_refresh_b = QPushButton('刷新')
    ov_refresh_b.setIcon(_nav_icon('refresh', 12, UI_C['INK_1']))
    ov_refresh_b.setIconSize(QSize(12, 12))
    ov_title_row = QHBoxLayout()
    ov_title_row.setSpacing(8)
    ov_title_row.addWidget(ov_title_icon)
    ov_title_row.addWidget(ov_title)
    ov_title_row.addStretch(1)
    ov_title_row.addWidget(ov_refresh_b)
    ov_grid.addLayout(ov_title_row, 0, 0, 1, 2)

    def _human(n):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024 or unit == 'GB':
                return '%.1f %s' % (n, unit) if unit != 'B' else '%d B' % n
            n /= 1024.0

    # X18：数据目录随 CANONICAL_DIR 推导，不再硬编码
    # R15-X09：数据目录迁移到 %LOCALAPPDATA%\XingHongPanel\data（onedir 部署下随
    # exe 目录会被更新覆盖）；首启自动迁移旧目录数据（只复制不删源）
    _new_data = os.path.join(os.path.expandvars('%LOCALAPPDATA%'), 'XingHongPanel', 'data')
    try:
        os.makedirs(_new_data, exist_ok=True)
    except Exception:
        _err()
    _old_data = os.path.join(os.path.dirname(CANONICAL_DIR), 'data')
    if os.path.isdir(_old_data) and os.path.abspath(_old_data) != os.path.abspath(_new_data):
        try:
            import shutil as _sh
            for _fn in os.listdir(_old_data):
                _sp = os.path.join(_old_data, _fn)
                _dp = os.path.join(_new_data, _fn)
                if os.path.isfile(_sp) and not os.path.exists(_dp):
                    try:
                        _sh.copy2(_sp, _dp)
                    except Exception:
                        _err()
        except Exception:
            _err()
    DATA_DIR = _new_data
    ov_files = [
        ('metrics.jsonl', '性能指标'), ('jobs.jsonl', '任务记录'),
        ('favorites.json', '收藏'), ('settings.json', '当前设置'),
    ]
    ov_cells = {}
    row_i = 1
    for fn, cap in ov_files:
        fp = os.path.join(DATA_DIR, fn)
        if not os.path.exists(fp):
            row_i += 1
            continue
        cell = QFrame()
        cell.setStyleSheet(
            'QFrame { background: %s;'
            'border:1px solid %s; border-radius:%dpx; }' % (CARD_BG, CARD_BD, R_INNER))
        ch = QVBoxLayout(cell)
        ch.setContentsMargins(8, 8, 8, 8)
        ch.setSpacing(4)
        top = QHBoxLayout()
        c0 = QLabel(cap)
        c0.setStyleSheet(_t('color:@INK_2@; background:transparent; border:none;'))
        c2 = QLabel(fn)
        c2.setStyleSheet(_t('color:@INK_2@; background:transparent; border:none;'))
        top.addWidget(c0)
        top.addStretch(1)
        top.addWidget(c2)
        c1 = QLabel('统计中…')  # X11：先显占位，后台统计完成后回填
        c1.setStyleSheet(_t('font-size:15px; color:@INK_0@; font-weight:700;'
                         'background:transparent; border:none;'))
        ch.addLayout(top)
        ch.addWidget(c1)
        ov_grid.addWidget(cell, row_i, 0, 1, 2)
        ov_cells[fn] = c1
        row_i += 1

    def _refresh_ov():
        # X11：行数/大小统计移入 WORKER_POOL（先显"统计中…"），不再卡构建
        for _fn, _lbl in ov_cells.items():
            _lbl.setText('统计中…')
        try:
            footer_f1.setText('数据目录总大小：统计中…')
        except Exception:
            _err()

        def work():
            res = {}
            _total = 0
            for _fn in ov_cells:
                _fp = os.path.join(DATA_DIR, _fn)
                try:
                    _size = os.path.getsize(_fp)
                    _total += _size
                    _extra = ''
                    if _fn.endswith('.jsonl'):
                        with open(_fp, 'rb') as fh:
                            _n = sum(1 for _ in fh)
                        _extra = ' · %d 条' % _n
                    res[_fn] = '%s%s' % (_human(_size), _extra)
                except Exception:
                    res[_fn] = '—'
            try:
                for _f in os.listdir(DATA_DIR):
                    _fp = os.path.join(DATA_DIR, _f)
                    if os.path.isfile(_fp):
                        _total += os.path.getsize(_fp)
            except Exception:
                _err()
            res['__total__'] = _human(_total)
            return res

        def done(res):
            for _fn, _txt in (res or {}).items():
                if _fn in ov_cells:
                    ov_cells[_fn].setText(_txt)
            if res and '__total__' in res:
                try:
                    footer_f1.setText('数据目录总大小：%s' % res['__total__'])
                except Exception:
                    _err()

        qwait(work, done)

    ov_refresh_b.clicked.connect(_refresh_ov)

    for r in range(1, row_i):
        ov_grid.setRowStretch(r, 0)
    # 系统健康并入数据设置（XH-9）：弹性区放健康评分 + 检查项
    health_card = QFrame()
    health_card.setStyleSheet(
        'QFrame { background: %s;'
        'border:1px solid %s; border-radius:%dpx; }' % (CARD_BG, CARD_BD, R_CARD))
    hcv = QVBoxLayout(health_card)
    hcv.setContentsMargins(12, 12, 12, 8)
    hcv.setSpacing(4)
    hc_title = QLabel('系统健康')
    hc_title.setStyleSheet('font-size:15px; font-weight:700; color:%s;'
                           'background:transparent; border:none;' % TXT3)
    # R19 复测补齐：2 列网格每列仅 129px，健康项文案（最长约 180px）被右缘硬切。
    # 分数上提到标题行右侧，检查项改单列（274px 全宽），行数 7 固定，纵向净增 6px 可容纳。
    hc_top = QHBoxLayout()
    hc_top.setContentsMargins(0, 0, 0, 0)
    hc_top.setSpacing(8)
    # R28-V20：健康卡补盾牌线稿，与同页「数据存储概览」保持同层级
    hc_title_icon = QLabel()
    hc_title_icon.setFixedSize(18, 18)
    hc_title_icon.setPixmap(_nav_icon('shield', 18, UI_C['INK_2']).pixmap(18, 18))
    hc_title_icon.setStyleSheet('background:transparent; border:none;')
    hc_top.addWidget(hc_title_icon)
    hc_top.addWidget(hc_title)
    hc_top.addStretch(1)
    health_score_lbl = QLabel('—')
    health_score_lbl.setStyleSheet(_t('font-size:22px; font-weight:700; color:@OK@;'
                                   'background:transparent; border:none;'))
    health_score_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    hc_top.addWidget(health_score_lbl)
    hcv.addLayout(hc_top)
    # R15-X07/X15：健康检查分项恢复显示（round_014 曾整组隐藏）——每项独立 label + wordWrap
    hc_grid = QGridLayout()
    hc_grid.setContentsMargins(0, 0, 0, 0)
    hc_grid.setHorizontalSpacing(16)
    hc_grid.setVerticalSpacing(4)
    hcv.addLayout(hc_grid)
    hcv.addStretch(1)
    ov_grid.addWidget(health_card, row_i, 0, 1, 2)
    ov_grid.setRowStretch(row_i, 1)  # 健康区弹性填充
    footer = QFrame()
    footer.setStyleSheet('background:transparent; border:none;')
    fv = QVBoxLayout(footer)
    fv.setContentsMargins(4, 8, 4, 4)
    fv.setSpacing(4)
    footer_f1 = QLabel('数据目录总大小：统计中…')
    footer_f1.setStyleSheet('font-size:15px; font-weight:700; color:%s;'
                            'background:transparent; border:none;' % TXT3)
    f2 = QLabel(DATA_DIR)
    f2.setStyleSheet('color:%s; background:transparent; border:none;' % TXT4)
    f2.setWordWrap(True)
    fv.addWidget(footer_f1)
    fv.addWidget(f2)
    ov_grid.addWidget(footer, row_i + 1, 0, 1, 2)
    p0h.addWidget(ov_box, 4)
    _style_form_labels(p0f)             # R24-V4：表单标签 12px + 次级色
    cfg_top.addTab(p0, '数据设置')
    # X11：构建完成后立即触发一次后台统计（先显"统计中…"）
    _refresh_ov()

    # ---- 子标签 2：生成记录 ----
    p1 = QWidget()
    p1v = QVBoxLayout(p1)
    p1v.setContentsMargins(12, 12, 12, 8)
    cfg_jobs_tbl = QTableWidget(0, 6)
    cfg_jobs_tbl.setHorizontalHeaderLabels(['时间', '类型', '状态', '文件', '耗时(s)', '参数'])
    _jh = cfg_jobs_tbl.horizontalHeader()
    _jh.setHighlightSections(False)
    _jh.setStretchLastSection(False)
    _jh.setSectionResizeMode(QHeaderView.Interactive)
    _jh.setSectionResizeMode(5, QHeaderView.Stretch)
    # R28：状态列 72→94，给 10px 状态圆点留位，避免「success」被省略成「succ…」
    for _ci, _cw in enumerate((96, 72, 94, 168, 72)):
        cfg_jobs_tbl.setColumnWidth(_ci, _cw)
    cfg_jobs_tbl.setTextElideMode(Qt.ElideRight)
    cfg_jobs_tbl.setIconSize(QSize(10, 10))
    cfg_jobs_tbl.setWordWrap(False)
    cfg_jobs_tbl.setShowGrid(False)
    cfg_jobs_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
    cfg_jobs_tbl.setSelectionMode(QTableWidget.NoSelection)
    cfg_jobs_tbl.setFocusPolicy(Qt.NoFocus)
    cfg_jobs_tbl.verticalHeader().setVisible(False)
    cfg_jobs_tbl.verticalHeader().setDefaultSectionSize(30)
    cfg_jobs_tbl.setVerticalScrollBarPolicy(SCROLL_V)
    cfg_jobs_tbl.setHorizontalScrollBarPolicy(SCROLL_H)
    cfg_jobs_note = QLabel('')
    cfg_jobs_note.setStyleSheet('font-size:12px; color:%s; background:transparent; border:none;'
                                % TXT4)
    p1v.setSpacing(8)
    p1v.addWidget(cfg_jobs_tbl, 1)
    p1v.addWidget(cfg_jobs_note)
    cfg_top.addTab(p1, '生成记录')
    # 系统·健康独立标签已删除（XH-9），健康内容已并入「数据设置」右侧弹性区

    # ---- 子标签 3：运行日志（左导航独立日志页已删除，日志统一在此） ----
    p3 = QWidget()
    p3v = QVBoxLayout(p3)
    p3v.setContentsMargins(12, 12, 12, 8)
    cfg_kv_chip = QLabel('KVMem · 长上下文 LLM —')
    cfg_kv_chip.setObjectName('sub')
    cfg_kv_log = QPlainTextEdit()
    cfg_kv_log.setReadOnly(True)
    cfg_kv_log.setMaximumBlockCount(800)
    cfg_kv_log.setLineWrapMode(QPlainTextEdit.WidgetWidth)
    cfg_kv_log.setVerticalScrollBarPolicy(SCROLL_V)
    cfg_kv_log.setHorizontalScrollBarPolicy(SCROLL_H)
    cfg_cf_chip = QLabel('ComfyUI · 生图 / 生视频 —')
    cfg_cf_chip.setObjectName('sub')
    cfg_cf_log = QPlainTextEdit()
    cfg_cf_log.setReadOnly(True)
    cfg_cf_log.setMaximumBlockCount(800)
    cfg_cf_log.setLineWrapMode(QPlainTextEdit.WidgetWidth)
    cfg_cf_log.setVerticalScrollBarPolicy(SCROLL_V)
    cfg_cf_log.setHorizontalScrollBarPolicy(SCROLL_H)
    for wgt in (cfg_kv_chip, cfg_kv_log, cfg_cf_chip, cfg_cf_log):
        p3v.addWidget(wgt)
    p3v.setStretch(1, 1)
    p3v.setStretch(3, 1)
    cfg_top.addTab(p3, '运行日志')
    # ===== R024：进阶配置（内嵌，不再弹独立窗口）+ 使用说明/隐私/协议 =====
    adv_prefs = ux.prefs()
    adv_secrets = ux.SecretStore(adv_prefs)
    p4 = QWidget()
    v4 = QVBoxLayout(p4)
    v4.setContentsMargins(8, 8, 8, 8)
    v4.setSpacing(8)

    def _xh_apply_adv(vals, silent=False):
        lvl = str(vals.get('animLevel') or '标准')
        XH_ADV['anim'] = 0 if lvl == '关闭' else (120 if lvl == '降级' else 260)
        XH_ADV['reduce'] = bool(vals.get('reduceMotion')) or lvl in ('降级', '关闭')
        XH_ADV['perf'] = str(vals.get('perfMode') or '均衡')
        XH_ADV['theme'] = str(vals.get('theme') or '原版深色')
        # B5：进阶项从"只存不用"改为真实生效
        try:
            XH_ADV['pollInterval'] = max(1, min(10, int(vals.get('pollInterval') or 2)))
        except Exception:
            XH_ADV['pollInterval'] = 2
        try:
            XH_ADV['chartPoints'] = max(100, min(2000, int(vals.get('chartPoints') or 1200)))
        except Exception:
            XH_ADV['chartPoints'] = 1200
        XH_ADV['smooth'] = str(vals.get('smooth') or '平滑')
        XH_ADV['logLevel'] = str(vals.get('logLevel') or 'INFO')
        XH_ADV['notify'] = bool(vals.get('notifyEnabled', True))
        XH_ADV['dnd'] = bool(vals.get('dnd'))
        w._notify_prefs = {'enabled': XH_ADV['notify'], 'dnd': XH_ADV['dnd']}
        try:
            BUF_CAP[0] = XH_ADV['chartPoints']
            if len(rows_buf) > BUF_CAP[0]:
                del rows_buf[:-BUF_CAP[0]]
        except Exception:
            _err()
        # R20-8：旋钮必须真的改运行时——按唯一真源重算全部定时器间隔并立即下发；
        # 启动期定时器尚未创建时（getattr 为空）只更新 XH_CADENCE，创建处读它。
        try:
            _cad = _xh_cadence()
            XH_CADENCE.clear(); XH_CADENCE.update(_cad)
            _tms = getattr(w, '_poll_timers', None)
            if _tms:
                for _nm, _tm in _tms.items():
                    if _tm.interval() != _cad[_nm]:
                        _tm.setInterval(_cad[_nm])
        except Exception:
            _err()
        # R247：主题真做——五类令牌（背景/卡片/导航/页脚/正文）一起换，
        # 不再只下发两条 QLabel 规则。原版深色 = 空串，恢复既有渲染。
        try:
            _xh_apply_theme(w, XH_ADV['theme'])
        except Exception:
            _err()
        # 性能模式：省电档降低过渡时长与缩略图优先级；高性能保持标准
        if XH_ADV['perf'] == '省电':
            XH_ADV['anim'] = min(XH_ADV['anim'], 120)
            XH_ADV['reduce'] = True
        # 只读启动应用（silent）时不写注册表，避免用源码启动时把自启项改成 python.exe
        if not silent:
            try:
                import winreg
                _exe = sys.executable
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                   r'Software\Microsoft\Windows\CurrentVersion\Run', 0,
                                   winreg.KEY_SET_VALUE)
                if vals.get('autoStart'):
                    winreg.SetValueEx(k, 'XingHongPanel', 0, winreg.REG_SZ, '"%s"' % _exe)
                else:
                    try:
                        winreg.DeleteValue(k, 'XingHongPanel')
                    except Exception:
                        _err()
                winreg.CloseKey(k)
            except Exception:
                _err()
        if not silent:
            w._notify_prefs = {'enabled': bool(vals.get('notifyEnabled')),
                               'dnd': bool(vals.get('dnd'))}
            w._toast.show('进阶配置已应用：动画 %s · 性能 %s · 主题 %s · 轮询 %ss · 曲线 %s'
                          % (lvl, XH_ADV['perf'], XH_ADV['theme'],
                             XH_ADV['pollInterval'], XH_ADV['smooth']), 'ok')

    adv_panel_x = None
    _adv_ph = [None]

    def _adv_vals_from_prefs():
        """AdvPanel.collect() 的无控件等价物（R253）。"""
        out = {}
        for _gn, _items in ux.SPEC:
            for _k, _lab, _kind, _arg in _items:
                _v = adv_prefs.get(_k)
                if _kind == 'choice':
                    out[_k] = (str(_v) if str(_v) in _arg
                               else str(ux.DEFAULTS.get(_k, _arg[0])))
                elif _kind == 'bool':
                    out[_k] = bool(_v)
                elif _kind == 'int':
                    try:
                        out[_k] = max(_arg[0], min(_arg[1], int(_v)))
                    except Exception:
                        out[_k] = int(ux.DEFAULTS.get(_k, _arg[0]))
                elif _kind == 'secret':
                    continue
                else:
                    out[_k] = str(_v or '')
        return out

    def _build_adv_panel():
        nonlocal adv_panel_x
        if adv_panel_x is not None:
            return adv_panel_x
        _perf_mark('cfg_adv_b')
        adv_panel_x = ux.AdvPanel(
            p4, adv_prefs, adv_secrets,
            on_apply=_xh_apply_adv,
            on_clear=lambda: _xh_clear(),
            on_diag=lambda: _xh_export_diag(),
            on_guide=lambda key: _xh_open_guide(),
            on_legal=lambda: w._xh_open_legal())
        if _adv_ph[0] is not None:
            v4.removeWidget(_adv_ph[0])
            _adv_ph[0].hide()
            _adv_ph[0].deleteLater()
            _adv_ph[0] = None
        v4.addWidget(adv_panel_x)
        _perf_mark('cfg_adv_e')
        w._adv_panel = adv_panel_x
        return adv_panel_x

    w._xh_apply_adv = _xh_apply_adv
    w._ensure_adv_panel = _build_adv_panel    # setpoll 自检复用同一条 on_apply 路径
    if _LAZY_CFG:
        _adv_ph[0] = QLabel('进阶配置在首次打开本标签时加载（R253 冷启动惰性构建）')
        _adv_ph[0].setObjectName('sub')
        _adv_ph[0].setAlignment(Qt.AlignCenter)
        v4.addWidget(_adv_ph[0], 1)
        # 启动仍要应用已保存的进阶配置（此前只在点「保存」时生效，重启后设置丢失）
        try:
            _xh_apply_adv(_adv_vals_from_prefs(), silent=True)
        except Exception:
            _err()
    else:
        _build_adv_panel()
        try:
            _xh_apply_adv(adv_panel_x.collect(), silent=True)
        except Exception:
            _err()
    _perf_mark('cfg_apply_e')
    cfg_top.addTab(p4, '进阶配置')

    # R255：说明与协议改为「进阶配置下方一行入口」的独立二级页面。
    # 设置页不再保留五份正文；二级页面首次点开时才构建（LegalView 仍在 ux_core）。
    _legal_pg = [None]
    _legal_view = [None]
    _legal_back = [None]      # (nav_index, cfg_tab, [(scroll_area, value), ...])

    def _build_legal_page():
        if _legal_pg[0] is not None:
            return _legal_pg[0]
        _perf_mark('cfg_docbuild_b')
        pgd = ux.legal_page(on_back=_xh_close_legal)
        _perf_mark('cfg_docbuild_e')
        _legal_pg[0] = pgd
        _legal_view[0] = pgd.findChild(ux.LegalView)
        stack.addWidget(pgd)
        return pgd

    def _xh_open_legal(sel=None):
        """进入二级页面；记住设置页分区与各滚动区位置，返回时原样恢复。"""
        if _legal_pg[0] is None or stack.currentWidget() is not _legal_pg[0]:
            _sp = []
            for _sa in ct.findChildren(QAbstractScrollArea):
                try:
                    _sp.append((_sa, _sa.verticalScrollBar().value()))
                except Exception:
                    _err()
            _legal_back[0] = (stack.currentIndex(), cfg_top.currentIndex(), _sp)
        _build_legal_page()
        stack.setCurrentWidget(_legal_pg[0])
        if sel is not None and _legal_view[0] is not None:
            _legal_view[0].select(sel)

    def _xh_close_legal():
        """返回设置页：停在进入前的分区与滚动位置，不跳回页首。"""
        _nav, _tab, _sp = _legal_back[0] or (stack.indexOf(ct), 3, [])
        if 0 <= _nav < stack.count():
            stack.setCurrentIndex(_nav)
        if 0 <= _tab < cfg_top.count():
            cfg_top.setCurrentIndex(_tab)
        for _sa, _v in _sp:
            try:
                _sa.verticalScrollBar().setValue(_v)
            except Exception:
                _err()

    w._xh_open_legal = _xh_open_legal
    w._xh_close_legal = _xh_close_legal

    def _lazy_cfg_tab(i):
        if i == 3:
            _build_adv_panel()

    _LAZY_HOOK[0] = _lazy_cfg_tab

    def _ensure_lazy_cfg():
        # 自检命令需要真控件时补齐，避免与用户路径分叉。
        if not _LAZY_CFG:
            return
        _build_adv_panel()

    w._ensure_lazy_cfg = _ensure_lazy_cfg
    # R255：探针按「目录第 n 份」补齐：进二级页面后选到该份，滚动只作用于唯一 docBody。
    w._ensure_lazy_doc = _xh_open_legal

    def _xh_open_guide():
        _xh_open_legal(4)                  # 目录第 5 项 = 使用说明

    def _xh_open_adv():
        idx = stack.indexOf(ct)
        if idx >= 0:
            stack.setCurrentIndex(idx)
        cfg_top.setCurrentIndex(3)

    def _xh_export_diag():
        import zipfile
        _d = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'XingHongPanel')
        os.makedirs(_d, exist_ok=True)
        _out = os.path.join(_d, 'diag_%s.zip' % xh_time.fstamp())
        with zipfile.ZipFile(_out, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in os.listdir(_d):
                if f.endswith(('.log', '.json')):
                    try:
                        z.write(os.path.join(_d, f), f)
                    except Exception:
                        _err()
            z.writestr('prefs.json', json.dumps(
                {k2: adv_prefs.get(k2) for k2 in ux.DEFAULTS}, ensure_ascii=False))
        return _out

    def _xh_clear():
        _d = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'XingHongPanel')
        for f in (os.listdir(_d) if os.path.isdir(_d) else []):
            if f.endswith('.log'):
                try:
                    os.remove(os.path.join(_d, f))
                except Exception:
                    _err()

    # R024：入口按钮已移入设置分区，旧的独立弹窗与状态行已删除

    # R009：配置中心不套外层滚动；内部子标签充满，运行日志内部滚动
    stack.addWidget(ct)

    _perf_mark('tab5_e')
    # ---- 数据设置：回调 / CSV 导出 / 四条配置轮询 ----
    # 原 build_ui L8925–9210（排在「小星」页之后，仅书写顺序；此处紧随构建段）----
    def store_changed(i):
        set_rh.setEnabled(i == 1)

    set_store.currentIndexChanged.connect(store_changed)

    def win_changed(_i):
        # C1-B2-1：原 `nonlocal chart_win; chart_win = win_map[i]` 是历史残留——
        # 状态页用的是它自己闭包里的 chart_win（pages_status.py L398），这个 launcher
        # 局部量没有任何消费者（全仓 chart_win 只有状态页内部读 + 本处写）。按
        # 「要么真有消费者、要么删」删掉写入；本下拉保留原有唯一可见效果 back_to_live()。
        # 注意：「曲线窗口选择对状态页无效」是既有功能缺口，不在本批顺手改。
        back_to_live()

    set_win.currentIndexChanged.connect(win_changed)
    # 默认选中 3 分钟（状态页 chart_win 初值同为 180）
    set_win.setCurrentIndex(1)

    def save_panel_set():
        iv_map = {0: 1, 1: 2, 2: 3, 3: 5}
        rh_map = {0: 6, 1: 12, 2: 24, 3: 72}
        permanent = set_store.currentIndex() == 0
        payload = {
            'sample_interval': iv_map[set_iv.currentIndex()],
            'retain_hours': rh_map[set_rh.currentIndex()],
            'permanent': permanent,
        }
        set_save_b.setEnabled(False)

        def work():
            try:
                return api('POST', '/api/settings/save', payload)
            except Exception as e:
                return (0, {'msg': str(e)})

        def done(r):
            set_save_b.setEnabled(True)
            _c, res = r
            if isinstance(res, dict):
                QMessageBox.information(w, '设置', '已保存（采样 %ss · %s）' % (
                    res.get('sample_interval'),
                    '永久存储' if res.get('permanent') else '保留 %sh' % res.get('retain_hours')))

        qwait(work, done)

    set_save_b.clicked.connect(save_panel_set)

    def export_csv(path, suggest):
        _xprefs2 = QSettings('X.H', 'XingHongPanel')
        _edef = str(_xprefs2.value('export_dir', '') or '')
        if _edef and suggest and not os.path.isabs(suggest):
            suggest = os.path.join(_edef, suggest)
        fn, _ = QFileDialog.getSaveFileName(w, '保存 CSV', suggest, 'CSV (*.csv)')
        if fn:
            _xprefs2.setValue('export_dir', os.path.dirname(fn))
        if not fn:
            return

        def work():
            try:
                return get_bytes(path)
            except Exception as e:
                return (0, str(e).encode())

        def done(r):
            _c, data = r
            if _c == 200 and data:
                try:
                    with open(fn, 'wb') as f:
                        f.write(data)
                    QMessageBox.information(w, '导出', '已保存：' + fn)
                except Exception as e:
                    QMessageBox.warning(w, '导出失败', str(e))
            else:
                QMessageBox.warning(w, '导出失败', '下载数据失败（code=%s）' % _c)

        qwait(work, done)

    set_csv1.clicked.connect(lambda: export_csv('/api/export/metrics.csv', 'metrics.csv'))
    set_csv2.clicked.connect(lambda: export_csv('/api/export/jobs.csv', 'jobs.csv'))
    set_csv3.clicked.connect(lambda: export_csv('/api/export/energy.csv', 'energy.csv'))

    def poll_cfg():
        qwait(lambda: api('GET', '/api/state'), _cfg_done, key='state')

    def _cfg_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, st = r
            # R15-X12：用户在编辑数据设置控件时（聚焦中）不回填覆盖
            _fw = w.focusWidget()
            if _fw is not None and _fw in (set_iv, set_rh, set_store):
                return
            settings = st.get('settings', {})
            iv = settings.get('sample_interval', 2)
            iv_map = {1: 0, 2: 1, 3: 2, 5: 3}
            if iv in iv_map:
                set_iv.setCurrentIndex(iv_map[iv])
            rh = settings.get('retain_hours', 24)
            rh_map = {6: 0, 12: 1, 24: 2, 72: 3}
            if rh in rh_map:
                set_rh.setCurrentIndex(rh_map[rh])
            perm = settings.get('permanent', True)
            set_store.setCurrentIndex(0 if perm else 1)
            set_rh.setEnabled(not perm)
        except Exception:
            _err()

    def poll_jobs_tbl():
        # R03：仅「生成记录」子页可见时才拉 50 条任务并重建 300 个表格项
        try:
            if cfg_top.currentIndex() != 1:
                return
        except Exception:
            _err()
        qwait(lambda: api('GET', '/api/jobs?n=50'), _jobs_tbl_done, key='jobs')

    _jobs_sig = [None]

    def _jobs_tbl_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, d = r
            jobs = d.get('jobs', []) if isinstance(d, dict) else []
            # R03：数据未变则跳过整表重建（8 s 一轮 × 300 项 QTableWidgetItem 是 cfg 页主要开销）
            sig = tuple((str(j.get('time') or j.get('wall') or ''), str(j.get('status', '')),
                         str(j.get('file', ''))) for j in jobs if isinstance(j, dict))
            if sig == _jobs_sig[0]:
                return
            _jobs_sig[0] = sig
            # R19：无滚动条页面——只渲染能完整放下的最新 15 条（行高 30，视口 ≥500）
            _shown = list(reversed(jobs))[:15]
            cfg_jobs_tbl.setUpdatesEnabled(False)
            try:
                cfg_jobs_tbl.setRowCount(len(_shown))
                for i, j in enumerate(_shown):
                    t = j.get('time') or j.get('wall') or ''
                    if isinstance(t, (int, float)):
                        t = xh_time.md_hm(t)
                    cfg_jobs_tbl.setItem(i, 0, QTableWidgetItem(str(t)))
                    cfg_jobs_tbl.setItem(i, 1, QTableWidgetItem(str(j.get('kind', ''))))
                    # R28-V21：状态列补 10px 圆点（成功绿/失败红/进行中蓝/其他灰），文字与排序不变
                    _st_it = QTableWidgetItem(str(j.get('status', '')))
                    _st = str(j.get('status', '')).lower()
                    if 'succ' in _st or 'ok' == _st:
                        _st_col = UI_C['OK']
                    elif any(_k in _st for _k in ('fail', 'err', 'cancel')):
                        _st_col = UI_C['ERR']
                    elif any(_k in _st for _k in ('run', 'queue', 'pend', 'wait')):
                        _st_col = UI_C['INFO']
                    else:
                        _st_col = UI_C['IDLE']
                    _st_it.setIcon(QIcon(_state_dot(_st_col, 10)))
                    cfg_jobs_tbl.setItem(i, 2, _st_it)
                    cfg_jobs_tbl.setItem(i, 3, QTableWidgetItem(str(j.get('file', ''))))
                    # R36-F2：键存在但值为 None 时 str(None) 会渲染成「None」。按「脏值
                    # 显示 —」口径统一成 —（后端还没回报耗时=未知，不是 0 s）。
                    _el = j.get('elapsed')
                    if _el is None or _el == '':
                        _el = j.get('dur')
                    if _el is None or _el == '':
                        _el = '—'
                    elif isinstance(_el, (int, float)):
                        _el = '%.1f' % float(_el)
                    else:
                        _el = str(_el)
                    cfg_jobs_tbl.setItem(i, 4, QTableWidgetItem(_el))
                    cfg_jobs_tbl.setItem(i, 5, QTableWidgetItem(str(j.get('params', ''))[:200]))
                if not _shown:
                    cfg_jobs_note.setText('暂无生成记录')
                elif len(jobs) > len(_shown):
                    cfg_jobs_note.setText('共 %d 条 · 本页显示最新 %d 条（更多请用上方「任务 CSV」导出）'
                                          % (len(jobs), len(_shown)))
                else:
                    cfg_jobs_note.setText('共 %d 条 · 已全部显示' % len(jobs))
            finally:
                cfg_jobs_tbl.setUpdatesEnabled(True)
        except Exception:
            _err()

    def poll_health():
        # R03：仅「数据设置」子页可见时才拉健康检查（/api/health 与首页无关）
        try:
            if cfg_top.currentIndex() != 0:
                return
        except Exception:
            _err()
        qwait(lambda: api('GET', '/api/health'), _health_done, key='health')

    def _health_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, h = r
            if isinstance(h, dict):
                score = h.get('score')
                if score is not None:
                    health_score_lbl.setText(str(score))
                    col = UI_C['OK'] if score >= 80 else (UI_C['WARN'] if score >= 60 else UI_C['WARN_2'])
                    if getattr(health_score_lbl, '_xh_col', None) != col:
                        health_score_lbl.setStyleSheet(
                            'font-size:22px; font-weight:700; color:%s;'
                            'background:transparent; border:none;' % col)
                        health_score_lbl._xh_col = col
                checks = h.get('checks', [])
                if isinstance(checks, list):
                    # R15-X07/X15：每项独立 label 池（复用不重建）
                    # R19：改单列满宽 + wordWrap(True) 兜底折行（原 2 列每列 129px，文案会硬切）
                    _want = []
                    for c in checks:
                        if isinstance(c, dict):
                            st = str(c.get('status') or '')
                            if st == 'ok':
                                mark = '✓'
                            elif st in ('warn', 'err'):
                                mark = '✗'
                            else:
                                mark = '·'  # info（未启动/离线）用中性记号
                            _col = (UI_C['OK'] if st == 'ok'
                                    else (UI_C['WARN'] if st == 'warn'
                                          else (UI_C['WARN_2'] if st == 'err' else UI_C['INK_2'])))
                            _want.append((mark, c.get('name', ''), c.get('msg', ''), _col))
                    _cur = getattr(w, '_health_lbls', [])
                    for _i in range(max(len(_cur), len(_want))):
                        if _i >= len(_cur):
                            _lb = QLabel('')
                            _lb.setWordWrap(True)
                            _lb.setStyleSheet(
                                'font-size:12px; background:transparent; border:none;')
                            hc_grid.addWidget(_lb, _i, 0)
                            _cur.append(_lb)
                        _lb = _cur[_i]
                        if _i >= len(_want):
                            _lb.hide()
                            continue
                        _mk, _nm, _msg, _col = _want[_i]
                        _lb.setText('%s %s %s' % (_mk, _nm, _msg))
                        _lb.setStyleSheet(
                            'font-size:12px; color:%s; background:transparent; border:none;'
                            % _col)
                        _lb.show()
                    w._health_lbls = _cur
        except Exception:
            _err()

    def poll_cfg_logs():
        # R03：仅「运行日志」子页可见时才拉两份日志正文
        try:
            if cfg_top.currentIndex() != 2:
                return
        except Exception:
            _err()
        qwait(lambda: api('GET', '/api/log/kv'), _cfg_logs_kv_done, key='logkv')
        qwait(lambda: api('GET', '/api/log/comfy'), _cfg_logs_cf_done, key='logcf')

    def _cfg_logs_apply(r, edit):
        if not isinstance(r, tuple) or r[0] == 'ERR':
            return
        _code, kv = r
        if isinstance(kv, dict):
            lines = kv.get('lines', [])
            # 「日志级别」设置：按级别过滤展示（DEBUG 全量 / INFO 去调试行 / WARNING、ERROR 只看异常）
            _lvl = XH_ADV.get('logLevel', 'INFO')
            if _lvl == 'INFO':
                lines = [x for x in lines if 'DEBUG' not in str(x).upper()]
            elif _lvl == 'WARNING':
                lines = [x for x in lines
                         if ('WARN' in str(x).upper() or 'ERROR' in str(x).upper()
                             or '警告' in str(x) or '错误' in str(x) or '异常' in str(x))]
            elif _lvl == 'ERROR':
                lines = [x for x in lines
                         if ('ERROR' in str(x).upper() or '错误' in str(x) or '异常' in str(x))]
            txt = '\n'.join(str(x) for x in lines)
            # R19：日志里残留的 ANSI 颜色控制序列直接剥离（原先显示成 "☒[33m"）
            txt = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', txt)
            if edit.toPlainText() != txt:
                edit.setPlainText(txt)
                _vsb = edit.verticalScrollBar()
                _vsb.setValue(_vsb.maximum())  # 自动跟到最新一行（滚动条本身隐藏）

    def _cfg_logs_kv_done(r):
        _cfg_logs_apply(r, cfg_kv_log)

    def _cfg_logs_cf_done(r):
        _cfg_logs_apply(r, cfg_cf_log)

    poll_cfg()

    def cfg_sub_changed(_i):
        # R03：子页切换即时刷新（子页级门控后不再等 6–12 s 定时器）；
        # 生成记录重进时清掉签名，强制重建一次。
        _jobs_sig[0] = None
        QTimer.singleShot(60, poll_cfg)
        QTimer.singleShot(60, poll_jobs_tbl)
        QTimer.singleShot(60, poll_health)
        QTimer.singleShot(60, poll_cfg_logs)

    return {
        'adv_prefs': adv_prefs,
        'adv_secrets': adv_secrets,
        'cfg_top': cfg_top,
        'cfg_jobs_tbl': cfg_jobs_tbl,
        '_cfg_done': _cfg_done,
        'poll_cfg': poll_cfg,
        'poll_jobs_tbl': poll_jobs_tbl,
        'poll_health': poll_health,
        'poll_cfg_logs': poll_cfg_logs,
        'cfg_sub_changed': cfg_sub_changed,
    }
