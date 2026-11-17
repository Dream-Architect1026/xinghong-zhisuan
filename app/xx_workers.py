# -*- coding: utf-8 -*-
"""小星（本地模型助手）后台线程：对话请求 / Agent 步骤执行（C1-B3 第 1 笔）。

原位置：xinghong_launcher.py L6417–6482（XxChatWorker）与 L6485–6497 + L6521–6602
（XX_AGENT_TOOLS / XX_AGENT_NAMES / XxAgentWorker），整段原样搬出，逐行一致。

分层：app 层，依赖 PySide6 / 标准库 / infra(xh_http)；launcher 的 api、_xx_scrub、
会话目录全部显式传参（参数名与原标识符逐字一致），禁止 import xinghong_launcher。

句柄：launcher 解包后仍用原来的两个名字（XxChatWorker / XxAgentWorker）传给
ui/pages_xiaoxing.py，构造签名不变（XxChatWorker(ep,key,model,msgs,30,parent=w) /
XxAgentWorker(steps, parent=w)）。
"""
import json
import os
import socket
import time

from PySide6.QtCore import QThread, Signal

import xh_http

__all__ = ['build_xx_workers', 'XX_AGENT_TOOLS', 'XX_AGENT_NAMES']

# ── P3：小星的 Agent 工具清单（步骤定义的合法工具名即此表，顺序即 UI 顺序）──────
XX_AGENT_TOOLS = [
    ('面板健康', 'GET /api/health：面板服务自检分数与检查项'),
    ('运行状态', 'GET /api/state：当前服务/设备状态快照'),
    ('告警', 'GET /api/alerts：最近一条告警'),
    ('最近产物', 'GET /api/outputs?limit=N：参数填数量（默认 5，上限 20）'),
    ('会话统计', '本地 ai_sessions：文件数与记录数（不读内容）'),
]
XX_AGENT_NAMES = [n for n, _ in XX_AGENT_TOOLS]


