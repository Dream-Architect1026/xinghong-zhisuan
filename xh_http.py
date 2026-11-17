# -*- coding: utf-8 -*-
"""统一 HTTP 客户端（星虹智算）。

收口前有 4 处各自手写的 HTTP 调用：
    xinghong_launcher._probe_panel_identity   http.client  GET /api/health（身份校验）
    xinghong_launcher.api                     http.client  任意方法 + JSON
    xinghong_launcher.get_bytes               http.client  GET 二进制
    xinghong_launcher.XxChatWorker.run        urllib       POST 外部端点（SSE 流式、可中断）
本模块把超时、重试、错误归类、失败日志收成一处，调用方只剩「要什么」。

行为约定（与收口前逐项等价，改之前先读 R38-21 收口报告）：
- 本地面板请求默认 retries=0：失败原样抛出 socket.timeout / ConnectionRefusedError 等，
  异常类型不包装，调用方既有的 try/except 语义不变；只有显式传 retries>0 才重试，
  且只对「连接没建起来/读超时」重试，HTTP 4xx/5xx 一律不重试。
- api 的返回结构不变：(status, 能解析就解析成对象，否则 decode 后的文本)。
- probe 永不抛：任何异常都折成 (False, '连接异常 XxxError')，与旧实现同文案。
- 日志只在失败时写（成功不落盘），由 set_logger() 注入落点；未注入时静默。
"""
import http.client
import json
import socket
import time
import urllib.request

# C1：模块导出清单（tools/check_imports.py 校验）
__all__ = ['set_logger', 'classify', 'PanelClient', 'stream_post',
           'K_OK', 'K_TIMEOUT', 'K_REFUSED', 'K_DNS', 'K_HTTP']

# 错误归类（日志/上报共用一套名字，别在各调用点自造字符串）
K_OK = 'ok'
K_TIMEOUT = 'timeout'
K_REFUSED = 'refused'
K_DNS = 'dns'
K_HTTP = 'http'          # 4xx / 5xx：连接是通的，服务端给了拒绝
K_OTHER = 'other'

_logger = None


def set_logger(fn):
    """注入失败日志落点：fn(kind, method, path, ms, status, detail)。传 None 关闭。"""
    global _logger
    _logger = fn


def classify(exc):
    """异常 → 归类名。只做判断，不改异常。"""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return K_TIMEOUT
    if isinstance(exc, ConnectionRefusedError):
        return K_REFUSED
    if isinstance(exc, socket.gaierror):
        return K_DNS
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return K_OTHER
    return K_OTHER


def _log(kind, method, path, ms, status, detail=''):
    if _logger is None or kind == K_OK:
        return
    try:
        _logger(kind, method, path, round(ms, 1), status, detail)
    except Exception:
        pass


