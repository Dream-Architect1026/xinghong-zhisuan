# -*- coding: utf-8 -*-
"""UI 页面：Tab「超分放大」（C1-B2 分层重构第 2 批）。

原位置：xinghong_launcher.py build_ui() 内 9333–9804 行，整段原样搬出。
分层：ui（与 xinghong_launcher.py 同层），只依赖 PySide6 / 标准库；
launcher 的控件工厂与私有工具全部由调用方显式传参，禁止 import launcher。
与视频页共享 video_loader（首帧提取器），由调用方传入同一实例。
"""
import os
import subprocess
import threading

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import (QApplication, QComboBox, QFormLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget)

import pathcfg as _pathcfg

__all__ = ['build_page_upscale']

def build_page_upscale(*, stack, w, api, qwait, err, bridge, mbridge, get_tray, mute,
                        perf_mark, nav_icon, t, UI_C, GlassCard, ChipGroup,
                        make_grid_gallery, mk_clear_btn, style_form_labels, ph_icon,
                        make_no_preview_cover, overlay_play_badge, cf_err_text,
                        preview_dialog, get_bytes, attach_delete_menu, comfy_out_dir,
                        state_dot, out_local_size, thumb_max_bytes, make_oversize_cover,
                        fetch_thumb, thumb_fail, thumb_retry_max, comfy_online, R10,
                        video_loader):
    """Tab「超分放大」页面构建（C1-B2 第 2 批，从 build_ui 原样搬出）。

    依赖全部显式传入；video_loader 是视频页产出的共享首帧提取器（同一实例，不新建）。
    返回值只暴露 launcher 后续确实引用的句柄（_probe3 / 刷新注册表 / _r10_del）。
    """
    perf_mark('tab4_b')
    ut = QWidget()
    uform = QHBoxLayout(ut)
    uform.setContentsMargins(4, 8, 4, 4)
    g_up = GlassCard('超分放大引擎')
    g_up.set_glyph('expand')
    uf = QFormLayout(g_up)
    uf.setContentsMargins(12, 12, 12, 8)
    up_kind = QComboBox()
    up_kind.addItems(['图片放大', '视频放大'])
    up_img = QComboBox()
    up_vid = QComboBox()
    up_scale = QComboBox()
    up_scale.addItems(['2x', '3x', '4x', '6x', '8x', '10x'])
    up_scale_chips = ChipGroup(
        [(2, '2x'), (3, '3x'), (4, '4x'), (6, '6x'), (8, '8x'), (10, '10x')],
        multi=False, default=4, cols=3)
    up_engine = QComboBox()
    up_state_lbl = QLabel('待加载')
    up_state_lbl.setObjectName('sub')
    # R28-V22：状态文字前补 10px 状态点（就绪绿/离线灰/缺模型琥珀/失败红），只表达状态
    up_state_dot = QLabel()
    up_state_dot.setFixedSize(10, 10)
    up_state_dot.setPixmap(state_dot(UI_C['IDLE'], 10))
    up_state_dot.setStyleSheet('background:transparent; border:none;')
    _up_state_row = QWidget()
    _usr = QHBoxLayout(_up_state_row)
    _usr.setContentsMargins(0, 0, 0, 0)
    _usr.setSpacing(8)
    _usr.addWidget(up_state_dot)
    _usr.addWidget(up_state_lbl)
    _usr.addStretch(1)

    def _set_up_state(txt, col):
        up_state_lbl.setText(txt)
        up_state_dot.setPixmap(state_dot(col, 10))
    up_btn = QPushButton('开始放大')
    up_btn.setFixedHeight(35)           # R24-V2：36→35，并入全局按钮族
    up_btn.setIcon(nav_icon('expand', 14, UI_C['INK_0']))
    up_btn.setIconSize(QSize(14, 14))
    up_lbl = QLabel('')
    up_lbl.setObjectName('sub')
    up_lbl.setWordWrap(True)
    up_refresh = QPushButton('刷新输入列表')
    up_refresh.setIcon(nav_icon('refresh', 14, UI_C['INK_1']))
    up_refresh.setIconSize(QSize(14, 14))
    uf.addRow('类型', up_kind)
    uf.addRow('输入图片', up_img)
    uf.addRow('输入视频', up_vid)
    uf.addRow('放大引擎', up_engine)
    uf.addRow('引擎状态', _up_state_row)
    uf.addRow('倍率', up_scale_chips)

    def _up_scale_chosen(v):
        ix = up_scale.findText('%dx' % v)
        if ix >= 0 and ix != up_scale.currentIndex():
            up_scale.setCurrentIndex(ix)
    up_scale_chips.toggled.connect(lambda: _up_scale_chosen(up_scale_chips.value()))
    # R09（XH-063）：倍率双向同步。可见控件是 chips，请求参数读的是隐藏 combo，
    # 原来只有 chips→combo 单向；且初值本身不一致（combo 默认 2x / chips 默认 4x），
    # 于是程序化 setCurrentText('2x') 后请求是 2x、界面仍高亮 4x（R08 实拍即此现象）。
    def _up_scale_from_combo(ix):
        txt = up_scale.itemText(ix) if ix >= 0 else ''
        if txt:
            up_scale_chips.set_value(int(txt.rstrip('x')))
    up_scale.setCurrentIndex(max(0, up_scale.findText('%dx' % (up_scale_chips.value() or 4))))
    up_scale.currentIndexChanged.connect(_up_scale_from_combo)

    # R007（XH-29）：类型联动——图片放大隐藏「输入视频」行，反之亦然
    def _up_kind_sync():
        is_vid = up_kind.currentIndex() == 1
        try:
            uf.setRowVisible(up_img, not is_vid)
            uf.setRowVisible(up_vid, is_vid)
        except Exception:
            err()
    up_kind.currentIndexChanged.connect(_up_kind_sync)
    _up_kind_sync()

    # X22：说明紧跟参数自然排列，移除中间弹性填充（消除参数区中部大留白）
    up_help = QLabel('')
    up_help.setObjectName('sub')
    up_help.setWordWrap(True)
    uf.addRow(up_help)
    hb = QHBoxLayout()
    hb.addWidget(up_btn, 1)
    hb.addWidget(up_refresh, 1)
    uf.addRow(hb)
    uf.addRow(up_lbl)
    uf.setVerticalSpacing(8)
    # R014（领导）：参数栏收窄并贴内容高（原样拉伸导致中部/下方大片空白），
    # 结果墙占满剩余宽度成为页面主体。
    # R015（领导）：参数卡限宽并纵向拉满到底（原 AlignTop 半截悬空留大空白）
    # R020（领导）：中栏参数卡再收窄（330→292），把宽度让给结果墙；chips 网格
    # 换行且按钮最小宽按文字实测，倍率文字不再被截断。
    g_up.setMaximumWidth(292)
    g_up.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    uform.addWidget(g_up, 0)

    # 右侧：inline 结果 + 放大结果墙（X-76/77）
    uright = QVBoxLayout()
    # X5：去掉放大结果区，新产物直接进入放大结果墙
    g_uwall = GlassCard('放大结果墙')
    g_uwall.set_glyph('expand')
    uwallv = QVBoxLayout(g_uwall)
    uwallv.setContentsMargins(12, 12, 12, 8)
    # R11（XH-073）：来源透明。用户把私照拿来超分后，墙里出现放大的私照容易误解成
    # 「程序翻了我的相册」——这里固定显示文件来源目录，并给一个直接打开该目录的按钮。
    u_src = QHBoxLayout()
    _u_hint = QLabel('来源：本机输出目录 output\\upscale（本程序自己产出的文件，不扫描相册）')
    _u_hint.setWordWrap(True)
    _u_hint.setStyleSheet(t('color:@INK_2@; font-size:12px;'))
    _u_open = QPushButton('打开该目录')
    _u_open.setIcon(nav_icon('folder', 14, UI_C['INK_1']))
    _u_open.setIconSize(QSize(14, 14))
    _u_open.setToolTip('在资源管理器中打开 output\\upscale')
    _u_open.setFixedHeight(35)          # R24-V2：24→35，不再做 24px 微型按钮
    u_src.addWidget(_u_hint, 1)
    u_src.addWidget(_u_open, 0)
    uwallv.addLayout(u_src)

    def _open_upscale_dir():
        out = _pathcfg.comfy_out('upscale')
        if not out:
            err()
            return
        try:
            subprocess.Popen(['explorer', out])
        except Exception:
            err()

    _u_open.clicked.connect(_open_upscale_dir)
    uwall = make_grid_gallery(icon_size=160)  # R014：墙宽两列铺满（178格×2 ≤ 视口）
    uwall._letterbox = True  # R13（P1-6）：用户放大结果也按原比例留白
    uwallv.addWidget(uwall, 1)
    uwallv.addWidget(mk_clear_btn(uwall, lambda: poll_uwall()))
    uright.addWidget(g_uwall, 1)
    uform.addLayout(uright, 1)
    # R009：超分页不套外层滚动；参数区固定，放大结果墙内部滚动
    style_form_labels(uf)              # R24-V4：表单标签 12px + 次级色
    stack.addWidget(ut)

    # 根据引擎选择锁定倍率（esrgan 固定 4x，对齐网页 upScaleLock）
    # R01（25 轮）：ESRGAN 原生只有 4×，但不应把倍率锁死并谎报"固定4x"——
    # 后端已支持 4× 后按所选倍率回缩，故倍率始终可选。
    up_engine.currentIndexChanged.connect(lambda _i: up_scale_chips.setEnabled(True))

    def poll_up():
        # R015：顺带刷新 ComfyUI 在线态（原只在生图页更新，超分页会误报离线）
        def _up_state(r):
            try:
                if isinstance(r, tuple) and r[0] != 'ERR' and isinstance(r[1], dict):
                    cf = r[1].get('comfy') or {}
                    comfy_online['v'] = bool(cf.get('alive') or cf.get('ready'))
            except Exception:
                err()
        # R10（pollslow）：超分页的 /api/state 与跨页 t2 重复，合并后只留 t2
        if 'pollslow' not in R10 and 'pollslow2' not in R10:
            qwait(lambda: api('GET', '/api/state'), _up_state, key='state')
        qwait(lambda: api('GET', '/api/comfy/upscale/list'), _up_done, key='upslist')

    def _up_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, d = r
            curimg = up_img.currentText()
            up_img.clear()
            up_img.addItems([str(i) for i in d.get('images', [])])
            if curimg:
                i = up_img.findText(curimg)
                if i >= 0:
                    up_img.setCurrentIndex(i)
            # B-01 修正：输入视频来自 list.videos（input 目录），非输出目录
            curv = up_vid.currentText()
            up_vid.clear()
            up_vid.addItems([str(v) for v in d.get('videos', [])])
            if curv:
                i = up_vid.findText(curv)
                if i >= 0:
                    up_vid.setCurrentIndex(i)
            # 引擎与模型合并为一个下拉（对齐网页 upEngines）
            cureng = up_engine.currentData()
            up_engine.clear()
            if d.get('vosr2'):
                up_engine.addItem('VOSR2 · 推荐', 'vosr2')
                up_engine.setItemData(up_engine.count() - 1, 'VOSR2（扩散修复·推荐）', Qt.ToolTipRole)
            if d.get('seedvr2'):
                up_engine.addItem('SeedVR2 · 单步', 'seedvr2')
                up_engine.setItemData(up_engine.count() - 1, 'SeedVR2（单步扩散修复）', Qt.ToolTipRole)
            for f in d.get('models', []):
                up_engine.addItem('ESRGAN: %s (4×模型 · 按所选倍率输出)' % f, 'esrgan:' + str(f))
            if cureng:
                i = up_engine.findData(cureng)
                if i >= 0:
                    up_engine.setCurrentIndex(i)
            ready = comfy_online['v'] and bool(d.get('seedvr2') or d.get('vosr2') or d.get('models'))
            # R015（领导）：修"假就绪"——引擎就绪必须 ComfyUI 在线且模型存在
            if ready:
                _set_up_state('放大引擎已就绪', UI_C['OK'])
                up_state_lbl.setToolTip('ComfyUI 在线，且检测到可用放大模型')
            elif not comfy_online['v']:
                # 表单右列较窄，长句会被省略号吃掉；短句放正文，完整做法放 tooltip
                _set_up_state('ComfyUI 离线：先启动再刷新', UI_C['IDLE'])
                up_state_lbl.setToolTip('请先在「状态」页点「启动 ComfyUI」，等状态变为在线后，回本页点「刷新输入列表」')
            elif not up_engine.count():
                _set_up_state('未找到放大模型：先放入模型', UI_C['WARN'])
                up_state_lbl.setToolTip('请把 VOSR2 / SeedVR2 / Real-ESRGAN 模型放到 ComfyUI 的 models 目录，然后点「刷新输入列表」')
            else:
                _set_up_state('放大引擎缺失', UI_C['WARN'])
                up_state_lbl.setToolTip('已检测到模型但引擎未就绪，请检查 ComfyUI 旁路节点是否安装完整')
            up_scale_chips.setEnabled(True)
            update_up_help()
        except Exception:
            _set_up_state('列表加载失败', UI_C['ERR'])

    # X8：引擎说明随当前选择动态变化
    def update_up_help():
        eng = up_engine.currentData() or ''
        is_vid = up_kind.currentIndex() == 1
        if eng.startswith('esrgan'):
            txt = ('Real-ESRGAN 图像放大：模型原生 4×，输出按所选倍率缩放（2×/3× 为 4× 结果高质量回缩）。\n'
                   '完成后结果进入右侧「放大结果墙」，双击可预览。')
        elif eng == 'vosr2':
            txt = ('VOSR2 视频扩散修复：逐帧修复并放大，画质最佳但耗时较长；\n'
                   '处理期间会释放显存，完成后进入结果墙。')
        elif eng == 'seedvr2':
            txt = ('SeedVR2 单步扩散修复：速度较快的视频/图像放大；\n'
                   '完成后结果进入右侧「放大结果墙」。')
        else:
            txt = ('请先选择放大引擎；图片放大走 Real-ESRGAN，视频放大逐帧处理。\n'
                   '完成后结果进入右侧「放大结果墙」，双击可预览。')
        if is_vid:
            txt += '\n当前为视频放大，请确保已选择输入视频。'
        up_help.setText(txt)
    up_engine.currentIndexChanged.connect(lambda _i: update_up_help())
    up_kind.currentIndexChanged.connect(lambda _i: update_up_help())

    # R023：扩散类放大在 32MP 输入上会直接 OOM（R022 测试员 P0：分配 11.77GiB >
    # 7.93GiB 上限，13m30s 后失败且界面无原因）。开始前按显存预算自动限幅：
    # 输出像素 ≤ 16MP（最长边 ≤ 4096），必要时生成等比缩小的输入副本。
    _COMFY_INPUT_DIR = _pathcfg.comfy_input()
    _MAX_OUT_MP = 16.0

    def _fit_input_image(name, scale, diffusion):
        """返回 (可用文件名, 提示文本)。失败时原样返回并提示。"""
        try:
            src = os.path.join(_COMFY_INPUT_DIR, name)
            if not os.path.isfile(src) or not diffusion:
                return name, ''
            img = QImage(src)
            if img.isNull():
                return name, ''
            w, h = img.width(), img.height()
            out_mp = (w * scale) * (h * scale) / 1e6
            if out_mp <= _MAX_OUT_MP:
                return name, ''
            max_side = int((_MAX_OUT_MP * 1e6 / float(scale * scale)) ** 0.5)
            if w >= h:
                tw, th = max_side, max(1, int(h * max_side / w))
            else:
                th, tw = max_side, max(1, int(w * max_side / h))
            nimg = img.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            stem, _ext = os.path.splitext(name)
            out_name = '%s__fit%d.png' % (stem, max(tw, th))
            nimg.save(os.path.join(_COMFY_INPUT_DIR, out_name), 'PNG')
            note = ('输入 %d×%d（%.1fMP）超出显存预算，已自动缩至 %d×%d，'
                    '预计输出 %d×%d（%.1fMP）。' %
                    (w, h, w * h / 1e6, tw, th, tw * scale, th * scale,
                     out_mp * (tw / w) * (th / h)))
            return out_name, note
        except Exception:
            return name, ''

    def do_upscale():
        eng = up_engine.currentData() or ''
        if not eng:
            up_lbl.setText('无可用引擎')
            return
        is_vid = up_kind.currentIndex() == 1
        scale = int(up_scale.currentText().rstrip('x'))
        payload = {
            'kind': 'video' if is_vid else 'image',
            'scale': scale,
            'prefix': 'panel',
        }
        if eng.startswith('esrgan:'):
            # R01（25 轮）：不再强制 scale=4；后端按 4× 原生超分后回缩到所选倍率
            payload['engine'] = 'esrgan'
            payload['model'] = eng[len('esrgan:'):]
        else:
            # B-03 修正：vosr2/seedvr2 只发 engine，不发 model
            payload['engine'] = eng
        if is_vid:
            v = up_vid.currentText()
            if not v:
                up_lbl.setText('请先选择输入视频')
                return
            payload['video'] = v
        else:
            im = up_img.currentText()
            if not im:
                up_lbl.setText('请先选择输入图片')
                return
            _diffusion = not eng.startswith('esrgan:')
            _im2, _note = _fit_input_image(im, int(payload.get('scale') or scale), _diffusion)
            payload['image'] = _im2
            if _note:
                up_lbl.setText(_note + ' 正在放大…')
                QApplication.processEvents()
        if not up_lbl.text().startswith('输入'):
            # R08：实测 2x Real-ESRGAN 单张 267.2 s（旧文案「约 10-90 秒」明显偏低），
            # 改为不承诺具体区间的实话口径。
            up_lbl.setText('正在释放显存并放大，视显存与分辨率可能需要数十秒到数分钟…')
        up_btn.setEnabled(False)

        def run():
            nonlocal _uwall_highlight_name
            try:
                _, d = api('POST', '/api/comfy/upscale', payload, timeout=1200)
                if isinstance(d, dict) and d.get('ok'):
                    # R15-S03：全部 UI 更新经主线程桥
                    bridge.run.emit(lambda: up_lbl.setText('完成：%s（耗时 %ss，已加入结果墙）'
                                                            % (d.get('file', ''),
                                                               d.get('elapsed'))))
                    # R015（领导）：任务完成托盘通知（经主线程桥，B3）
                    try:
                        # R36-N3：托盘「静音通知」门（默认关闭，不影响原行为）
                        if not mute['on']:
                            mbridge().run.emit(lambda t=get_tray(), m=str(d.get('file') or ''):
                                              t.showMessage('超分完成', m,
                                                            QSystemTrayIcon.Information, 4000))
                    except Exception:
                        err()
                    _uwall_highlight_name = str(d.get('file') or '')
                elif isinstance(d, dict):
                    _m = cf_err_text(d)
                    # R023：显存/超时类失败给出可操作原因（R022 测试员 P0：只显示灰字）
                    if 'memory' in _m.lower() or 'outofmemory' in _m.lower():
                        _m = '显存不足：已尝试自动限幅；请把倍率降到 2x 或换 Real-ESRGAN。原始信息：' + _m
                    bridge.run.emit(lambda t=_m: up_lbl.setText('失败：' + t))
                else:
                    bridge.run.emit(lambda: up_lbl.setText(str(d)))
            except Exception as e:
                _m = str(e)
                if 'timed out' in _m.lower() or 'timeout' in _m.lower():
                    _m = ('请求超时（>20 分钟）：输入过大或显存不足；已自动限幅仍失败时请降倍率。'
                          ' 原始信息：' + _m)
                bridge.run.emit(lambda t=_m: up_lbl.setText('失败：' + t))
            finally:
                bridge.run.emit(lambda: up_btn.setEnabled(True))
                bridge.run.emit(lambda: poll_uwall())

        threading.Thread(target=run, daemon=True).start()

    up_btn.clicked.connect(do_upscale)
    up_refresh.clicked.connect(poll_up)

    uwall_items = []  # 超分墙完整项（含 section=upscale），供预览
    _uwall_highlight_name = ''  # 新产物名，构建墙后高亮

    def poll_uwall():
        qwait(lambda: api('GET', '/api/outputs?limit=80'), _uwall_done, key='uwall')

    def _uwall_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, d = r
            outs = d.get('outputs', []) if isinstance(d, dict) else []
            ups = [o for o in outs if isinstance(o, dict) and o.get('section') == 'upscale']
            uwall_items[:] = ups
            current = [o.get('name') for o in ups]
            existing = [uwall.item(i).data(Qt.UserRole) for i in range(uwall.count())]
            if current == existing:
                # R09（XH-064）：超分墙同样补拉失败项（大图解码失败后不再永久缺失）
                for _i in range(uwall.count()):
                    _it = uwall.item(_i)
                    _nm = _it.data(Qt.UserRole)
                    if (_it.data(Qt.UserRole + 3) in ('ok', 'oversize') or not _nm
                            or thumb_fail.get(_nm, 0) >= thumb_retry_max):
                        continue
                    # R11（XH-074）：视频格不能走图片解码重试，否则必然失败并盖成
                    # 「海报缺失」；它们的首帧由 video_loader 负责。
                    if str(_nm).lower().endswith(('.mp4', '.webm', '.mkv')):
                        continue
                    fetch_thumb(_nm, _it)
                return
            uwall.clear()
            u_video_icon = ph_icon()
            for o in ups[:24]:
                name = o.get('name')
                # R11（XH-074）：超分墙的视频不只 mp4——webm/mkv 之前被当成图片交给
                # 解码器，解码必然失败，格子停在「海报缺失」。三种扩展名统一走首帧。
                is_vid = str(name).lower().endswith(('.mp4', '.webm', '.mkv'))
                if is_vid:
                    it = QListWidgetItem(u_video_icon, '')  # 不展示名字
                else:
                    it = QListWidgetItem('')  # 不展示名字
                it.setData(Qt.UserRole, name)
                it.setSizeHint(QSize(*uwall._cell))
                uwall.addItem(it)
                uwall._autofit()
                if is_vid:
                    url = os.path.join(comfy_out_dir, name.replace('/', os.sep))

                    def apply_poster(key, img, target_it=it):
                        if target_it.data(Qt.UserRole) != key:
                            return
                        # R11（XH-074）：同视频墙——视频格标记已处理，防止重建时被
                        # 图片缩略图重试覆盖成「海报缺失」。
                        target_it.setData(Qt.UserRole + 3, 'ok')
                        if img is not None and not img.isNull():
                            # R020：源帧入库，尺寸变化时按新方格重渲染
                            target_it.setData(Qt.UserRole + 1, img)
                            target_it.setData(Qt.UserRole + 2, True)
                            target_it.setIcon(QIcon(overlay_play_badge(img, uwall.iconSize().width())))
                        else:
                            # X16：双兜底失败 → 「首字+渐变」无预览封面
                            _ph = make_no_preview_cover(key, uwall.iconSize().width())
                            target_it.setData(Qt.UserRole + 1, _ph.toImage())
                            target_it.setData(Qt.UserRole + 2, True)
                            target_it.setIcon(
                                QIcon(overlay_play_badge(_ph.toImage(), uwall.iconSize().width())))

                    video_loader.enqueue(name, url, apply_poster)
                else:
                    # R09（XH-064）：与生图墙共用「下载+解码在工作线程」的取图路径
                    # R12（XH-082）：超大图直接占位，避免 102MB 私照一直停在加载中。
                    _osz = out_local_size(name)
                    if _osz >= thumb_max_bytes:
                        it.setData(Qt.UserRole + 3, 'oversize')
                        it.setIcon(QIcon(make_oversize_cover(
                            uwall.iconSize().width(),
                            _osz / (1024.0 * 1024.0))))
                    else:
                        fetch_thumb(name, it)
            # X5：高亮新生成产物
            nonlocal _uwall_highlight_name
            if _uwall_highlight_name:
                for _i in range(uwall.count()):
                    if uwall.item(_i).data(Qt.UserRole) == _uwall_highlight_name:
                        uwall.setCurrentRow(_i)
                        uwall.scrollToItem(uwall.item(_i), QListWidget.PositionAtTop)
                        break
                _uwall_highlight_name = ''
        except Exception:
            err()

    def open_uwall_item(item):
        name = item.data(Qt.UserRole)
        idx = next((i for i, o in enumerate(uwall_items) if o.get('name') == name), None)
        if idx is not None:
            dlg = preview_dialog(w, uwall_items, idx, api, get_bytes)
            dlg.exec()
        else:
            w._toast.show('该超分作品尚未加载完成，请稍候再试', 'warning')  # R15-X17：statusBar 改 toast

    uwall.itemDoubleClicked.connect(open_uwall_item)
    attach_delete_menu(uwall, lambda: poll_uwall())

    perf_mark('tab4_e')

    return {
        'uwall': uwall,
        'poll_uwall': poll_uwall,
        'up_btn': up_btn,
        'up_lbl': up_lbl,
        'up_img': up_img,
        'up_vid': up_vid,
        'up_kind': up_kind,
        'up_engine': up_engine,
        'up_scale': up_scale,
        'up_scale_chips': up_scale_chips,
        'up_state_lbl': up_state_lbl,
        'up_refresh': up_refresh,
        'poll_up': poll_up,
    }
