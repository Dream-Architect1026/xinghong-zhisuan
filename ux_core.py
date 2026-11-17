"""星虹智算 UX 内核（星虹自有：设计令牌、文档渲染、偏好/密钥存储、同意对话框）。

包含：设计令牌、进阶配置中心（含问号帮助数据表）、隐私说明 / 用户协议 / 使用说明文本、
偏好存储与密钥受保护存储、同意确认对话框。

约束：只用 PySide6 + 标准库；不得在日志/源码中出现用户 API Key 明文。
"""
import base64
import ctypes
import ctypes.wintypes
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QIntValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

# C1：模块导出清单（tools/check_imports.py 校验；跨模块只用这里列出的公开名）
__all__ = [
    'PRODUCT_NAME', 'APP_VERSION', 'CONSENT_VERSION', 'SPEC',
    'TOKENS', 'DEFAULTS', 'LEGAL_VIEW_ITEMS', 'DOC_BROWSER_QSS',
    'Prefs', 'SecretStore', 'LegalView', 'ConsentDialog', 'AdvPanel', 'prefs',
    'legal_page', 'page_head', 'ensure_consent', 'doc_html',
    'assert_legal_consistent',
]

# ---------------------------------------------------------------- 设计令牌
# 星虹自有：唯一副本在本文件内，不再抽成跨应用共享模块。
TOKENS = {
    'bg': '#1c1c1c',
    'surface': 'rgba(255,255,255,0.045)',
    'surface_hi': 'rgba(255,255,255,0.085)',
    'border': 'rgba(255,255,255,0.13)',
    'text': '#e8f4ff',
    'text_sub': '#9fb4d0',
    'accent': '#03a9f4',
    'accent2': '#7c4dff',
    'accent3': '#2fb6ff',
    'ok': '#34d399',
    'warn': '#fbbf24',
    'err': '#ef6a5d',
    'surface_soft': 'rgba(255,255,255,0.05)',
    'border_soft': 'rgba(255,255,255,0.10)',
    'text_muted': '#9fb4c8',
    'code_bg': 'rgba(0,0,0,0.25)',
    'font_card': '14px', 'font_h': '18px',
    'port1': '#22d3ee', 'port2': '#a78bfa', 'port3': '#34d399', 'port4': '#fbbf24',
    'font_xl': '84px', 'font_l': '26px', 'font_m': '15px',
    'font_body': '13px', 'font_s': '12px', 'font_xs': '11px',
    'space1': 4, 'space2': 8, 'space3': 12, 'space4': 16, 'space5': 24,
    'radius1': 8, 'radius2': 12, 'radius3': 16,
    'dur_fast': 150, 'dur_base': 220, 'dur_slow': 300,
}


# ---------------------------------------------------------------- 文档渲染
def _md_inline(s):
    """行内 Markdown：**加粗**、`代码`；先转义再替换，避免注入。"""
    import html as _html
    e = _html.escape(str(s))
    e = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', e)
    e = re.sub(r'`([^`]+)`',
               r'<span style="font-family:Consolas,monospace;color:#cfe8ff;">\1</span>', e)
    return e


def doc_html(text):
    """把说明/协议正文渲染成有标题层级的 HTML（供 QTextBrowser 使用）。

    支持 legal/*.md 的 Markdown 子集：#/##/### 标题、**加粗**、`代码`、--- 分隔线、
    | 表格 |；同时保留使用说明里的《…》首行、「一、」小标题、「·」与「1.」列表项、
    以空白开头的续行。颜色取自设计令牌，切换主题后随之变化。
    """
    _h1 = TOKENS.get('heading') or TOKENS['text']
    _h2c = TOKENS.get('accent3') or TOKENS['accent']
    lines = str(text or '').splitlines()
    out = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip()
        t = raw.strip()
        if not t:
            i += 1
            continue
        if t == '---':
            out.append('<div style="border-top:1px solid rgba(255,255,255,0.12);'
                       'margin:12px 0;"></div>')
            i += 1
            continue
        if t.startswith('|'):
            # 连续 | 行按表格渲染；第二行是 |---|---| 分隔线时丢弃。
            rows = []
            while i < n and lines[i].strip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            if len(rows) >= 2 and all(set(c) <= set('-: ') for c in rows[1]):
                head, body = rows[0], rows[2:]
            else:
                head, body = None, rows
            cell = []
            if head:
                cell.append('<tr>' + ''.join(
                    '<th style="text-align:left;padding:5px 8px;color:%s;'
                    'border-bottom:1px solid rgba(255,255,255,0.18);">%s</th>'
                    % (_h2c, _md_inline(c)) for c in head) + '</tr>')
            for r in body:
                cell.append('<tr>' + ''.join(
                    '<td style="padding:4px 8px;color:%s;'
                    'border-bottom:1px solid rgba(255,255,255,0.07);">%s</td>'
                    % (TOKENS['text_sub'], _md_inline(c)) for c in r) + '</tr>')
            out.append('<table style="border-collapse:collapse;width:100%%;'
                       'margin:6px 0 10px 0;">%s</table>' % ''.join(cell))
            continue
        _e = _md_inline(t)
        _h2 = (len(t) > 1 and t[1] == '、'
               and t[0] in '一二三四五六七八九十')
        _num = t.split('.', 1)[0].isdigit() and '.' in t
        if t.startswith('### '):
            out.append('<div style="font-size:13px;font-weight:700;color:%s;'
                       'margin:10px 0 4px 0;">%s</div>' % (_h2c, _md_inline(t[4:])))
        elif t.startswith('## '):
            out.append('<div style="font-size:14px;font-weight:700;color:%s;'
                       'margin:16px 0 6px 0;">%s</div>' % (_h2c, _md_inline(t[3:])))
        elif t.startswith('# '):
            out.append('<div style="font-size:18px;font-weight:700;color:%s;'
                       'margin:0 0 12px 0;">%s</div>' % (_h1, _md_inline(t[2:])))
        elif i == 0 and t.startswith('《'):
            out.append('<div style="font-size:18px;font-weight:700;'
                       'color:%s;margin:0 0 12px 0;">%s</div>' % (_h1, _e))
        elif _h2:
            out.append('<div style="font-size:14px;font-weight:700;'
                       'color:%s;margin:16px 0 6px 0;">%s</div>' % (_h2c, _e))
        elif t.startswith('·'):
            out.append('<div style="margin:2px 0 6px 12px;color:%s;">%s</div>'
                       % (TOKENS['text'], _e))
        elif _num:
            out.append('<div style="margin:2px 0 6px 4px;color:%s;">%s</div>'
                       % (TOKENS['text'], _e))
        elif raw[:1].isspace():
            out.append('<div style="margin:0 0 6px 20px;color:%s;">%s</div>'
                       % (TOKENS['text_sub'], _e))
        else:
            out.append('<div style="margin:0 0 6px 0;color:%s;">%s</div>'
                       % (TOKENS['text'], _e))
        i += 1
    return ('<div style="font-size:13px;">' + ''.join(out) + '</div>')


