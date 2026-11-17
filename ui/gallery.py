# -*- coding: utf-8 -*-
"""UI 组件：网页版宫格资源池 + R10 历史作品删除/清空（C1-B2 第 8a 批）。

原位置：xinghong_launcher.py build_ui() L6853–7162（310 行），整段原样搬出：
  make_grid_gallery（等大方格 / 自适应列数 / 180–460px 反算行高）/
  _wall_names / _do_delete（POST /api/outputs/delete，action 池去重）/
  _ask_delete（单张 Yes/No、批量输数字）/ _attach_delete_menu / _mk_clear_btn /
  _ph_icon（缺图占位）。

非机械差异（1 处，已在 _b2_8a_equal.py 声明）：
  _shorten_name 全仓 0 引用，删除，不搬。

分层：ui 层，只依赖 PySide6 / 标准库 / infra.config；launcher 的常量（FIT_STAT / SCROLL_* /
UI_C）、绘图工厂（_attach_empty_state / overlay_play_badge /
fit_pixmap / cover_pixmap / _cover_canvas / _nav_icon）与 api/qwait 全部显式传参，
禁止 import xinghong_launcher。B3：原中转常量改为直接读 infra.config。

句柄：launcher 解包后仍用原来的 7 个名字（make_grid_gallery / _attach_delete_menu /
_mk_clear_btn / _ph_icon / _ask_delete / _wall_names / _del_state），
管道演练入口 w._r10_del 继续引用同一份 _del_state。
"""
import math

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMenu, QMessageBox, QPushButton,
                               QVBoxLayout)

from infra.config import XH_COVER

__all__ = ['build_gallery_helpers']