class PanelClient:
    """对本地面板服务（127.0.0.1:<port>）的客户端。

    token_fn：启动时由 launcher 注入 _local_token（本地 token 鉴权头 X-Local-Token），
    每次请求现取，token 轮换后无需重建客户端。
    """

    def __init__(self, host, port, token_fn=None, retries=0):
        self.host = host
        self.port = int(port)
        self._token_fn = token_fn
        self.retries = int(retries or 0)

    # ---------------------------------------------------------------- 内部
    def _tok(self):
        if self._token_fn is None:
            return None
        try:
            return self._token_fn()
        except Exception:
            return None

    def _headers(self, extra, has_body, tok=None):
        h = dict(extra or {})
        if has_body:
            h['Content-Type'] = 'application/json'
        if tok:
            h['X-Local-Token'] = str(tok)
        return h

    def _request(self, method, path, body, extra_headers, timeout, retries, read_limit=None):
        """→ (status, raw_bytes, elapsed_ms)。失败原样抛，只在这里写日志/重试。"""
        retries = self.retries if retries is None else int(retries)
        attempt = 0
        auth_retry = 0
        auth_deadline = time.time() + 3.0   # 开机换 token 的等待预算
        while True:
            t0 = time.time()
            status = None
            tok = self._tok()
            try:
                c = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
                try:
                    c.request(method, path, body=body,
                              headers=self._headers(extra_headers, bool(body), tok))
                    r = c.getresponse()
                    raw = r.read(read_limit) if read_limit else r.read()
                    status = r.status
                finally:
                    try:
                        c.close()
                    except Exception:
                        pass
            except Exception as e:
                ms = (time.time() - t0) * 1000.0
                kind = classify(e)
                # 只重试「连接没建起来 / 读超时」，且必须显式开 retries>0
                if attempt < retries and kind in (K_TIMEOUT, K_REFUSED):
                    attempt += 1
                    _log(kind, method, path, ms, status,
                         '%s: %s（第 %d 次重试）' % (type(e).__name__, e, attempt))
                    time.sleep(min(0.2 * attempt, 1.0))
                    continue
                _log(kind, method, path, ms, status, '%s: %s' % (type(e).__name__, e))
                raise
            ms = (time.time() - t0) * 1000.0
            if status == 401 and self._token_fn is not None:
                # 面板重启时 runtime.json 会被新后端的新 token 覆盖：开机约 2 s 内客户端
                # 可能仍带着上一轮 token。等文件出现新 token 再重试（预算 3 s），
                # 只有到最后仍是 401 才记日志——否则每次启动都刷一屏 401。
                new_tok = self._tok()
                if (new_tok and new_tok != tok) or (auth_retry < 12 and time.time() < auth_deadline):
                    auth_retry += 1
                    if not (new_tok and new_tok != tok):
                        time.sleep(0.25)
                    continue
            if status and status >= 400:
                _log(K_HTTP, method, path, ms, status,
                     raw[:200].decode('utf-8', 'replace'))
            return status, raw, ms

    # ---------------------------------------------------------------- 对外
    def json(self, method, path, payload=None, timeout=8, retries=None):
        """JSON 请求 → (status, 解析结果)。解析失败退回 (status, 文本)。"""
        body = json.dumps(payload) if payload is not None else None
        status, raw, _ = self._request(method, path, body, {}, timeout, retries)
        try:
            return status, json.loads(raw)
        except Exception:
            return status, raw.decode('utf-8', 'replace')

    def bytes(self, path, timeout=10, method='GET', retries=None):
        """二进制下载 → (status, bytes)。"""
        status, raw, _ = self._request(method, path, None, {}, timeout, retries)
        return status, raw

    def probe(self, path='/api/health', timeout=3.0, read_limit=65536):
        """身份校验（免鉴权 /api/health）：必须是 200 + 含 score/checks 的 JSON。

        永不抛：→ (ok, msg)。msg 文案与收口前逐一相同。
        """
        try:
            status, raw, _ = self._request('GET', path, None, {}, timeout, 0,
                                           read_limit=read_limit)
        except Exception as e:
            return False, '连接异常 %s' % type(e).__name__
        if status != 200:
            return False, 'HTTP %d' % status
        try:
            d = json.loads(raw.decode('utf-8', 'replace'))
        except Exception:
            return False, '返回的不是接口数据'
        if isinstance(d, dict) and 'checks' in d and 'score' in d:
            return True, ''
        return False, '返回的不是星虹接口'


def stream_post(url, body, headers=None, handshake_timeout=10.0, read_timeout=1.0,
                total_timeout=30.0, should_stop=None, chunk=2048, started_at=None):
    """POST 外部端点（LLM），逐块 yield 字节。

    与收口前 XxChatWorker.run 的内联实现逐条一致：
      - 握手用 handshake_timeout；进入读循环后把 socket 超时压到 read_timeout，
        这样 should_stop() 能被及时看到；
      - 读循环里的 socket.timeout 视为「这一拍没数据」，继续下一拍（不报错）；
      - should_stop() 为真 → RuntimeError('已取消')；
        累计耗时超过 total_timeout → RuntimeError('超时 %d s')。
    started_at：总时长起点（调用方从发请求前开始计，传它可保持与旧实现同一口径）。
    """
    req = urllib.request.Request(url, data=body,
                                 headers=dict(headers or {}), method='POST')
    t0 = time.time() if started_at is None else float(started_at)
    resp = urllib.request.urlopen(req, timeout=handshake_timeout)
    try:
        try:
            resp.fp.raw._sock.settimeout(read_timeout)
        except Exception:
            pass
        while True:
            if should_stop is not None and should_stop():
                raise RuntimeError('已取消')
            if total_timeout - (time.time() - t0) <= 0:
                raise RuntimeError('超时 %d s' % int(total_timeout))
            try:
                b = resp.read(chunk)
            except socket.timeout:
                continue
            if not b:
                break
            yield b
    except (GeneratorExit, KeyboardInterrupt):
        raise
    except Exception as e:
        _log(classify(e), 'POST', url, (time.time() - t0) * 1000.0, None,
             '%s: %s' % (type(e).__name__, e))
        raise
    finally:
        try:
            resp.close()
        except Exception:
            pass