# ---------------------------------------------------------------- 页内头
def page_head(text=None, meta=None, status=None, badge=None, back=None):
    """48 px 页内头，一个函数两种内容模型（不许拆成两个函数）。

    文档模型：text = 18 px/700 标题，meta = 11 px 右侧「协议版本 … · 生效日期 …」
    状态模型：status = 11 px 左侧状态短句，badge = 右侧徽章控件
    back = 可选的返回回调：给了就在标题左侧放「← 返回」，用于二级页面。
    返回 (header, refs)：refs 暴露 title/meta/status/badge，供调用方就地刷新。
    """
    hdr = QWidget()
    hdr.setFixedHeight(48)
    hl = QHBoxLayout(hdr)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(8)
    if back is not None:
        _b = QPushButton('←  返回')
        _b.setObjectName('ghost')
        _b.setCursor(Qt.PointingHandCursor)
        _b.setFixedHeight(30)
        _b.setStyleSheet('font-size:12px; padding:0 10px;')
        _b.clicked.connect(lambda _=False: back())
        hl.addWidget(_b)
    _t = QLabel(text or '')
    _t.setStyleSheet('font-size:18px; font-weight:700;')
    _t.setVisible(bool(text))
    _s = QLabel(status or '')
    _s.setObjectName('sub')
    _s.setStyleSheet('font-size:11px;')
    _s.setVisible(bool(status))
    _m = QLabel(meta or '')
    _m.setObjectName('sub')
    _m.setStyleSheet('font-size:11px;')
    _m.setVisible(bool(meta))
    hl.addWidget(_t)
    hl.addWidget(_s)
    hl.addStretch(1)
    hl.addWidget(_m)
    if badge is not None:
        hl.addWidget(badge)
    return hdr, {'title': _t, 'meta': _m, 'status': _s, 'badge': badge}


APP_VERSION = '1.5.2'
# 产品名单源（R251 改名）：窗口标题、页脚、exe 元数据、
# 安装包、product_name.txt、便携 zip 名全部从这个常量派生，禁止在别处再写死产品名。
# R252：窗口标题不再带副标题，历史名一律归并成 PRODUCT_NAME。
PRODUCT_NAME = '星虹智算'
CONSENT_VERSION = 'v3-20261112'
# 协议版本单一来源：CONSENT_VERSION 形如 v<序号>-<YYYYMMDD>，生效日期由它派生。
# 正文里禁止再手写第二份版本号/日期；改协议必须同时改 CONSENT_VERSION（会触发重新征得同意）。
_DOC_DATE = CONSENT_VERSION.split('-')[-1]
DOC_EFFECTIVE = '%s-%s-%s' % (_DOC_DATE[:4], _DOC_DATE[4:6], _DOC_DATE[6:])
# 统一命名：DATE 常量以 DOCS_UPDATED 为正式名，DOC_EFFECTIVE 保留为别名。
DOCS_UPDATED = DOC_EFFECTIVE


def _ver_rank(v):
    """把 v<序号>-<YYYYMMDD> 解析成可比较的 (序号, 日期)；非法/空值返回 None。

    只用于「已同意版本 vs 当前版本」的大小比较，不参与展示。
    """
    try:
        head, date = str(v).strip().split('-', 1)
        if not head.startswith('v') or len(date) != 8 or not date.isdigit():
            return None
        return (int(head[1:]), int(date))
    except Exception:
        return None


DIALOG_QSS = """
QDialog { background: %(bg)s; color: %(text)s; }
QLabel { color: %(text)s; font-size: %(font_body)s; }
QLabel#hint { color: %(text_sub)s; }
QTabWidget::pane { border: 1px solid %(border)s; border-radius: 10px; }
QTabBar::tab { background: rgba(255,255,255,0.05); color: %(text_sub)s;
  padding: 6px 12px; border-radius: 8px; margin-right: 4px; font-size: 12px; }
QTabBar::tab:selected { background: rgba(3,169,244,0.22); color: #fff; }
QPushButton { background: rgba(255,255,255,0.06); border: 1px solid %(border)s;
  border-radius: 8px; padding: 6px 14px; min-height: 28px; color: %(text)s; }
QPushButton:hover { background: rgba(3,169,244,0.18); }
QPushButton:disabled { color: #64748b; }
QComboBox, QLineEdit, QSpinBox { background: rgba(255,255,255,0.07);
  border: 1px solid %(border)s; border-radius: 8px; padding: 5px 8px;
  min-height: 26px; color: %(text)s; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color: %(accent)s; }
QCheckBox { spacing: 8px; color: %(text)s; }
QScrollArea { background: transparent; border: none; }
QTextBrowser { background: rgba(255,255,255,0.04); border: 1px solid %(border)s;
  border-radius: 10px; padding: 10px; color: %(text)s; }
"""


def _qss():
    return DIALOG_QSS % TOKENS


