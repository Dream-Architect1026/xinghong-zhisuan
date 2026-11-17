# -*- coding: utf-8 -*-
"""UI 页面：Tab「生图」（C1-B2 第 5 批）。

原位置：xinghong_launcher.py build_ui() 内 7503–8073 行，整段原样搬出（571 行）。
分层：ui 层，只依赖 PySide6 / 标准库；launcher 的控件工厂、私有工具、配置快照全部显式传参。

两处与状态页共用、因此留在 launcher 模块级、由两页共用，不在此复制：
- `_safe_post`：POST 兜底（抛异常统一折成 (0, {"msg": ...})）；
- `_comfy_power`：ComfyUI 启停（飞行期置灰 + 失败弹窗 + 180s 超时），本页以 `comfy_power` 入参接收，
  qwait / w 由 launcher 侧闭包预绑定。

唯一一处非机械改写：`_mbridge`/`tray` 在配置页段才创建，页面侧改 `mbridge()`/`get_tray()` 懒取
——与视频页/超分页同款写法。
"""
import json
import os
import subprocess
import threading
import time
from collections import OrderedDict

from PySide6.QtCore import QBuffer, QByteArray, QSettings, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSpinBox, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from infra.config import XH_COVER, XH_WALL_N, XH_MUTE as _XH_MUTE
import pathcfg as _pathcfg

__all__ = ['build_page_gen']