def build_gallery_helpers(*, w, api, qwait, ACTION_POOL, _err,
                          FIT_STAT, SCROLL_H, SCROLL_OFF,
                          _attach_empty_state, overlay_play_badge,
                          fit_pixmap, cover_pixmap, _cover_canvas,
                          _nav_icon, UI_C):
    """宫格墙与删除/清空辅助（C1-B2 第 8a 批，从 build_ui 原样搬出）。

    参数名与 build_ui 内原标识符逐字一致（MECHANICAL_EQUAL 逐行比对）。
    """

    def make_grid_gallery(icon_size=150):
        """统一的网页版宫格资源池（XH-2）：等大方格、流畅纵向滑动、干净描边。"""
        lst = QListWidget()
        lst.setViewMode(QListWidget.IconMode)
        lst.setMovement(QListWidget.Static)
        lst.setResizeMode(QListWidget.Adjust)
        # R13（P0-2）：uniform 会按「首条目」缓存尺寸，后补条目/占位图尺寸不同 →
        # 行高不齐、最后一行被切。改为每条目显式 sizeHint，关掉 uniform 缓存。
        lst.setUniformItemSizes(False)
        lst.setWrapping(True)
        lst.setFlow(QListWidget.LeftToRight)
        # R020（领导）：历史作品不展示名字 → 彻底去掉 26px 文字带；
        # 条目高度=正方形缩略图本身，只留 4px 接缝，滚动连续无空白带。
        cell = icon_size
        lst.setIconSize(QSize(icon_size, icon_size))
        lst.setGridSize(QSize(icon_size + 4, icon_size + 4))
        # R008（N2 根因）：uniform 尺寸会拿"首条目"（图标未返回、短名一行）的矮
        # sizeHint 锁死全表 → 图标压扁成细条。条目创建处必须显式 setSizeHint。
        lst._cell = (cell, cell)
        lst.setSpacing(0)
        # R008（N2）：关词折行——无空格长名会把条目 sizeHint 撑爆（行距暴涨/图标压扁）
        lst.setWordWrap(False)
        lst.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        lst.setHorizontalScrollBarPolicy(SCROLL_H)
        # R015（领导）：不显示滚动条（滚轮仍可滚动），对齐网页无缝宫格
        lst.setVerticalScrollBarPolicy(SCROLL_OFF)
        lst.setEditTriggers(QListWidget.NoEditTriggers)
        lst.setSelectionMode(QListWidget.SingleSelection)
        lst.setMinimumHeight(220)
        # R22（缺图 A2）：0 条时给统一空态，有数据自动隐藏；三面墙共用。
        _attach_empty_state(lst)
        # R017/R020（领导）：宫格自适应填满墙宽——两列大图；窄到排不下两列时
        # 单列铺满整宽（不再出现"单列 + 半边空白"）。
        def _apply_fit():
            FIT_STAT['apply'] += 1
            try:
                vw = lst.viewport().width()
                vh = lst.viewport().height()
                if vw < 60 or vh < 60:
                    return
                gap = 4
                # R020：按墙宽选 1–4 列（目标格子约 190px），宽窗不再只排 2 列留白
                cols = max(1, min(4, int(vw / 190.0 + 0.5)))
                # R13（P0-2）：Qt 每个格子都占 (cell+gap)（最右一列也带 gap），
                # 按 cols*(cell+gap) <= vw 反算，否则差 1 个 gap 就会掉成单列、右半留白
                cell = int((vw - gap * cols) / cols)
                cell = max(60, min(cell, 460))
                # R13（P0-2）：按"整数行"反算格边长——rows*(cell+gap) 恰好装进可视高，
                # 最后一排完整、无半行（余量 ≤3px，小于行距）；content 与视口都是
                # grid 的整数倍，滚到哪都不会切半行
                rows = max(1, int(math.ceil(vh / float(cell + gap))))
                if rows > 1:
                    cell = min(cell, int((vh - gap * rows) / rows))
                cell = max(60, cell)
                grid = cell + gap
                new_size = QSize(cell, cell)
                changed = lst.iconSize() != new_size
                # R14：几何与每条 sizeHint 都没变就直接返回——原实现每次 Resize/Show
                # 都无条件 setIconSize/setGridSize + 逐条 setSizeHint，会让 Qt 整表
                # 重新布局；这条早退不产生任何像素差（下面要写的值本来就相同）。
                if (not changed
                        and lst.gridSize() == QSize(cell + gap, cell + gap)
                        and getattr(lst, '_rows', -1) == rows
                        and all(lst.item(_i).sizeHint() == new_size
                                for _i in range(lst.count()))):
                    FIT_STAT['skip'] += 1
                    return
                lst.setIconSize(new_size)
                # 行/列缝统一 4px（旧版纵向只有 1px，导致行高不齐）
                lst.setGridSize(QSize(cell + gap, cell + gap))
                lst._cell = (cell, cell)
                lst._rows = rows
                lst._cols = max(1, int(vw // (cell + gap)))
                for _i in range(lst.count()):
                    lst.item(_i).setSizeHint(QSize(cell, cell))
                if changed:
                    lst._reicon()  # 尺寸变化后按新方格重渲染，消除"旧图标+大格"空隙
            except Exception:
                _err()

        _fit_timer = QTimer(lst)
        _fit_timer.setSingleShot(True)
        _fit_timer.setInterval(120)

        def _autofit():
            _fit_timer.start()

        _fit_timer.timeout.connect(_apply_fit)

        def _reicon():
            """按当前 iconSize 重渲染全部缩略图（源图缓存在 UserRole+1）。"""
            FIT_STAT['reicon'] += 1
            size = lst.iconSize().width()
            for _i in range(lst.count()):
                it = lst.item(_i)
                src = it.data(Qt.UserRole + 1)
                if not isinstance(src, QImage) or src.isNull():
                    continue
                if it.data(Qt.UserRole + 2):
                    it.setIcon(QIcon(overlay_play_badge(src, size)))
                elif getattr(lst, '_letterbox', False) and not XH_COVER:
                    # R13（P1-6）：作品墙按原比例留白，不裁构图
                    it.setIcon(QIcon(fit_pixmap(QPixmap.fromImage(src), size)))
                else:
                    it.setIcon(QIcon(cover_pixmap(QPixmap.fromImage(src), size)))
        lst._reicon = _reicon

        class _FitEvt(QObject):
            def eventFilter(self, obj, ev):
                # R020（领导）：视口/控件尺寸变化、页面首次显示都重新排格，
                # 修复"初始化时按 310px 排成一列、真正铺开后不重排"的空档问题。
                if ev.type() in (QEvent.Resize, QEvent.Show):
                    # R022：隐藏页切回来时首帧常沿用旧格宽（Show 时 viewport 宽度
                    # 尚未更新），补一次 0ms 即时重排，不等 120ms 防抖。
                    if ev.type() == QEvent.Show:
                        QTimer.singleShot(0, _apply_fit)
                    _autofit()
                return False
        _fe = _FitEvt(lst)
        lst.installEventFilter(_fe)
        try:
            lst.viewport().installEventFilter(_fe)
        except Exception:
            _err()
        QTimer.singleShot(0, _apply_fit)
        QTimer.singleShot(280, _apply_fit)  # 等布局稳定后再校正一次
        lst._autofit = _autofit
        return lst

    # ============ R10（XH-067）：历史作品删除 / 清空 ============
    # 用户明确要求：只删「程序自己产出」且落在 output 白名单内的文件；
    # 二次确认后才发请求；删完立刻刷新墙，刷新不会复活。
    _del_state = {'box': None}

    def _wall_names(lst):
        return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                if lst.item(i).data(Qt.UserRole)]

    def _do_delete(names, refresh, label='作品', walls=()):
        names = [n for n in names if isinstance(n, str) and n]
        if not names:
            return

        def _drop(dels):
            # 墙先本地剔除（不等下一拍轮询），保证「删完立刻消失」
            for _lst in walls:
                for _i in range(_lst.count() - 1, -1, -1):
                    _it = _lst.item(_i)
                    if _it is not None and _it.data(Qt.UserRole) in dels:
                        _lst.takeItem(_i)

        def _work():
            return api('POST', '/api/outputs/delete', {'names': names}, timeout=20)

        def _done(r):
            code, d = (r if isinstance(r, tuple) and len(r) == 2 else ('ERR', None))
            if code == 'ERR' or not isinstance(d, dict) or not d.get('ok'):
                _msg = (d or {}).get('error') or (d or {}).get('msg') or '网络错误'
                try:
                    w._toast.show('删除失败：%s' % _msg, 'warn')
                except Exception:
                    _err()
            else:
                try:
                    _del = set(d.get('deleted') or names)
                    _drop(_del)
                except Exception:
                    _err()
                try:
                    _n = int(d.get('n') or 0)
                    _nf = int(d.get('nfail') or 0)
                    w._toast.show('已删除 %d 个%s%s' % (
                        _n, label, ('，跳过 %d 个' % _nf) if _nf else ''), 'ok')
                except Exception:
                    _err()
            # 立即刷新对应墙（后端已删文件，轮询回来即消失，刷新不复活）
            try:
                refresh()
            except Exception:
                _err()

        qwait(_work, _done, key='delout', pool=ACTION_POOL)

    def _ask_delete(names, refresh, label='作品', walls=()):
        names = [n for n in names if isinstance(n, str) and n]
        if not names:
            try:
                w._toast.show('没有可删除的%s' % label)
            except Exception:
                _err()
            return
        if len(names) == 1:
            _txt = ('确认删除该%s？\n\n%s\n\n将从程序输出目录永久删除，无法恢复。'
                    % (label, names[0]))
            box = QMessageBox(w)
            box.setWindowTitle('删除确认')
            box.setIcon(QMessageBox.Warning)
            box.setText(_txt)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            try:
                box.button(QMessageBox.Yes).setText('确认删除')
                box.button(QMessageBox.No).setText('取消')
            except Exception:
                _err()

            def _ret(r):
                try:
                    if r == QMessageBox.Yes:
                        _do_delete(names, refresh, label, walls)
                finally:
                    _del_state['box'] = None

            _del_state['box'] = box
            box.finished.connect(_ret)
            box.open()
            return

        # R11（XH-073）：批量「清空历史」是不可逆损失，二次确认硬性要求输入文件数。
        # 单张删除保持原有 Yes/No 二次确认（误删代价小、频率高，不折腾）。
        n = len(names)
        dlg = QDialog(w)
        dlg.setWindowTitle('清空历史确认')
        dv = QVBoxLayout(dlg)
        _t1 = QLabel('即将从程序输出目录永久删除 %d 个文件，删除后无法恢复。' % n)
        _t1.setWordWrap(True)
        _t2 = QLabel('为防止误删，请输入数字 %d 确认（输入不符则「确认清空」不可点）：' % n)
        _t2.setWordWrap(True)
        ed = QLineEdit()
        ed.setPlaceholderText('输入 %d' % n)
        row = QHBoxLayout()
        canb = QPushButton('取消')
        okb = QPushButton('确认清空')
        okb.setEnabled(False)
        row.addStretch(1)
        row.addWidget(canb)
        row.addWidget(okb)
        dv.addWidget(_t1)
        dv.addWidget(_t2)
        dv.addWidget(ed)
        dv.addLayout(row)
        ed.textChanged.connect(lambda s: okb.setEnabled(s.strip() == str(n)))
        canb.clicked.connect(dlg.reject)
        okb.clicked.connect(dlg.accept)

        def _fin(r):
            try:
                if r == QDialog.Accepted:
                    _do_delete(names, refresh, label, walls)
            finally:
                _del_state.update(dlg=None, edit=None, okb=None)

        _del_state.update(dlg=dlg, edit=ed, okb=okb)
        dlg.finished.connect(_fin)
        dlg.resize(440, 190)
        dlg.open()
        try:
            ed.setFocus()
        except Exception:
            _err()

    def _attach_delete_menu(lst, refresh, label='作品'):
        lst.setContextMenuPolicy(Qt.CustomContextMenu)

        def _menu(pos):
            it = lst.itemAt(pos)
            if it is None:
                return
            _nm = it.data(Qt.UserRole)
            menu = QMenu(lst)
            act1 = menu.addAction('删除该%s' % label)
            act2 = menu.addAction('清空历史（%d）' % lst.count())
            _pick = menu.exec(lst.viewport().mapToGlobal(pos))
            if _pick == act1 and _nm:
                _ask_delete([_nm], refresh, label, (lst,))
            elif _pick == act2:
                _ask_delete(_wall_names(lst), refresh, label, (lst,))

        lst.customContextMenuRequested.connect(_menu)

    def _mk_clear_btn(lst, refresh, text='清空历史', icon=True):
        b = QPushButton(text)
        if icon:
            b.setIcon(_nav_icon('trash', 14, UI_C['INK_1']))
            b.setIconSize(QSize(14, 14))
        b.setToolTip('删除本墙全部作品（仅限程序输出目录内、程序自己产出的文件）\n'
                     '批量清空需在确认框里输入文件数量，防误删')
        b.clicked.connect(lambda: _ask_delete(_wall_names(lst), refresh, '作品', (lst,)))
        return b

    def _ph_icon(size=140):
        """R007（XH-22）：首帧/海报缺失占位——深色渐变 + 居中小播放角标。
        R23-A4：底改走 `_cover_canvas`（R22 统一占位色板：海军蓝玻璃 + 青色描边 +
        12px 圆角），不再自绘一套灰蓝底，三画法（缺失/超大/无预览）与视频墙同族。"""
        pm, p, r = _cover_canvas(size)
        c = size * 0.5
        p.setBrush(QColor(210, 228, 250, 185))
        p.setPen(Qt.NoPen)
        p.drawPolygon([QPointF(c - 6, c - 13), QPointF(c - 6, c + 13),
                       QPointF(c + 15, c)])
        p.end()
        return QIcon(pm)

    # C1-B2-8a：原 `_shorten_name` 全仓 0 引用（仅定义、无调用）——与 B2-6 的
    # chart_win 同类处理：删，不把「写了没人读」的债带进 ui 层。

    return {'make_grid_gallery': make_grid_gallery,
            'wall_names': _wall_names,
            'do_delete': _do_delete,
            'ask_delete': _ask_delete,
            'attach_delete_menu': _attach_delete_menu,
            'mk_clear_btn': _mk_clear_btn,
            'ph_icon': _ph_icon,
            'state': _del_state}