# ---------------------------------------------------------------- 帮助数据表
HELP = {
    # 显示与主题
    'theme': ('主题配色', '切换整体配色：原版深色为默认；暗夜护眼整体降亮；高对比把文字提亮、背景压暗。',
              '夜间长时间看画面选暗夜护眼；环境光偏强或文字发灰时选高对比。'),
    'animLevel': ('动画与特效强度', '标准=完整过渡与呼吸动效；降级=去掉辉光与流光；关闭=只保留必要淡入。',
                  '机器吃力或觉得画面晃眼时降一级；关闭后数据刷新速度不变。'),
    'reduceMotion': ('减少动效', '开启后页面切换与卡片入场缩短到 120 ms，循环动画停止。',
                     '晕动敏感、或需要长时间专注看图时开启。'),
    # 运行
    'perfMode': ('性能模式', '省电=过渡动画压到 120 ms 并停止循环动效；均衡=标准动效。',
                 '笔记本离电或长期挂机选省电；日常选均衡。'),
    # 启动
    'autoStart': ('开机自启', '登录 Windows 后自动启动星虹智算，写入当前用户启动项。',
                  '需要开机即用本地服务时开启；偶尔才用则关闭。'),
    # 运行与曲线
    'pollInterval': ('端口轮询频率（秒）', 'UI 向本地服务拉取指标的最小间隔；越小越实时，越大越省 CPU。',
                     '想更跟手就调小；长期挂机、机器发烫时调大。'),
    'chartPoints': ('图表点数', '曲线缓冲保留的点数，超出后丢弃最旧的点；点数越多能回看的时间越长。',
                    '只看近期走势保持默认；要回看长时段再调大。'),
    'smooth': ('曲线平滑', '平滑=相邻点插值细分，折线更顺；原始=按采样点直连，形状更忠实。',
               '读数精度优先选「原始」，观感优先选「平滑」。'),
    # 通知与日志
    'notifyEnabled': ('系统通知', '控制右上角提示条是否出现；关闭后仍会写入运行日志。',
                      '专注时不希望被打断就关闭。'),
    'dnd': ('免打扰', '只静音普通提示，错误与警告照常提示。',
            '开会或演示时开启。'),
    'logLevel': ('日志级别', 'INFO 去掉调试行；WARNING/ERROR 只看异常。',
                 '排查问题用 DEBUG；日常用 INFO。'),
}

DEFAULTS = {
    'theme': '原版深色', 'animLevel': '标准', 'reduceMotion': False,
    'perfMode': '均衡', 'autoStart': False,
    'pollInterval': 2, 'chartPoints': 1200, 'smooth': '平滑',
    'notifyEnabled': True, 'dnd': False, 'logLevel': 'INFO',
}

SPEC = [
    ('显示与主题', [
        ('theme', '主题配色', 'choice', ['原版深色', '暗夜护眼', '高对比']),
        ('animLevel', '动画与特效强度', 'choice', ['标准', '降级', '关闭']),
        ('reduceMotion', '减少动效', 'bool', None),
    ]),
    ('运行', [
        ('perfMode', '性能模式', 'choice', ['省电', '均衡']),
    ]),
    ('运行与曲线', [
        ('pollInterval', '端口轮询频率（秒）', 'int', (1, 10)),
        ('chartPoints', '图表点数', 'int', (100, 2000)),
        ('smooth', '曲线平滑', 'choice', ['平滑', '原始']),
    ]),
    ('通知与日志', [
        ('notifyEnabled', '系统通知', 'bool', None),
        ('dnd', '免打扰', 'bool', None),
        ('logLevel', '日志级别', 'choice', ['INFO', 'DEBUG', 'WARNING', 'ERROR']),
    ]),
    ('启动', [
        ('autoStart', '开机自启', 'bool', None),
    ]),
]

# 每项的效果时机（改完立刻给用户明确反馈；不允许"存了不用"）
EFFECT = {
    'theme': '即时', 'animLevel': '即时', 'reduceMotion': '即时',
    'perfMode': '即时', 'autoStart': '写入系统启动项',
    'pollInterval': '即时', 'chartPoints': '即时', 'smooth': '即时',
    'notifyEnabled': '即时', 'dnd': '即时', 'logLevel': '即时（日志页刷新后）',
}


# ---------------------------------------------------------------- 偏好读写
_PREFS = None


def prefs(org='X.H', app='XingHongPanel'):
    """进程内唯一 Prefs 实例（C1-B3 唯一入口）。

    配置页（adv_prefs） / main() 的 R023 只读实例 / diag_cmds 的 consent、theme 分支
    全部走这里，保证「密钥写进 A、读从 B」这类跨实例问题不再可能出现。
    只读同一个 QSettings 文件，构造多个实例本也等价；收成一个是为了让身份可被
    运行时证据（id 探针）直接证明。
    """
    global _PREFS
    if _PREFS is None:
        _PREFS = Prefs(org, app)
    return _PREFS


class Prefs:
    """统一 QSettings 读写（组 adv/ 与 legal/）。"""

    def __init__(self, org, app):
        self.s = QSettings(org, app)

    def get(self, key, default=None):
        d = DEFAULTS.get(key, default)
        try:
            v = self.s.value('adv/' + key, d)
        except Exception:
            return d
        if isinstance(d, bool):
            return bool(v) if not isinstance(v, str) else v.lower() in ('1', 'true', 'yes')
        if isinstance(d, int):
            try:
                return int(v)
            except Exception:
                return d
        return v

    def set(self, key, value):
        self.s.setValue('adv/' + key, value)

    def reset(self):
        for k in list(DEFAULTS):
            try:
                self.s.remove('adv/' + k)
            except Exception:
                pass

    def consent_ok(self):
        got = str(self.s.value('legal/accepted', ''))
        if got == CONSENT_VERSION:
            return True
        # 同一份 legal/accepted 可能被「本机另一份构建」写过：旧构建只认自己的版本号，
        # 看到更高版本会当成未同意重弹，并把值改回自己的版本 → 两份构建互相覆盖，
        # 表现为「同意过还每次启动都弹」。因此已同意「同版本或更高版本」都算已同意；
        # 协议版本升到比已同意更新时，仍然照常重新征得一次同意。
        _a, _b = _ver_rank(got), _ver_rank(CONSENT_VERSION)
        return bool(_a and _b and _a >= _b)

    def set_consent(self, ok):
        self.s.setValue('legal/accepted', CONSENT_VERSION if ok else '')
        self.s.setValue('legal/accepted_at',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S') if ok else '')
        # 立即落盘：避免点「同意」后马上关窗/被强杀时丢掉同意记录（下次又弹）。
        try:
            self.s.sync()
        except Exception:
            pass

    def consent_at(self):
        return str(self.s.value('legal/accepted_at', '') or '')


# ---------------------------------------------------------------- 加密存储
class SecretStore:
    """Windows DPAPI（当前用户）加密；不可用时退化为加盐混淆（并标记）。"""

    # 盐值：**固定值**，发布后不得变更——改动会让用户已存的 API Key 无法解密。
    _DPAPI_ENTROPY = b'XingHong-Panel-UX-v1'

    @staticmethod
    def _dpapi(data, protect=True):
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [('cbData', ctypes.wintypes.DWORD),
                        ('pbData', ctypes.POINTER(ctypes.c_char))]

        def _blob(b):
            buf = ctypes.create_string_buffer(b, len(b))
            return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        bin_blob, keep = _blob(data)
        ent, keep2 = _blob(SecretStore._DPAPI_ENTROPY)
        out = DATA_BLOB()
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(bin_blob), None, ctypes.byref(ent), None, None,
                0x01, ctypes.byref(out))
        if not ok:
            raise OSError('DPAPI failed')
        try:
            raw = ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)
        return raw

    def __init__(self, prefs):
        self.p = prefs

    def save_key(self, key):
        key = (key or '').strip()
        if not key:
            self.p.s.remove('adv/aiApiKeyEnc')
            return
        try:
            enc = self._dpapi(key.encode('utf-8'), True)
            self.p.s.setValue('adv/aiApiKeyEnc', 'dpapi:' + base64.b64encode(enc).decode())
        except Exception:
            salt = hashlib.sha256(self._DPAPI_ENTROPY).digest()
            raw = bytes(b ^ salt[i % len(salt)] for i, b in enumerate(key.encode('utf-8')))
            self.p.s.setValue('adv/aiApiKeyEnc', 'obf:' + base64.b64encode(raw).decode())

    def load_key(self):
        v = str(self.p.s.value('adv/aiApiKeyEnc', '') or '')
        if not v:
            return ''
        try:
            kind, _, payload = v.partition(':')
            raw = base64.b64decode(payload)
            if kind == 'dpapi':
                return self._dpapi(raw, False).decode('utf-8')
            salt = hashlib.sha256(self._DPAPI_ENTROPY).digest()
            return bytes(b ^ salt[i % len(salt)] for i, b in enumerate(raw)).decode('utf-8')
        except Exception:
            return ''