def build_page_gen(*, stack, w, api, qwait, _err, _perf_mark, _safe_post, comfy_power,
                   _bridge,
                   _attach_delete_menu, _mk_clear_btn, make_grid_gallery,
                   mbridge, get_tray,
                   GlassCard, ChipGroup, PreviewDialog, ShimmerCover, ShowcaseDialog,
                   UI_C, _THUMB_MAX_BYTES,
                   _out_local_size, _style_form_labels, cf_err_text, get_bytes,
                   make_missing_cover, make_oversize_cover, wall_icon):
    """Tab「生图」页面构建（C1-B2 第 5 批，从 build_ui 原样搬出）。

    参数名与 build_ui 内原标识符逐字一致（机械等价可逐行比对）。
    返回 launcher 后续确实引用的句柄：`w._probe3` / `w._invoke_map` / t2 定时器 / 超分页缩略图工具。
    """
    # ============ Tab 3: 生图 ============
    _perf_mark('tab2_b')
    gt = QWidget()
    gv3 = QHBoxLayout(gt)
    gv3.setContentsMargins(4, 8, 4, 4)
    left = QVBoxLayout()
    g_cfg = GlassCard('生成参数')
    g_cfg.set_glyph('spark')
    # R014（领导）：参数卡限宽，历史作品墙占满剩余宽度（两列铺满）
    g_cfg.setMaximumWidth(400)
    lf = QFormLayout(g_cfg)
    lf.setContentsMargins(12, 12, 12, 8)
    cf_lbl = QLabel('—')
    cf_lbl.setMinimumWidth(0)  # R13（P1-3）：状态与按钮成组，状态占剩余宽、按钮定宽
    cf_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    cf_start_b = QPushButton('启动 ComfyUI')
    cf_start_b.setMinimumWidth(102)
    cf_stop_b = QPushButton('停止')
    cf_stop_b.setMinimumWidth(52)
    # R13（P1-3）：原「状态一行 + 按钮右对齐一行」在 1024 视口看着像左右两端失联；
    # 改为同一行：左状态（可压缩）+ 右侧两个定宽按钮，视觉上是一个组。
    cf_wrap = QWidget()
    cfrow = QHBoxLayout(cf_wrap)
    cfrow.setContentsMargins(0, 0, 0, 0)
    cfrow.setSpacing(8)
    cfrow.addWidget(cf_lbl, 1)
    cfrow.addWidget(cf_start_b, 0)
    cfrow.addWidget(cf_stop_b, 0)
    lf.addRow('ComfyUI', cf_wrap)
    prompt = QPlainTextEdit()
    prompt.setPlaceholderText('提示词')
    prompt.setFixedHeight(80)
    lf.addRow('提示词', prompt)
    neg = QPlainTextEdit()
    neg.setPlaceholderText('负面词')
    neg.setFixedHeight(44)
    lf.addRow('负面词', neg)
    model_combo = QComboBox()
    model_combo.addItems(['Qwen-Image 2.1 · INT8 原版（6.76GB）', 'Qwen-Image 2.1 · UC-GGUF（无内容过滤）'])
    lf.addRow('生图模型', model_combo)
    # R13（P0-1）：预设与自定义宽高共用唯一事实来源（spin 值）。
    # 点预设 → 写 spin；手改 spin → 预设自动切到匹配项或「自定义」。
    size_chips = ChipGroup(
        [('land', '横 1216×832'), ('port', '竖 832×1216'),
         ('sq', '方 1024²'), ('custom', '自定义')], default='land', cols=2)
    lf.addRow('尺寸预设', size_chips)
    # 自定义宽/高（X-53，预设只负责填值）；上限对齐后端校验 64–2048，避免填大了才在服务端报错
    gen_w = QSpinBox()
    gen_w.setRange(64, 2048)
    gen_w.setSingleStep(64)
    gen_w.setValue(1216)
    gen_w.setToolTip('64–2048，建议 64 的倍数')
    gen_h = QSpinBox()
    gen_h.setRange(64, 2048)
    gen_h.setSingleStep(64)
    gen_h.setValue(832)
    gen_h.setToolTip('64–2048，建议 64 的倍数')
    custom_row = QHBoxLayout()
    custom_row.addWidget(gen_w)
    custom_row.addWidget(QLabel('×'))
    custom_row.addWidget(gen_h)
    custom_row.addStretch(1)
    lf.addRow('自定义宽/高', custom_row)
    steps = QSpinBox()
    steps.setRange(1, 100)
    steps.setValue(20)
    steps.setMaximumWidth(200)
    cfgv = QDoubleSpinBox()
    cfgv.setRange(0.1, 20)
    cfgv.setValue(1.0)
    cfgv.setMaximumWidth(200)
    seed = QSpinBox()
    seed.setRange(0, 2147483647)
    seed.setMaximumWidth(200)
    lf.addRow('步数', steps)
    lf.addRow('CFG', cfgv)
    lf.addRow('种子(0=随机)', seed)
    left.addWidget(g_cfg)
    gen_lbl = QLabel('')
    gen_lbl.setObjectName('sub')
    gen_btn = QPushButton('生 成')
    gen_btn.setFixedHeight(35)          # R24-V2：38→35，并入全局按钮族
    left.addWidget(gen_lbl)
    left.addWidget(gen_btn)
    # 生成结果 inline（X-55）
    gen_result = QLabel('')
    gen_result.setAlignment(Qt.AlignCenter)
    gen_result.setMinimumHeight(180)
    gen_result.hide()
    left.addWidget(gen_result)
    # 生成中骨架微光（shimmer）
    gen_shimmer = ShimmerCover()
    gen_shimmer.setFixedHeight(180)
    gen_shimmer.setStyleSheet(
        'ShimmerCover { background: rgba(14,30,52,0.6); border-radius:8px; }')
    gen_shimmer.hide()
    left.addWidget(gen_shimmer)
    gv3.addLayout(left, 3)
    right = QVBoxLayout()
    g_out = GlassCard('历史作品')
    g_out.set_glyph('spark')
    outv = QVBoxLayout(g_out)
    outv.setContentsMargins(12, 12, 12, 8)
    outputs = make_grid_gallery(icon_size=130)  # R015：130 保证 308 视口稳定两列（148 临界失败）
    outputs._letterbox = True  # R13（P1-6）：用户作品按原比例留白，不裁构图
    outputs._xname = 'gen'     # R14：归因寻址用（不影响界面）
    out_btn_row = QHBoxLayout()
    # R28：此行宽仅 ~314px，三枚按钮加图标会触发省略号/挤压，故本行不加图标（保持 R24 版式）
    showcase_b = QPushButton('杰作展示')
    open_dir_b = QPushButton('打开输出目录')
    out_btn_row.addWidget(showcase_b)
    out_btn_row.addWidget(open_dir_b)
    out_btn_row.addWidget(_mk_clear_btn(outputs, lambda: poll_outputs(), icon=False))
    outv.addWidget(outputs, 1)
    outv.addLayout(out_btn_row)
    right.addWidget(g_out, 1)  # R015：卡片纵向拉满（原 Fixed 高度下方留大片空白）
    gv3.addLayout(right, 3)
    # R009：生图页不套外层滚动；参数区固定适配，历史作品宫格内部滚动
    _style_form_labels(lf)              # R24-V4：表单标签 12px + 次级色
    stack.addWidget(gt)

    def open_showcase():
        dlg = ShowcaseDialog(w, api, get_bytes)
        dlg.exec()

    showcase_b.clicked.connect(open_showcase)

    # R13（P0-1）：双向联动。唯一事实来源=两个 spin 的值；chip 只是它的投影。
    _sz_guard = {'on': False}

    def _size_key(w, h):
        if (w, h) == (1216, 832):
            return 'land'
        if (w, h) == (832, 1216):
            return 'port'
        if (w, h) == (1024, 1024):
            return 'sq'
        return 'custom'

    def _sync_size_chip(*_a):
        if _sz_guard['on']:
            return
        k = _size_key(gen_w.value(), gen_h.value())
        if size_chips.value() != k:
            size_chips.set_value(k)

    def _apply_size_chip():
        if _sz_guard['on']:
            return
        v = size_chips.value()
        rules = {'land': (1216, 832), 'port': (832, 1216), 'sq': (1024, 1024)}
        if v not in rules:
            return  # 「自定义」只表态、不改值
        _sz_guard['on'] = True
        try:
            gen_w.setValue(rules[v][0])
            gen_h.setValue(rules[v][1])
        finally:
            _sz_guard['on'] = False

    size_chips.toggled.connect(_apply_size_chip)
    gen_w.valueChanged.connect(_sync_size_chip)
    gen_h.valueChanged.connect(_sync_size_chip)
    _sync_size_chip()

    def show_inline_image(url, elapsed=None):
        def _preview_fail():
            # 完成但看不到图时，别让用户对着空白区发呆：明说是预览失败、去哪儿找文件
            try:
                gen_lbl.setText((gen_lbl.text() or '完成') + '（预览加载失败，请点「打开输出目录」查看）')
            except Exception:
                _err()

        def work():
            try:
                _, data = get_bytes(url, timeout=20)
                pm = QPixmap()
                if pm.loadFromData(data):
                    cap = gen_result.width() or 360
                    scaled = pm.scaled(cap, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    # R15-S03：经主线程桥回填
                    _bridge.run.emit(lambda: gen_result.setPixmap(scaled))
                    _bridge.run.emit(lambda: gen_result.show())
                    if elapsed is not None:
                        _bridge.run.emit(lambda: gen_lbl.setText('耗时 %ss' % elapsed))
                else:
                    _bridge.run.emit(_preview_fail)
            except Exception:
                _bridge.run.emit(_preview_fail)
        threading.Thread(target=work, daemon=True).start()

    # R023-M2：生图表单记忆（提示词/负面词/模型/尺寸/步数/CFG；种子不记，保持 0=随机语义）
    _genprefs = QSettings('X.H', 'XingHongPanel')
    try:
        prompt.setPlainText(str(_genprefs.value('genPrompt', '') or ''))
        neg.setPlainText(str(_genprefs.value('genNegative', '') or ''))
        model_combo.setCurrentIndex(max(0, min(model_combo.count() - 1,
            int(_genprefs.value('genModel', 0)))))
        gen_w.setValue(max(64, min(2048, int(_genprefs.value('genW', 1216)))))
        gen_h.setValue(max(64, min(2048, int(_genprefs.value('genH', 832)))))
        steps.setValue(max(1, min(100, int(_genprefs.value('genSteps', 20)))))
        cfgv.setValue(max(0.1, min(20.0, float(_genprefs.value('genCfg', 1.0)))))
    except Exception:
        _err()

    def build_gen_payload():
        """R13（P0-1）：请求体唯一构造点。UI 上显示的宽高就是这里读的两个 spin。"""
        return {
            'variant': 'uc-gguf' if 'UC-GGUF' in model_combo.currentText() else 'int8',
            'prompt': prompt.toPlainText().strip(),
            'negative': neg.toPlainText().strip() or '低质量，模糊，变形，水印，文字',
            'width': gen_w.value(), 'height': gen_h.value(),
            'steps': steps.value(), 'cfg': cfgv.value(),
            'seed': seed.value() or int(time.time()) % 2147483647,
        }

    def _sztest(path):
        """四态自检：点横/竖/方 + 手改。记录 UI 显示值、chip、请求体宽高。"""
        _orig = (gen_w.value(), gen_h.value())
        picks = [('横', 'land'), ('竖', 'port'), ('方', 'sq'), ('手改', None)]
        out = []
        for label, chipval in picks:
            if chipval is None:
                gen_w.setValue(768); gen_h.setValue(768)
            else:
                for v, b in size_chips._buttons:
                    if v == chipval:
                        b.click()
            plc = build_gen_payload()
            _txt = ''
            for v, b in size_chips._buttons:
                if v == size_chips.value():
                    _txt = b.text()
            out.append({'state': label, 'ui_w': gen_w.value(), 'ui_h': gen_h.value(),
                        'chip': size_chips.value(), 'chip_text': _txt,
                        'payload_w': plc['width'], 'payload_h': plc['height'],
                        'match': (gen_w.value() == plc['width']
                                  and gen_h.value() == plc['height'])})
        gen_w.setValue(_orig[0]); gen_h.setValue(_orig[1])
        try:
            with open(path, 'w', encoding='utf-8') as _f:
                json.dump({'states': out, 'win': [w.width(), w.height()]},
                          _f, ensure_ascii=False, indent=1)
        except Exception:
            _err()

    w._sztest_fn = _sztest

    def do_generate():
        # 空提示词不进后端：就地给明确提示，避免"点了没反应"或只回一句英文校验错
        if not prompt.toPlainText().strip():
            gen_lbl.setText('请先填写提示词')
            try:
                w._toast.show('请先填写提示词再生成', 'warn')
                prompt.setFocus()
            except Exception:
                _err()
            return
        try:
            _genprefs.setValue('genPrompt', prompt.toPlainText())
            _genprefs.setValue('genNegative', neg.toPlainText())
            _genprefs.setValue('genModel', model_combo.currentIndex())
            _genprefs.setValue('genW', gen_w.value())
            _genprefs.setValue('genH', gen_h.value())
            _genprefs.setValue('genSteps', steps.value())
            _genprefs.setValue('genCfg', float(cfgv.value()))
        except Exception:
            _err()
        payload = build_gen_payload()
        # R13：审计钩子——把真实请求体落盘（默认关闭，仅 XH_REQLOG 时生效）
        _rqlog = os.environ.get('XH_REQLOG')
        if _rqlog:
            try:
                with open(_rqlog, 'a', encoding='utf-8') as _rf:
                    _rf.write('gen %s\n' % json.dumps(payload, ensure_ascii=False))
            except Exception:
                _err()
        gen_lbl.setText('生成中…（同步请求，可能数十秒）')
        gen_result.hide()
        gen_shimmer.show()
        gen_btn.setEnabled(False)

        def run():
            try:
                _, d = api('POST', '/api/comfy/generate', payload, timeout=660)
                if isinstance(d, dict) and d.get('ok'):
                    # R15-S03：全部 UI 更新经主线程桥
                    _bridge.run.emit(lambda: gen_lbl.setText('完成：' + str(d.get('file', ''))))
                    _bridge.run.emit(lambda: show_inline_image(d.get('url'), d.get('elapsed')))
                    # R015（领导）：任务完成托盘通知（经主线程桥，B3）
                    try:
                        # R36-N3：托盘「静音通知」门（默认关闭，不影响原行为）
                        if not _XH_MUTE['on']:
                            mbridge().run.emit(lambda t=get_tray(), m=str(d.get('file', '')):
                                                t.showMessage('生图完成', m,
                                                              QSystemTrayIcon.Information, 4000))
                    except Exception:
                        _err()
                elif isinstance(d, dict):
                    _bridge.run.emit(
                        lambda dd=d: gen_lbl.setText(cf_err_text(dd, '失败：')))
                else:
                    _bridge.run.emit(lambda: gen_lbl.setText(str(d)))
            except Exception as e:
                _bridge.run.emit(lambda: gen_lbl.setText('失败：' + str(e)))
            finally:
                _bridge.run.emit(lambda: gen_shimmer.hide())
                _bridge.run.emit(lambda: gen_btn.setEnabled(True))
                _bridge.run.emit(lambda: poll_outputs())

        threading.Thread(target=run, daemon=True).start()

    gen_btn.clicked.connect(do_generate)

    out_names = []
    out_items = []  # 完整 output 项（含 section/kind/size/mtime），供预览
    # R022：真正的 LRU（OrderedDict）——原先 dict+pop(next(iter())) 是插入序 FIFO，
    # 反复浏览同一批旧图会被淘汰再重下，同时 120×512² 峰值约 126MB，降到 60×448²。
    _thumb_cache = OrderedDict()   # {name: (mtime, QImage 源图)}——按当前方格重渲染
    _out_mtime = {}     # R15-X13：最近一次输出的 name→mtime 映射
    _THUMB_CACHE_LIMIT = 60
    _THUMB_SRC_MAX = 448
    # R09（XH-064）：失败重试与并发去重——t4 每 10 s 轮询一次，原来列表没变就直接
    # return，一次瞬时失败（超时/大图解码失败）会让格子永久停在「海报缺失」。
    _thumb_inflight = set()   # 正在拉取的 name，防止同一张被重复并发拉
    _thumb_fail = {}          # name -> 失败次数，达上限后不再重试
    _THUMB_RETRY_MAX = 5

    def _thumb_cache_trim(limit=_THUMB_CACHE_LIMIT):
        """R022：LRU 上限；命中时由调用方 move_to_end 刷新热度。"""
        try:
            while len(_thumb_cache) > limit:
                _thumb_cache.popitem(last=False)
        except Exception:
            _err()

    def _decode_thumb(data):
        """R09（XH-064）：超分产物可达 13056×9792（解码约 488 MB），超过 Qt 默认
        128 MB 分配上限，QImage/QPixmap.loadFromData 直接失败 → 「海报缺失」。
        这里放开上限，并把缩放请求交给读取器，避免先解全图再缩。"""
        try:
            from PySide6.QtCore import QBuffer, QByteArray
            from PySide6.QtGui import QImageReader
        except Exception:
            return None
        try:
            ba = QByteArray(data)
            buf = QBuffer(ba)
            buf.open(QBuffer.ReadOnly)
            rd = QImageReader(buf)
            rd.setAutoTransform(True)
            try:
                rd.setAllocationLimit(0)   # 0 = 不限制（Qt 6.5+）
            except Exception:
                _err()
            _sz = rd.size()
            if _sz.isValid() and max(_sz.width(), _sz.height()) > _THUMB_SRC_MAX:
                rd.setScaledSize(_sz.scaled(_THUMB_SRC_MAX, _THUMB_SRC_MAX,
                                            Qt.KeepAspectRatio))
            img = rd.read()
            if img.isNull():
                pm = QPixmap()
                if pm.loadFromData(data):
                    img = pm.toImage()
            return None if img.isNull() else img
        except Exception:
            return None

    def _fetch_thumb(n, it):
        """R09：工作线程内「下载 + 解码」，主线程只套图标（大图解码不再卡 UI）。"""
        if not n or n in _thumb_inflight:
            return
        _thumb_inflight.add(n)

        def _work():
            rr = get_bytes('/output/' + n, timeout=15)
            _st = rr[0] if isinstance(rr, tuple) and len(rr) == 2 else 'ERR'
            img = _decode_thumb(rr[1]) if _st == 200 else None
            return (_st, img)

        def _done(rr):
            _thumb_inflight.discard(n)
            if not thumb_done(rr, it):
                _thumb_fail[n] = _thumb_fail.get(n, 0) + 1

        qwait(_work, _done)

    def poll_outputs():
        qwait(lambda: api('GET', '/api/outputs?limit=48'), _outputs_done, key='outputs')

    def _outputs_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            _code, d = r
            outs = d.get('outputs', []) if isinstance(d, dict) else []
            # R008（N9）：历史作品墙只收图片，视频归视频墙（后端已按 section/kind 分流）
            # R09（XH-065）：生图历史墙剔除超分产物——用户把私人照片用于 2x 放大后，
            # 放大结果会落在 output/upscale，原实现把它当「历史作品」摆在生图页，
            # 看起来就像程序翻到了本机私照。放大结果有自己的「放大结果墙」。
            IMG_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')
            pairs = [(o.get('name'), o) for o in outs
                     if isinstance(o, dict)
                     and str(o.get('name', '')).lower().endswith(IMG_EXT)
                     and o.get('section') != 'upscale'
                     and o.get('kind') != 'video']
            names = [n for n, _o in pairs if n]
            items = [o for _n, o in pairs]
            if names != out_names:
                out_names[:] = names
                out_items[:] = items
                _out_mtime.clear()
                _thumb_fail.clear()   # R09：新列表重置重试计数
                for _o in items:
                    if isinstance(_o, dict) and _o.get('name'):
                        _out_mtime[_o.get('name')] = _o.get('mtime')
                outputs.clear()
                for n in names[:(XH_WALL_N or 24)]:
                    it = QListWidgetItem('')  # 领导要求：历史作品不展示名字
                    it.setData(Qt.UserRole, n)  # R008（N2）：匹配改走 UserRole
                    it.setSizeHint(QSize(*outputs._cell))
                    outputs.addItem(it)
                    outputs._autofit()
                    # R12（XH-082）：超大图直接给「图片过大」占位，不进整图解码。
                    _osz = _out_local_size(n)
                    if _osz >= _THUMB_MAX_BYTES:
                        it.setData(Qt.UserRole + 3, 'oversize')
                        it.setIcon(QIcon(make_oversize_cover(
                            outputs.iconSize().width(),
                            _osz / (1024.0 * 1024.0))))
                        continue
                    # R15-X13/R020：未变文件直接复用缓存源图，按当前方格生成图标
                    _c = _thumb_cache.get(n)
                    if _c is not None and _c[0] == _out_mtime.get(n):
                        _thumb_cache.move_to_end(n)  # R022：命中即刷新 LRU 热度
                        it.setData(Qt.UserRole + 1, _c[1])
                        it.setData(Qt.UserRole + 2, False)
                        it.setData(Qt.UserRole + 3, 'ok')
                        _isz = outputs.iconSize().width()
                        _lb = not XH_COVER   # 与原实现一致：非 XH_COVER 一律 letterbox
                        it.setIcon(QIcon(wall_icon(n, _out_mtime.get(n), _c[1], _isz, _lb)))
                        continue
                    _fetch_thumb(n, it)
            else:
                # R09（XH-064）：列表未变也要补拉失败项（每次轮询最多 6 张，避免再并发打爆）
                _retry = 0
                for _i in range(outputs.count()):
                    if _retry >= 6:
                        break
                    _it = outputs.item(_i)
                    if _it.data(Qt.UserRole + 3) in ('ok', 'oversize'):
                        continue
                    _nm = _it.data(Qt.UserRole)
                    if not _nm or _thumb_fail.get(_nm, 0) >= _THUMB_RETRY_MAX:
                        continue
                    _fetch_thumb(_nm, _it)
                    _retry += 1
        except Exception:
            _err()

    def thumb_done(r, it):
        # R09（XH-064）：接受 (status, QImage) 或 (status, bytes)；返回是否成功，
        # 供重试计数。失败仍保留「海报缺失」占位，但下一轮轮询会再试。
        _ok = False
        try:
            _img = None
            if isinstance(r, tuple) and len(r) == 2 and r[0] == 200:
                _img = r[1] if isinstance(r[1], QImage) else _decode_thumb(r[1])
            if _img is not None and not _img.isNull():
                # R020：保留源图（QImage）供自适应尺寸重渲染，图标按当前方格 cover
                lst = it.listWidget()
                isz = lst.iconSize().width() if lst is not None else 140
                src = _img
                # R022：缓存源图上限 448px（4× 超分原图可达数十 MB）
                if max(src.width(), src.height()) > _THUMB_SRC_MAX:
                    src = src.scaled(_THUMB_SRC_MAX, _THUMB_SRC_MAX,
                                     Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
                it.setData(Qt.UserRole + 1, src)
                it.setData(Qt.UserRole + 2, False)
                it.setData(Qt.UserRole + 3, 'ok')
                _nm = it.data(Qt.UserRole)
                _lb2 = bool(getattr(lst, '_letterbox', False)) and not XH_COVER
                _ic = wall_icon(_nm or '', _out_mtime.get(_nm), src, isz, _lb2)
                it.setIcon(QIcon(_ic))
                if _nm:
                    _thumb_cache[_nm] = (_out_mtime.get(_nm), src)
                    _thumb_cache_trim()
                _ok = True
        except Exception:
            _ok = False
        if not _ok:
            try:
                it.setData(Qt.UserRole + 3, 'missing')
                # X16：海报失败态——灰底虚线框 + 「海报缺失」小字（区别于加载中）
                lst = it.listWidget()
                isz = lst.iconSize().width() if lst is not None else 140
                it.setIcon(QIcon(make_missing_cover(isz)))
            except Exception:
                _err()
        return _ok

    def open_out_item(item):
        # 应用内资源预览（X-70a），双击打开 PreviewDialog
        # R008（N2）：显示名为短名，匹配改走 UserRole 存的原始名
        name = item.data(Qt.UserRole) or item.text()
        idx = None
        for i, o in enumerate(out_items):
            fn = o.get('name') if isinstance(o, dict) else str(o)
            if fn == name or os.path.basename(str(fn)) == os.path.basename(str(name)):
                idx = i
                break
        if idx is None:
            return
        dlg = PreviewDialog(w, out_items, idx, api, get_bytes)
        dlg.exec()

    outputs.itemDoubleClicked.connect(open_out_item)
    _attach_delete_menu(outputs, lambda: poll_outputs())

    def open_outdir():
        out = _pathcfg.comfy_out()
        if not out:
            return
        subprocess.Popen(['explorer', out])

    open_dir_b.clicked.connect(open_outdir)
    # XH-FIX-1.4.3：原两行是 `lambda: do('/api/comfy/start'|'/api/comfy/stop')`，
    # 全文件没有 do() 定义 → 1.4.3 包内点这两个按钮必 NameError（真故障，非重构中间态）。
    # 照状态页 comfy 启停写法，改走 _safe_post + qwait：后台执行、按钮飞行期置灰、
    # 失败弹窗，成功状态由 poll_comfy 刷新（stop 后端要杀进程树，超时放宽到 180s）。
    cf_start_b.clicked.connect(lambda: comfy_power('/api/comfy/start', '启动', cf_start_b))
    cf_stop_b.clicked.connect(lambda: comfy_power('/api/comfy/stop', '停止', cf_stop_b))

    def poll_comfy():
        qwait(lambda: api('GET', '/api/state'), _comfy_done, key='state')

    _comfy_online = {'v': False}  # R015：ComfyUI 在线状态（超分引擎就绪判定共用）

    def _comfy_done(r):
        try:
            if not isinstance(r, tuple) or r[0] == 'ERR':
                return
            # X3：当前页可见性早退——生图页状态标签只在生图页可见时刷新
            if stack.currentIndex() != 2:
                return
            _code, d = r
            cf = d.get('comfy') or {}
            online = bool(cf.get('alive') or cf.get('ready'))
            ready = not online and bool(cf.get('present'))
            _comfy_online['v'] = online  # R015：供超分引擎就绪判定共用
            cf_lbl.setText('<span style="color:%s">●</span> ComfyUI · %s%s'
                           % (UI_C['OK'] if online else UI_C['INK_3'],
                              '在线' if online else '离线',
                              ' · 模型已就位' if ready else ''))
            cf_lbl.setToolTip('服务端口 8189')
        except Exception:
            _err()

    _perf_mark('tab2_e')

    return {
        'cf_start_b': cf_start_b,
        'cf_stop_b': cf_stop_b,
        'gen_btn': gen_btn,
        'gen_lbl': gen_lbl,
        'prompt': prompt,
        'gen_w': gen_w,
        'gen_h': gen_h,
        'steps': steps,
        'cfgv': cfgv,
        'seed': seed,
        'model_combo': model_combo,
        'poll_outputs': poll_outputs,
        'poll_comfy': poll_comfy,
        '_comfy_done': _comfy_done,
        '_comfy_online': _comfy_online,
        'outputs': outputs,
        '_fetch_thumb': _fetch_thumb,
        '_thumb_fail': _thumb_fail,
        '_THUMB_RETRY_MAX': _THUMB_RETRY_MAX,
    }

