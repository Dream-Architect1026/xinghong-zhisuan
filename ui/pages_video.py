# -*- coding: utf-8 -*-
"""UI 页面：Tab「视频生成」（C1-B2 分层重构第 1 批）。

原位置：xinghong_launcher.py build_ui() 内 9308–9573 行，整段原样搬出。
分层：ui（与 xinghong_launcher.py 同层），只依赖 PySide6 / 标准库，
launcher 的控件工厂与私有工具全部由调用方显式传参，禁止 import launcher。
"""
import os
import threading
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget)

__all__ = ['build_page_video']

def build_page_video(*, stack, w, api, qwait, err, bridge, mbridge, get_tray, mute,
                     perf_mark, nav_icon, t, UI_C, GlassCard, ChipGroup,
                     make_grid_gallery, mk_clear_btn, style_form_labels,
                     video_loader_cls, ph_icon, make_no_preview_cover,
                     overlay_play_badge, cf_err_text, poll_outputs, comfy_out_dir,
                     preview_dialog, get_bytes, attach_delete_menu):
    """Tab「视频生成」页面构建（C1-B2 第 1 批，从 build_ui 原样搬出）。

    依赖全部显式传入，不 import xinghong_launcher（避免循环依赖）。
    返回值只暴露 launcher 后续确实引用的句柄：
        vwall / poll_vwall / video_loader / vgen_btn / vgen_lbl / vprompt / v_first
    """
    perf_mark('tab3_b')
    vt = QWidget()
    vform = QHBoxLayout(vt)
    vform.setContentsMargins(4, 8, 4, 4)
    vg = GlassCard('ComfyUI 视频生成（H3 路线）')
    vg.set_glyph('video')
    # R014（领导）：参数卡限宽，视频历史墙占满剩余宽度（两列铺满）
    vg.setMaximumWidth(400)
    vf = QFormLayout(vg)
    vf.setContentsMargins(12, 12, 12, 8)
    vprompt = QPlainTextEdit()
    vprompt.setPlaceholderText('视频提示词')
    vprompt.setFixedHeight(80)
    vf.addRow('提示词', vprompt)
    v_first = QLineEdit()
    v_first.setPlaceholderText('留空=文生视频；填路径=图生视频')
    pick_first = QPushButton('选择首帧图')
    # R30：选图按钮补 14px 文件夹线稿（该行独占表单宽度，无相邻控件可挤）
    pick_first.setIcon(nav_icon('folder', 14, UI_C['INK_1']))
    pick_first.setIconSize(QSize(14, 14))

    def pick_frame():
        fn, _ = QFileDialog.getOpenFileName(w, '选择首帧图', '', 'Images (*.png *.jpg *.jpeg)')
        if fn:
            v_first.setText(fn)

    pick_first.clicked.connect(pick_frame)
    vf.addRow('首帧(可选)', v_first)
    vf.addRow('', pick_first)
    # 分辨率预设：chip 单选（XH-10），选中后同步到宽高
    v_w = QSpinBox()
    v_w.setRange(256, 2048)
    v_w.setValue(768)
    v_h = QSpinBox()
    v_h.setRange(256, 2048)
    v_h.setValue(768)
    v_res_chips = ChipGroup(
        [(512, '512×512'), (768, '768×768'), (1024, '1024×1024'), (1280, '1280×768')],
        multi=False, default=768, cols=2)
    vf.addRow('分辨率', v_res_chips)
    v_wh = QHBoxLayout()
    v_wh.addWidget(v_w)
    v_wh.addWidget(v_h)
    vf.addRow('自定义宽×高', v_wh)

    def _v_res_chosen(v):
        if v == 1280:
            v_w.setValue(1280); v_h.setValue(768)
        else:
            v_w.setValue(v); v_h.setValue(v)
    v_res_chips.toggled.connect(lambda: _v_res_chosen(v_res_chips.value()))
    # 时长：chip 单选
    v_sec = QDoubleSpinBox()
    v_sec.setRange(1, 20)
    v_sec.setValue(5)
    v_dur_chips = ChipGroup(
        [(3, '3s'), (5, '5s'), (8, '8s'), (10, '10s')],
        multi=False, default=5)
    v_dur_presets = {3, 5, 8, 10}
    dur_row_w = QWidget()
    dur_row = QHBoxLayout(dur_row_w)
    dur_row.setContentsMargins(0, 0, 0, 0)
    dur_row.setSpacing(8)
    for _v2, _b2 in v_dur_chips._buttons:
        _b2.setMinimumWidth(52)  # R015-B1：chips 不再被挤成小圆块
    dur_row.addWidget(v_dur_chips, 1)
    vf.addRow('时长', dur_row_w)
    # R015-B1：细调拆独立行（原与 chips 同行互相挤压裁字）
    _fine_row = QHBoxLayout()
    _fine_lbl = QLabel('细调（秒）')
    _fine_lbl.setStyleSheet(t('color:@INK_2@; font-size:12px;'))
    _fine_row.addWidget(_fine_lbl, 1)
    v_sec.setFixedWidth(86)
    _fine_row.addWidget(v_sec, 0, Qt.AlignVCenter)
    vf.addRow('', _fine_row)

    def _v_dur_chip():
        v_sec.setValue(v_dur_chips.value())
    v_dur_chips.toggled.connect(_v_dur_chip)

    def _v_dur_spin(_v):
        # 手动改秒数：匹配预设则高亮对应 chip，否则清空（XH-1 双向联动）
        iv = int(round(v_sec.value()))
        if iv in v_dur_presets:
            v_dur_chips.set_value(iv)
        else:
            v_dur_chips.clear()
    v_sec.valueChanged.connect(_v_dur_spin)
    v_seed = QSpinBox()
    v_seed.setRange(0, 2147483647)
    vf.addRow('种子(0=随机)', v_seed)
    vgen_lbl = QLabel('')
    vgen_lbl.setObjectName('sub')
    vgen_btn = QPushButton('生成视频')
    vgen_btn.setFixedHeight(35)         # R24-V2：38→35，并入全局按钮族
    vgen_btn.setIcon(nav_icon('video', 14, UI_C['INK_0']))
    vgen_btn.setIconSize(QSize(14, 14))
    vf.addRow(vgen_lbl)
    vf.addRow(vgen_btn)
    vf.addRow(QWidget())  # 弹性填充，让参数充满页面
    vf.setVerticalSpacing(8)
    vform.addWidget(vg, 1)

    # 右侧：仅视频历史墙（X5：去掉生成结果区，新产物直接进入历史墙顶部）
    vright = QVBoxLayout()
    g_vwall = GlassCard('视频历史墙')
    g_vwall.set_glyph('video')
    vwallv = QVBoxLayout(g_vwall)
    vwallv.setContentsMargins(12, 12, 12, 8)
    vwall = make_grid_gallery(icon_size=130)  # R015：130 保证窄墙稳定两列
    vwallv.addWidget(vwall, 1)
    vwallv.addWidget(mk_clear_btn(vwall, lambda: poll_vwall()))
    vright.addWidget(g_vwall, 1)
    vform.addLayout(vright, 1)
    # R009：视频页不套外层滚动；参数区固定，视频历史墙内部滚动
    style_form_labels(vf)              # R24-V4：表单标签 12px + 次级色
    stack.addWidget(vt)

    # R020：首帧按 2× 墙格（260px）提取，自适应放大后仍清晰（不再 130 放大糊）
    video_loader = video_loader_cls(w, size=260)
    video_icon = ph_icon()

    def do_vgen():
        # 空提示词 / 首帧路径不存在：就地拦截，给出可执行的提示，不发请求
        if not vprompt.toPlainText().strip():
            vgen_lbl.setText('请先填写视频提示词')
            try:
                w._toast.show('请先填写视频提示词再生成', 'warn')
                vprompt.setFocus()
            except Exception:
                err()
            return
        _ff = v_first.text().strip()
        if _ff and not _ff.lower().startswith('http') and not os.path.isfile(_ff):
            vgen_lbl.setText('首帧文件不存在：%s' % _ff)
            try:
                w._toast.show('首帧文件不存在，请重新选择', 'warn')
            except Exception:
                err()
            return
        payload = {
            'variant': 'h3',
            'prompt': vprompt.toPlainText().strip(),
            'width': v_w.value(), 'height': v_h.value(),
            'seconds': v_sec.value(),
            'seed': v_seed.value() or int(time.time()) % 2147483647,
        }
        if _ff:
            payload['first_frame'] = _ff
        vgen_lbl.setText('视频生成中…（可能数分钟）')
        vgen_btn.setEnabled(False)

        def run():
            nonlocal _vwall_highlight_name
            try:
                _, d = api('POST', '/api/comfy/generate', payload, timeout=900)
                if isinstance(d, dict) and d.get('ok'):
                    # R15-S03：全部 UI 更新经主线程桥
                    bridge.run.emit(lambda: vgen_lbl.setText('完成：%s（耗时 %ss，已加入历史墙）'
                                                              % (d.get('file', ''),
                                                                 d.get('elapsed'))))
                    # R015（领导）：任务完成托盘通知（经主线程桥，B3）
                    try:
                        # R36-N3：托盘「静音通知」门（默认关闭，不影响原行为）
                        if not mute['on']:
                            mbridge().run.emit(lambda t=get_tray(), m=str(d.get('file') or ''):
                                              t.showMessage('视频生成完成', m,
                                                            QSystemTrayIcon.Information, 4000))
                    except Exception:
                        err()
                    # X5：新产物进入历史墙顶部，标记名字供高亮
                    _vwall_highlight_name = str(d.get('file') or '')
                elif isinstance(d, dict):
                    bridge.run.emit(
                        lambda dd=d: vgen_lbl.setText(cf_err_text(dd, '失败：')))
                else:
                    bridge.run.emit(lambda: vgen_lbl.setText(str(d)))
            except Exception as e:
                bridge.run.emit(lambda: vgen_lbl.setText('失败：' + str(e)))
            finally:
                bridge.run.emit(lambda: vgen_btn.setEnabled(True))
                bridge.run.emit(lambda: poll_outputs())
                bridge.run.emit(lambda: poll_vwall())

        threading.Thread(target=run, daemon=True).start()

    vgen_btn.clicked.connect(do_vgen)

    vwall_items = []  # 视频墙完整项（含 mp4），供预览（历史作品墙 out_items 只含图片）
    _vwall_highlight_name = ''  # 新生成产物名，构建墙后高亮

    def poll_vwall():
        qwait(lambda: api('GET', '/api/outputs?limit=60'), _vwall_done, key='vwall')

    def _vwall_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, d = r
            outs = d.get('outputs', []) if isinstance(d, dict) else []
            vids = [o for o in outs
                    if isinstance(o, dict) and (
                        o.get('kind') == 'video'
                        or str(o.get('name', '')).lower().endswith(('.mp4', '.webm', '.mkv')))]
            vwall_items[:] = vids
            current = [o.get('name') for o in vids]
            existing = [vwall.item(i).data(Qt.UserRole) for i in range(vwall.count())]
            if current == existing:
                return
            vwall.clear()
            for o in vids[:24]:
                name = o.get('name')
                # 先用渐变+角标占位（R007 XH-22），首帧提取完成后替换为真实海报
                it = QListWidgetItem(video_icon, '')  # 不展示名字
                it.setData(Qt.UserRole, name)
                it.setSizeHint(QSize(*vwall._cell))
                vwall.addItem(it)
                vwall._autofit()
                url = os.path.join(comfy_out_dir, name.replace('/', os.sep))

                def apply_poster(key, img, target_it=it):
                    if target_it.data(Qt.UserRole) != key:
                        return
                    # R11（XH-074）：视频格的封面由本回调负责，必须打上「已处理」标记，
                    # 否则墙重建时会被图片缩略图重试逻辑当成解码失败 → 盖成「海报缺失」。
                    target_it.setData(Qt.UserRole + 3, 'ok')
                    if img is not None and not img.isNull():
                        # R020：源帧入库，尺寸变化时按新方格重渲染
                        target_it.setData(Qt.UserRole + 1, img)
                        target_it.setData(Qt.UserRole + 2, True)
                        target_it.setIcon(QIcon(overlay_play_badge(img, vwall.iconSize().width())))
                    else:
                        # X16：ffmpeg+QVideoSink 均失败 → 「首字+渐变」无预览封面
                        _ph = make_no_preview_cover(key, vwall.iconSize().width())
                        target_it.setData(Qt.UserRole + 1, _ph.toImage())
                        target_it.setData(Qt.UserRole + 2, True)
                        target_it.setIcon(
                            QIcon(overlay_play_badge(_ph.toImage(), vwall.iconSize().width())))

                video_loader.enqueue(name, url, apply_poster)
            # X5：高亮新生成产物（选中 + 滚动到顶部）
            nonlocal _vwall_highlight_name
            if _vwall_highlight_name:
                for _i in range(vwall.count()):
                    if vwall.item(_i).data(Qt.UserRole) == _vwall_highlight_name:
                        vwall.setCurrentRow(_i)
                        vwall.scrollToItem(vwall.item(_i), QListWidget.PositionAtTop)
                        break
                _vwall_highlight_name = ''
        except Exception:
            err()

    def open_vwall_item(item):
        name = item.data(Qt.UserRole)
        idx = next((i for i, o in enumerate(vwall_items) if o.get('name') == name), None)
        if idx is not None:
            dlg = preview_dialog(w, vwall_items, idx, api, get_bytes)
            dlg.exec()
        else:
            w._toast.show('该视频尚未加载完成，请稍候再试', 'warning')  # R15-X17：statusBar 改 toast

    vwall.itemDoubleClicked.connect(open_vwall_item)
    attach_delete_menu(vwall, lambda: poll_vwall())

    perf_mark('tab3_e')

    return {
        'vwall': vwall,
        'poll_vwall': poll_vwall,
        'video_loader': video_loader,
        'vgen_btn': vgen_btn,
        'vgen_lbl': vgen_lbl,
        'vprompt': vprompt,
        'v_first': v_first,
    }