# ---------------------------------------------------------------- 文本内容
# ---------------------------------------------------------------- 协议正文（随包文件）
# R38-26：正文从 legal/*.md 读取，不再内联在本文件；构建时随 spec datas 进 legal\。
# 好处：改文案不必动代码，git 里能直接 diff 正文。文件缺失会让同意页空白，
# 因此 legal_text() 缺文件即抛错，构建期另有 assert_legal_docs() 兜底。
LEGAL_DIR = Path(__file__).resolve().parent / 'legal'
LEGAL_CONTACT = 'dreamarchitect1026@gmail.com'
LEGAL_FILES = (('用户协议', '用户协议.md'),
               ('隐私说明', '隐私说明.md'),
               ('安全与免责提示', '安全与免责提示.md'),
               ('第三方组件与开源许可', '第三方组件与开源许可.md'))


def legal_text(fn):
    p = LEGAL_DIR / fn
    try:
        t = p.read_text(encoding='utf-8')
    except OSError:
        raise RuntimeError('LEGAL_DOC_MISSING: %s（安装包不完整或缺 legal 目录）' % p)
    return t.replace('%DOCVER%', CONSENT_VERSION).replace('%DOCDATE%', DOC_EFFECTIVE)


# 正文不再在导入期读取：缺文件时 LegalView 把该目录项标为不可用，
# 保证「装坏了仍能看其余四份」；构建期 assert_legal_docs() 仍会拦截缺文件出包。


def consent_version_line():
    """首启页与设置页共同展示的这一行；两条断言以它为准。"""
    return '协议版本 %s · 生效日期 %s' % (CONSENT_VERSION, DOC_EFFECTIVE)


def assert_legal_consistent():
    """断言：① 文档版本 = CONSENT_VERSION = UI 展示版本；② 文档邮箱 = UI 邮箱。"""
    ui = consent_version_line()
    for _title, fn in LEGAL_FILES:
        t = legal_text(fn)
        m = re.search(r'版本：(\S+)', t)
        got = m.group(1) if m else ''
        if got != CONSENT_VERSION or CONSENT_VERSION not in ui:
            raise RuntimeError('LEGAL_VERSION_MISMATCH: %s 文档版本 %r / UI %r / CONSENT %r'
                               % (fn, got, ui, CONSENT_VERSION))
        if LEGAL_CONTACT not in t:
            raise RuntimeError('LEGAL_CONTACT_MISMATCH: %s 未包含 %s' % (fn, LEGAL_CONTACT))

GUIDE = """《使用说明》

一、这个应用做什么
· 星虹智算是本机的 AI 生成工作台：发起生成任务、查看队列与结果、对成品做超分放大，都在同一个窗口完成。
· 生成与放大由你本机运行的 ComfyUI 与显卡执行；应用负责提交任务、展示状态与归档结果。
· 全程不依赖云端账号，只有你主动启动或填写的模型服务会被访问。

二、六个页面
1. 状态：算力与运行概况，先在这里确认服务是否在线。
2. 推理路线：KVMem、AI 对话（LM Studio）、ComfyUI、代理四项服务的运行状态，并提供启动/停止操作。
3. ComfyUI 生图：选模型、填参数、提交图像任务，完成后进入作品墙。
4. 视频生成：提交视频任务，产物进入视频墙。
5. 超分放大：图片或视频按 2×、3×、4×、6×、8×、10× 六档放大，引擎为 VOSR2、SeedVR2、Real-ESRGAN。
6. 配置中心：数据设置、生成记录、运行日志、进阶配置；说明与协议在「进阶配置」下方的入口里，点开是独立的二级页面。

三、进阶配置（配置中心 → 进阶配置）
· 显示与主题：主题配色、动画与特效强度、减少动效。
· 运行：性能模式（省电 / 均衡）。
· 运行与曲线：端口轮询频率、图表点数、曲线平滑。
· 通知与日志：系统通知、免打扰、日志级别。
· 启动：开机自启。
每项右侧「?」写明做什么、改了会怎样、什么时候该动它；改完点「保存并应用」。标「即时」的项保存后立即生效（日志级别需切到「运行日志」页刷新后生效），其余项需重启应用。

四、任务与算力
· 提交后的任务进入队列，完成后出现在对应结果墙；耗时取决于模型大小、分辨率与显卡。
· 服务离线时任务不会开始，先到「状态」或「推理路线」确认对应服务已启动。
· 显存不足会中止并报错；换更小的模型或降分辨率后重试，不要连续快速重试。

五、结果与存储
· 结果进入对应结果墙（作品墙、视频墙、放大结果墙），双击可预览。
· 文件保存在本机输出目录，应用不会自动删除；「一键清理」只清理运行日志。

六、数据与隐私
· 生成记录、日志与配置都保存在本机；卸载应用不会自动删除，保留期限与清理方式见《隐私说明》第四条。
· 详见《隐私说明》与《用户协议》。
"""