def build_xx_workers(*, api, _xx_scrub, XX_SESSION_DIR):
    """返回 {'XxChatWorker': 类, 'XxAgentWorker': 类}。

    参数名与 launcher 模块级原标识符逐字一致（MECHANICAL_EQUAL 逐行比对）。
    """

    class XxChatWorker(QThread):
        """对话请求：网络只在工作线程，30 s 硬超时（分块读时按累计耗时判），stop() 可中断。"""

        done = Signal(object)

        def __init__(self, endpoint, key, model, messages, timeout=30, parent=None):
            super().__init__(parent)
            self._ep = endpoint
            self._key = key
            self._model = model
            self._msgs = messages
            self._timeout = float(timeout or 30)
            self._stop = False

        def stop(self):
            self._stop = True

        def run(self):
            _t0 = time.time()
            _res = {'ok': False, 'text': '', 'err': '', 'ms': 0}
            try:
                _body = {'messages': self._msgs, 'temperature': 0.4, 'stream': True}
                if self._model:
                    _body['model'] = self._model
                _hdr = {'Content-Type': 'application/json'}
                if self._key:
                    _hdr['Authorization'] = 'Bearer ' + self._key
                # 握手用较宽的超时；进入读循环后压到 1 s 一档，保证 stop() 能被及时看到
                _chunks = list(xh_http.stream_post(
                    self._ep, json.dumps(_body).encode('utf-8'), _hdr,
                    handshake_timeout=max(1.0, min(self._timeout, 10.0)),
                    read_timeout=1.0, total_timeout=self._timeout,
                    should_stop=lambda: self._stop, started_at=_t0))
                _txt = b''.join(_chunks).decode('utf-8', 'replace')
                if 'data:' in _txt:      # SSE 流式：只取 delta.content 拼接
                    _acc = []
                    for _ln in _txt.splitlines():
                        _ln = _ln.strip()
                        if not _ln.startswith('data:'):
                            continue
                        _p = _ln[5:].strip()
                        if _p in ('', '[DONE]'):
                            continue
                        try:
                            _d = (json.loads(_p).get('choices') or [{}])[0].get('delta') or {}
                            if _d.get('content'):
                                _acc.append(_d['content'])
                        except Exception:
                            continue
                    _res['text'] = ''.join(_acc).strip()
                else:                    # 非流式：整段 JSON 一次解析
                    _obj = json.loads(_txt or '{}')
                    _msg = ((_obj.get('choices') or [{}])[0] or {}).get('message') or {}
                    _res['text'] = str(_msg.get('content') or '').strip()
                _res['ok'] = bool(_res['text'])
                if not _res['ok']:
                    _res['err'] = '端点返回空内容'
            except socket.timeout:
                _res['err'] = '超时 %d s' % int(self._timeout)
            except Exception as _e:
                _res['err'] = _xx_scrub(str(_e) or _e.__class__.__name__)[:200]
            _res['ms'] = int((time.time() - _t0) * 1000)
            try:
                self.done.emit(_res)
            except Exception:
                pass

    class XxAgentWorker(QThread):
        """按定义顺序逐步执行本地工具；每步 10 s 超时，失败原因原样带回主线程。"""

        step = Signal(int, object)
        done = Signal(object)

        def __init__(self, steps, parent=None):
            super().__init__(parent)
            self._steps = [dict(_s) for _s in steps]

        @staticmethod
        def _one(tool, arg):
            if tool == '面板健康':
                _c, _d = api('GET', '/api/health', timeout=10)
                if _c != 200:
                    raise RuntimeError('HTTP %s：%s' % (_c, str(_d)[:120]))
                _d = _d if isinstance(_d, dict) else {}
                return 'score=%s，检查项 %d 个' % (_d.get('score'), len(_d.get('checks') or []))
            if tool == '运行状态':
                _c, _d = api('GET', '/api/state', timeout=10)
                if _c != 200:
                    raise RuntimeError('HTTP %s：%s' % (_c, str(_d)[:120]))
                _d = _d if isinstance(_d, dict) else {}
                _hot = [_k for _k in ('kv', 'comfy', 'lms', 'proxy', 'services', 'gpu')
                        if _k in _d]
                return '快照字段 %d 个%s' % (len(_d), ('；含 ' + '/'.join(_hot)) if _hot else '')
            if tool == '告警':
                _c, _d = api('GET', '/api/alerts', timeout=10)
                if _c != 200:
                    raise RuntimeError('HTTP %s：%s' % (_c, str(_d)[:120]))
                _n = len(_d) if isinstance(_d, (list, dict)) else 1
                _txt = str(_d)[:80] if _n else '无告警'
                return '%d 条；%s' % (_n, _txt)
            if tool == '最近产物':
                _n = 5
                if arg.strip():
                    try:
                        _n = max(1, min(20, int(arg.strip())))
                    except Exception:
                        raise RuntimeError('参数应为 1-20 的整数，收到 %r' % arg.strip())
                _c, _d = api('GET', '/api/outputs?limit=%d' % _n, timeout=10)
                if _c != 200:
                    raise RuntimeError('HTTP %s：%s' % (_c, str(_d)[:120]))
                _items = _d if isinstance(_d, list) else (
                    _d.get('items') if isinstance(_d, dict) else []) or []
                _names = [str((_x or {}).get('name') or '?')[:18] for _x in _items[:3]]
                return '返回 %d 个%s' % (len(_items), ('：' + '、'.join(_names)) if _names else '')
            if tool == '会话统计':
                _fs = [os.path.join(XX_SESSION_DIR, _n) for _n in
                       (os.listdir(XX_SESSION_DIR) if os.path.isdir(XX_SESSION_DIR) else [])
                       if _n.endswith('.jsonl')]
                _lines = 0
                for _p in _fs:
                    try:
                        with open(_p, encoding='utf-8') as _fp:
                            _lines += sum(1 for _l in _fp if _l.strip())
                    except Exception:
                        pass
                return '%d 个文件，%d 条记录' % (len(_fs), _lines)
            raise RuntimeError('未知工具 %r' % tool)

        def run(self):
            _out = []
            for _i, _s in enumerate(self._steps):
                _t0 = time.time()
                _r = {'i': _i, 'tool': _s.get('tool'), 'arg': _s.get('arg') or '',
                      'ok': False, 'ms': 0, 'summary': '', 'err': ''}
                try:
                    _r['summary'] = self._one(_s.get('tool'), str(_s.get('arg') or ''))
                    _r['ok'] = True
                except Exception as _e:
                    _r['err'] = _xx_scrub(str(_e) or _e.__class__.__name__)[:200]
                _r['ms'] = int((time.time() - _t0) * 1000)
                _out.append(_r)
                try:
                    self.step.emit(_i, _r)
                except Exception:
                    pass
            try:
                self.done.emit({'ok': all(_x['ok'] for _x in _out), 'steps': _out})
            except Exception:
                pass

    return {'XxChatWorker': XxChatWorker, 'XxAgentWorker': XxAgentWorker}