# 文档正文容器：允许内部滚动，滚动条极细、低对比、悬停才显现
DOC_BROWSER_QSS = (
    'QTextBrowser{border-radius:12px; padding:10px; font-size:13px;'
    'background:rgba(255,255,255,0.04);}'
    'QScrollBar:vertical{background:transparent;width:6px;margin:0;}'
    'QScrollBar::handle:vertical{background:rgba(255,255,255,0);'
    'border-radius:4px;min-height:28px;}'
    'QScrollBar::handle:vertical:hover{background:rgba(255,255,255,0.30);}'
    'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;'
    'background:transparent;}'
    'QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{'
    'background:transparent;}'
    'QScrollBar:horizontal{background:transparent;height:6px;margin:0;}'
    'QScrollBar::handle:horizontal{background:rgba(255,255,255,0);'
    'border-radius:4px;min-width:28px;}'
    'QScrollBar::handle:horizontal:hover{background:rgba(255,255,255,0.30);}'
    'QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;'
    'background:transparent;}'
    'QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{'
    'background:transparent;}')


# 目录五条：显示名 → legal 文件名（None = 内联正文，如使用说明）。
# 与 LEGAL_FILES 不同，这里包含使用说明；运行期缺失的文件在目录中显示为不可用。
LEGAL_VIEW_ITEMS = (('用户协议', '用户协议.md'),
                    ('隐私说明', '隐私说明.md'),
                    ('安全与免责', '安全与免责提示.md'),
                    ('第三方许可', '第三方组件与开源许可.md'),
                    ('使用说明', None))

# 格式条款提示：9.4（责任限制/免责）与 11.2（管辖法院）必须单独、加粗、显著，
# 不能混在整段小字里，否则消费者可主张该条未合理提示而无效。
LEGAL_TIP_TEXT = (
    '【重点条款提示】\n'
    '· 第九条 9.4：免责与责任限制，以及依法不可排除的责任范围；\n'
    '· 第十一条 11.2：争议由北京市海淀区人民法院管辖。\n'
    '上述两条对你的权利义务有重大影响，请重点阅读。')

LEGAL_NAV_QSS = (
    'QListWidget{background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.10);'
    ' border-radius:10px; padding:4px; outline:0;}'
    'QListWidget::item{padding:9px 10px; border-radius:7px; color:#9fb4d0;}'
    'QListWidget::item:hover{background:rgba(255,255,255,0.06); color:#e8f4ff;}'
    'QListWidget::item:selected{background:rgba(3,169,244,0.22); color:#ffffff;'
    ' font-weight:700;}'
    'QListWidget::item:disabled{color:rgba(159,180,208,0.35);}')


def legal_view_text(fn):
    """目录项正文；文件缺失返回 None（目录标不可用），不抛错。

    LEGAL_DOC_MISSING 仍由 legal_text() 与构建期 assert_legal_docs() 负责：
    运行期只降级不炸整页，缺文件照样不许出包。
    """
    if fn is None:
        return GUIDE
    try:
        return legal_text(fn)
    except RuntimeError:
        return None


class LegalView(QWidget):
    """说明与协议的唯一视图：重点提示 + 目录 + 单份正文。

    宽屏（>= COMPACT_WIDTH）左侧纵向目录、右侧只显示选中那一份；
    窄屏退化为下拉选择 + 正文，不做横向挤压。首启 ConsentDialog 与
    设置页「说明与协议」共用本类，禁止各写一份。
    """

    COMPACT_WIDTH = 700

    def __init__(self, parent=None, strict=False):
        super().__init__(parent)
        self._compact = None
        self._cur = -1
        self._syncing = False
        try:
            assert_legal_consistent()
        except RuntimeError as e:
            if strict or not str(e).startswith('LEGAL_DOC_MISSING'):
                raise
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        tip = QLabel(LEGAL_TIP_TEXT)
        tip.setWordWrap(True)
        tip.setStyleSheet('color:#ffb050; font-weight:700; font-size:12px;'
                          'background:rgba(255,176,80,0.10); border:1px solid rgba(255,176,80,0.45);'
                          'border-radius:8px; padding:8px;')
        root.addWidget(tip)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.list = QListWidget()
        self.list.setObjectName('legalNav')
        self.list.setFixedWidth(150)
        self.list.setStyleSheet(LEGAL_NAV_QSS)
        row.addWidget(self.list)

        right = QVBoxLayout()
        right.setSpacing(4)
        self.combo = QComboBox()
        self.combo.setObjectName('legalNavCompact')
        self.combo.hide()
        right.addWidget(self.combo)
        self.title = QLabel()
        self.title.setStyleSheet('font-size:16px; font-weight:700;')
        right.addWidget(self.title)
        self.ver = QLabel(consent_version_line())
        self.ver.setStyleSheet('color:#8a8f98; font-size:11px;')
        right.addWidget(self.ver)
        self.body = QTextBrowser()
        self.body.setObjectName('docBody')
        self.body.setStyleSheet(DOC_BROWSER_QSS)
        right.addWidget(self.body, 1)
        row.addLayout(right, 1)
        root.addLayout(row, 1)

        self._texts = []
        for i, (name, fn) in enumerate(LEGAL_VIEW_ITEMS):
            text = legal_view_text(fn)
            self._texts.append(text)
            ok = text is not None
            label = name if ok else '%s（不可用）' % name
            self.list.addItem(label)
            self.combo.addItem(label)
            if not ok:
                it = self.list.item(i)
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                ci = self.combo.model().item(i)
                if ci is not None:
                    ci.setEnabled(False)

        self.list.currentRowChanged.connect(self._on_row)
        self.combo.currentIndexChanged.connect(self._on_combo)
        first = next((i for i, t in enumerate(self._texts) if t is not None), 0)
        self.select(first)

    def select(self, idx):
        """只显示第 idx 份正文；缺失项拒绝切换（目录里已标不可用）。"""
        if not (0 <= idx < len(self._texts)) or self._texts[idx] is None:
            return
        self._cur = idx
        self._syncing = True
        try:
            self.list.setCurrentRow(idx)
            self.combo.setCurrentIndex(idx)
        finally:
            self._syncing = False
        self.title.setText(LEGAL_VIEW_ITEMS[idx][0])
        self.ver.setText(consent_version_line() if LEGAL_VIEW_ITEMS[idx][1] is not None
                         else '版本 v%s · 更新 %s' % (APP_VERSION, DOCS_UPDATED))
        self.body.setHtml(doc_html(self._texts[idx]))
        self.body.verticalScrollBar().setValue(0)

    def _on_row(self, row):
        if not self._syncing and row != self._cur:
            self.select(row)

    def _on_combo(self, idx):
        if not self._syncing and idx != self._cur:
            self.select(idx)

    def _apply_compact(self, compact):
        if compact == self._compact:
            return
        self._compact = compact
        self.list.setVisible(not compact)
        self.combo.setVisible(compact)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_compact(ev.size().width() < self.COMPACT_WIDTH)

    def is_compact(self):
        return bool(self._compact)


def legal_page(on_back=None):
    """「说明与协议」二级页面：页头（返回 + 标题 + 协议版本）+ 共用 LegalView。

    产品版本只留在「关于」；这里只显示协议版本与生效日期，避免被读成产品版本。
    """
    pg = QWidget()
    pg.setObjectName('legalPage')
    pv = QVBoxLayout(pg)
    pv.setContentsMargins(16, 12, 16, 12)
    pv.setSpacing(10)
    hdr, _refs = page_head(text='说明与协议', meta=consent_version_line(), back=on_back)
    pv.addWidget(hdr)
    pv.addWidget(LegalView(pg), 1)
    return pg


# ---------------------------------------------------------------- 控件
def help_badge(key, parent=None):
    """统一尺寸的问号徽标（18×18，与控件基线对齐，点击/悬停显示解释）。"""
    text = HELP.get(key, ('', ''))
    body = ('<b>%s</b><br>做什么：%s<br>何时用/影响：%s' %
            (text[0], text[1], text[2]) if len(text) == 3 else '<b>%s</b>' % (text[0] or key))
    lb = QLabel('?')
    lb.setFixedSize(18, 18)
    lb.setAlignment(Qt.AlignCenter)
    lb.setCursor(Qt.WhatsThisCursor)
    lb.setStyleSheet('QLabel{background:rgba(255,255,255,0.10); color:#9fb4c8;'
                     'border:1px solid rgba(255,255,255,0.16); border-radius:9px;'
                     'font-size:11px; font-weight:700;}'
                     'QLabel:hover{background:rgba(3,169,244,0.28); color:#fff;}')
    lb.setToolTip(body)
    lb.setWhatsThis(body)

    def _popup(ev):
        from PySide6.QtWidgets import QToolTip
        QToolTip.showText(lb.mapToGlobal(lb.rect().bottomLeft()), body, lb)
    lb.mousePressEvent = _popup
    return lb


class IntField(QLineEdit):
    """整数输入框：替代 QSpinBox（应用级 QSS 下 QSpinBox 不绘制数字）。"""

    def __init__(self, lo, hi, val, parent=None):
        super().__init__(parent)
        self._lo, self._hi = int(lo), int(hi)
        self.setValidator(QIntValidator(self._lo, self._hi, self))
        self.setText(str(int(val)))
        self.setMinimumHeight(28)

    def value(self):
        try:
            v = int(self.text() or 0)
        except Exception:
            v = 0
        return max(self._lo, min(self._hi, v))

    def setValue(self, v):
        self.setText(str(max(self._lo, min(self._hi, int(v)))))


class AdvPanel(QWidget):
    """R024：进阶配置面板（嵌入"设置"分区，不再是独立突兀弹窗）。

    每项包含：名称 / 一句话作用 / 控件 / 问号 / 生效时机 / 使用说明链接。
    """

    applied = Signal(dict)
    changed = Signal(str, object)

    def __init__(self, parent, prefs, secrets, on_apply=None, on_clear=None,
                 on_diag=None, on_guide=None, guide_keys=None, skip_keys=None,
                 on_legal=None):
        super().__init__(parent)
        self.p = prefs
        self.sec = secrets
        self.on_apply = on_apply
        self.on_clear = on_clear
        self.on_diag = on_diag
        self.on_guide = on_guide
        self.on_legal = on_legal
        self.guide_keys = guide_keys or {}
        # R026：AI 相关项已收拢到「小酷 AI → AI 配置」，进阶配置里不再出现第二份
        self.skip_keys = set(skip_keys or ())
        self.controls = {}
        self._build()

    # ---- 构建 ----
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel('进阶配置')
        t.setStyleSheet('font-size:17px; font-weight:700;')
        head.addWidget(t)
        sub = QLabel('所有开关都会即时生效或在标注的时机生效；每项右侧「?」为完整解释。')
        sub.setObjectName('hint')
        head.addWidget(sub, 1)
        v.addLayout(head)
        # R026：进阶配置不再用滚动区（红线：页面不得出现滚动条）——
        # 改为分组折叠面板，一次只展开一组，永远不需要滚动。
        self._groups = []
        self._first_group = True
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(2, 2, 8, 2)
        hv.setSpacing(8)
        for name, items in SPEC:
            items = [it for it in items if it[0] not in self.skip_keys]
            if not items:
                continue
            hv.addWidget(self._build_group(name, items))
        hv.addWidget(self._build_legal_entry())
        hv.addStretch(1)
        v.addWidget(holder, 1)
        v.addLayout(self._build_footer())
        self._refresh_state()

    def _build_group(self, name, items):
        # 折叠卡片：标题行可点击，一次只展开一组（配合无滚动条红线）
        card = QFrame()
        card.setObjectName('advCard')
        card.setStyleSheet(
            'QFrame#advCard{border:1px solid %(border)s; border-radius:10px;'
            ' background:rgba(255,255,255,0.035);}'
            'QLabel#desc{color:%(text_sub)s; font-size:11px;}'
            'QComboBox, QLineEdit{color:%(text)s; font-size:12px;'
            ' background:rgba(255,255,255,0.07); border:1px solid %(border)s;'
            ' border-radius:8px; padding:2px 8px;}'
            'QCheckBox{color:%(text)s; font-size:12px; spacing:8px;}'
            'QLabel#eff{color:%(text_sub)s; font-size:10px;'
            ' border:1px solid %(border)s; border-radius:8px; padding:1px 6px;}'
            % TOKENS)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(10, 6, 10, 6)
        cv.setSpacing(2)
        head = QPushButton('')
        head.setObjectName('advHead')
        head.setCheckable(True)
        head.setCursor(Qt.PointingHandCursor)
        head.setProperty('nm', name)
        head.setStyleSheet(
            'QPushButton#advHead{text-align:left; font-size:13px; font-weight:700;'
            ' color:%(text)s; background:transparent; border:0; padding:4px 2px;}'
            'QPushButton#advHead:hover{color:%(accent)s;}'
            % TOKENS)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for r, (key, label, kind, arg) in enumerate(items):
            nm = QLabel(label)
            nm.setStyleSheet('font-size:12px;')
            nm.setFixedWidth(150)
            ctl = self._make_ctl(key, kind, arg)
            ctl.setMinimumHeight(28)
            ctl.setMaximumWidth(260)
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(1)
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(ctl)
            row.addWidget(help_badge(key, box))
            row.addStretch(1)
            bv.addLayout(row)
            _desc = HELP.get(key, ('', '', ''))
            d = QLabel((_desc[1] if len(_desc) > 1 else '').split('。')[0] + '。')
            d.setObjectName('desc')
            d.setWordWrap(True)
            bv.addWidget(d)
            eff = QLabel(EFFECT.get(key, '即时'))
            eff.setObjectName('eff')
            grid.addWidget(nm, r, 0, Qt.AlignTop)
            grid.addWidget(box, r, 1)
            grid.addWidget(eff, r, 2, Qt.AlignVCenter)
            lk = QPushButton('说明')
            lk.setObjectName('ghost')
            lk.setFixedSize(44, 44)
            lk.setStyleSheet('font-size:11px; padding:0; border:0; background:transparent;')
            lk.clicked.connect(lambda _=False, k=key: self._open_guide(k))
            grid.addWidget(lk, r, 3, Qt.AlignVCenter)
        _open = self._first_group
        self._first_group = False
        head.setChecked(_open)
        head.setText(('▾  ' if _open else '▸  ') + name)
        body.setVisible(_open)
        head.toggled.connect(lambda on, h=head, b=body, n=name: self._toggle_group(h, b, n))
        cv.addWidget(head)
        cv.addWidget(body)
        self._groups.append((head, body))
        return card

    def _build_legal_entry(self):
        """「进阶配置」组下面的一行入口：说明与协议 ›，点击进入二级页面。"""
        btn = QPushButton()
        btn.setObjectName('advLegalEntry')
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(44)
        btn.setStyleSheet(
            'QPushButton#advLegalEntry{text-align:left; background:rgba(255,255,255,0.035);'
            ' border:1px solid %(border)s; border-radius:10px;}'
            'QPushButton#advLegalEntry:hover{background:rgba(120,190,255,0.10);'
            ' border-color:%(accent)s;}'
            'QLabel#advLegalName{color:%(text)s; font-size:13px; font-weight:700;'
            ' background:transparent;}'
            'QLabel#advLegalArrow{color:#cfe8ff; font-size:18px; font-weight:700;'
            ' background:transparent;}' % TOKENS)
        bl = QHBoxLayout(btn)
        bl.setContentsMargins(12, 8, 14, 8)
        nm = QLabel('说明与协议')
        nm.setObjectName('advLegalName')
        ar = QLabel('›')
        ar.setObjectName('advLegalArrow')
        bl.addWidget(nm)
        bl.addStretch(1)
        bl.addWidget(ar)
        btn.clicked.connect(lambda _=False: self.on_legal() if self.on_legal else None)
        return btn

    def _toggle_group(self, head, body, name):
        """手风琴：展开一组时自动收起其它组，避免纵向溢出。"""
        if not head.isChecked():
            body.setVisible(False)
            head.setText('▸  ' + name)
            return
        for h, b in self._groups:
            if h is head:
                continue
            h.blockSignals(True)
            h.setChecked(False)
            h.setText('▸  ' + h.property('nm'))
            h.blockSignals(False)
            b.setVisible(False)
        body.setVisible(True)
        head.setText('▾  ' + name)

    def _make_ctl(self, key, kind, arg):
        if kind == 'choice':
            ctl = QComboBox()
            ctl.addItems(arg)
            _cur = str(self.p.get(key))
            ctl.setCurrentText(_cur if _cur in arg else str(DEFAULTS.get(key, arg[0])))
        elif kind == 'bool':
            ctl = QCheckBox('已开启' if self.p.get(key) else '已关闭')
            ctl.setChecked(bool(self.p.get(key)))
            ctl.toggled.connect(lambda on, c=ctl: c.setText('已开启' if on else '已关闭'))
        elif kind == 'int':
            # QSpinBox 在应用级 QSS + NoButtons 下不绘制数字（实测空白），改用带校验的输入框
            ctl = IntField(arg[0], arg[1], int(self.p.get(key)))
            ctl.setMinimumWidth(96)
        elif kind == 'secret':
            ctl = QLineEdit()
            ctl.setEchoMode(QLineEdit.Password)
            ctl.setPlaceholderText('仅本机 DPAPI 加密保存；留空=清除')
            ctl.setText(self.sec.load_key())
        else:
            ctl = QLineEdit(str(self.p.get(key) or ''))
        self.controls[key] = (ctl, kind)
        if kind == 'choice':
            ctl.currentTextChanged.connect(lambda t, k=key: self._on_change(k, t))
        elif kind == 'bool':
            ctl.toggled.connect(lambda on, k=key: self._on_change(k, on))
        elif kind == 'int':
            ctl.textChanged.connect(lambda t, k=key, c=ctl: self._on_change(k, c.value()))
        else:
            ctl.textEdited.connect(lambda t, k=key: self._on_change(k, t))
        return ctl

    def _on_change(self, key, val):
        if hasattr(self, 'status'):
            # R031：页脚宽度有限，正文用短文案，完整提示放 tooltip（避免硬截断）
            _full = '已修改「%s」，点击「保存并应用」后生效（%s）' % (
                self._label(key), EFFECT.get(key, '即时'))
            _short = '已修改「%s」' % self._label(key)
            self.status.setText(_short)
            self.status.setToolTip(_full)
        self.changed.emit(key, val)
        self._refresh_state()

    def _label(self, key):
        for _n, items in SPEC:
            for k, lab, _a, _b in items:
                if k == key:
                    return lab
        return key

    def _build_footer(self):
        row = QHBoxLayout()
        self.status = QLabel('')
        self.status.setObjectName('hint')
        # 单行 + 省略：避免状态文案换行 3 行挤压底部按钮（综合审查项）
        self.status.setWordWrap(False)
        self.status.setMinimumWidth(150)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.status, 1)
        for text, fn, name in (('恢复默认', self._reset, ''),
                               ('导出诊断', self._diag, ''),
                               ('一键清理', self._clear, ''),
                               ('保存并应用', self._save, 'primary')):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            # R031：设置页页脚统一尺寸（84×30 / padding 0 6px）
            b.setMinimumSize(84, 30)
            b.setStyleSheet('font-size:11px; padding:0 6px;')
            b.clicked.connect(fn)
            if text == '保存并应用':
                self.save_b = b
            row.addWidget(b)
        return row

    # ---- 行为 ----
    def collect(self):
        out = {}
        for key, (ctl, kind) in self.controls.items():
            if kind == 'choice':
                out[key] = ctl.currentText()
            elif kind == 'bool':
                out[key] = ctl.isChecked()
            elif kind == 'int':
                out[key] = ctl.value()
            else:
                out[key] = ctl.text().strip()
        return out

    def _refresh_state(self):
        if hasattr(self, 'status') and not self.status.text():
            self.status.setText('设置改动后点「保存并应用」生效')

    def _open_guide(self, key):
        if callable(self.on_guide):
            self.on_guide(self.guide_keys.get(key, key))
        else:
            QMessageBox.information(self, '使用说明',
                                    '%s\n\n%s\n\n%s' % (HELP.get(key, (key,))[0],
                                                        HELP.get(key, ('', ''))[1],
                                                        HELP.get(key, ('', '', ''))[2]))

    def save(self):
        # 防重入：快速连点/重复信号不重复写配置与重放生效逻辑
        if getattr(self, '_saving', False):
            return
        self._saving = True
        try:
            self._save_impl()
        finally:
            self._saving = False

    def _save_impl(self):
        vals = self.collect()
        for k, val in vals.items():
            self.p.set(k, val)
        try:
            if callable(self.on_apply):
                self.on_apply(vals)
        except Exception as e:
            self.status.setText('应用失败：%s' % e)
            return
        self.applied.emit(vals)
        self.status.setText('已保存并应用（%s）' % datetime.now().strftime('%H:%M:%S'))
        self._refresh_state()

    # 兼容旧调用
    _save = save

    def _reset(self):
        if QMessageBox.question(self, '恢复默认', '恢复全部进阶配置为默认值？\n'
                                '（API Key 也会被清除）') != QMessageBox.Yes:
            return
        self.p.reset()
        self.sec.save_key('')
        for key, (ctl, kind) in self.controls.items():
            d = DEFAULTS.get(key)
            if kind == 'choice':
                ctl.setCurrentText(str(d))
            elif kind == 'bool':
                ctl.setChecked(bool(d))
            elif kind == 'int':
                ctl.setValue(int(d))
            else:
                ctl.setText('' if kind == 'secret' else str(d or ''))
        # R031：正文短文案，完整提示放 tooltip
        self.status.setText('已恢复默认，待写入')
        self.status.setToolTip('已恢复默认，点击「保存并应用」写入')

    def _diag(self):
        if callable(self.on_diag):
            try:
                self.status.setText('诊断已导出：%s' % self.on_diag())
            except Exception as e:
                self.status.setText('导出失败：%s' % e)
        else:
            self.status.setText('当前应用未提供诊断导出')

    def _clear(self):
        if QMessageBox.question(self, '清理数据',
                                '将删除本机运行日志（生成记录与作品文件不受影响）。继续？') \
                != QMessageBox.Yes:
            return
        if callable(self.on_clear):
            try:
                self.on_clear()
                self.status.setText('已清理')
            except Exception as e:
                self.status.setText('清理失败：%s' % e)


class ConsentDialog(QDialog):
    """首次启动的隐私说明 + 用户协议确认。

    结构：摘要 → 重点条款提示 → 目录 + 正文 → 同意/拒绝。
    目录与正文复用设置页同一个 LegalView，不另写一份。
    """

    def __init__(self, parent, prefs):
        super().__init__(parent)
        self.setWindowTitle('隐私说明与用户协议')
        self.resize(760, 620)
        self.setStyleSheet(_qss())
        self.p = prefs
        assert_legal_consistent()          # 首启即断言：版本三者一致、邮箱一致
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        lb = QLabel('首次使用请阅读并确认以下内容（可随时在「配置中心 → 说明与协议」回看与撤回）')
        lb.setWordWrap(True)
        v.addWidget(lb)
        ver = QLabel(consent_version_line())
        ver.setStyleSheet('color:#8a8f98; font-size:11px;')
        v.addWidget(ver)
        self.view = LegalView(self, strict=True)
        v.addWidget(self.view, 1)
        bb = QDialogButtonBox()
        self.ok_b = bb.addButton('同意并继续', QDialogButtonBox.AcceptRole)
        self.no_b = bb.addButton('暂不同意（AI 不可用）', QDialogButtonBox.RejectRole)
        self.ok_b.clicked.connect(self.accept)
        self.no_b.clicked.connect(self.reject)
        v.addWidget(bb)

    def accept(self):
        self.p.set_consent(True)
        super().accept()


def ensure_consent(parent, prefs):
    """返回是否已同意；首次启动弹窗，拒绝时保持未同意（AI 停用）。"""
    if prefs.consent_ok():
        return True
    dlg = ConsentDialog(parent, prefs)
    ok = dlg.exec() == QDialog.Accepted
    return ok
