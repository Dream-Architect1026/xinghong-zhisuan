# 星虹智算验收 harness：10 轮连续全量跑，只打本产品自己的面板端口。
# 每项检查都打真实本地服务，只有 HTTP 200 + 结构 sanity 才算一轮通过，需连续 10 轮。
# 结果以 JSON 打到 stdout（调用方重定向到文件）。
# 本套件不探测其它产品线，端口常量写死为星虹自己的值。
import http.client
import json
import os
import time

ROUNDS = 10

# 星虹面板后端监听端口（与 backend/panel-server.py 的 PANEL_PORT 一致）。
XH_PORT = 8899

# X1 本地 token 鉴权：除 /api/health 外的接口都要求 X-Local-Token。
# 运行态文件在 %LOCALAPPDATA%\<产品目录>\runtime.json。
_TOKEN_FILES = {XH_PORT: 'XingHongPanel'}


def _token(port):
    d = _TOKEN_FILES.get(port)
    if not d:
        return ''
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        with open(os.path.join(base, d, 'runtime.json'), encoding='utf-8') as f:
            return str(json.load(f).get('token') or '')
    except Exception:
        return ''


def call(port, method, path, payload=None, timeout=10, with_token=True):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    body = json.dumps(payload) if payload is not None else None
    headers = {'Content-Type': 'application/json'} if body else {}
    tok = _token(port) if with_token else ''
    if tok:
        headers['X-Local-Token'] = tok
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, {}


CHECKS = [
    ('star', XH_PORT, 'S1 健康检查', 'GET', '/api/health', None,
     lambda c, d: c == 200),
    ('star', XH_PORT, 'S2 状态总览', 'GET', '/api/state', None,
     lambda c, d: c == 200 and 'profiles' in d),
    ('star', XH_PORT, 'S3 GPU 指标', 'GET', '/api/metrics', None,
     lambda c, d: c == 200 and 'sys' in d),
    ('star', XH_PORT, 'S4 作品列表', 'GET', '/api/outputs?limit=20', None,
     lambda c, d: c == 200 and 'outputs' in d),
    ('star', XH_PORT, 'S5 能耗数据', 'GET', '/api/energy', None,
     lambda c, d: c == 200),
    ('star', XH_PORT, 'S6 释放显存', 'POST', '/api/comfy/free', {},
     lambda c, d: c == 200),
    ('star', XH_PORT, 'S7 停止KV(幂等)', 'POST', '/api/kv/stop', {},
     lambda c, d: c == 200),
    ('star', XH_PORT, 'S8 设置读取', 'POST', '/api/settings/get', {},
     lambda c, d: c == 200),
]

# 闸门自检（R38-26）：除 /api/health 外挑一个受保护接口，不带 token 打一次。
# 只接受 200（闸门未启用）或 401（闸门启用）两类明确回答；404 / 连接失败 /
# 其它状态一律判「闸门未就绪」并退出非 0 —— 防止下次静默失效又被当成全绿。
PROTECTED = {XH_PORT: '/api/state'}


def preflight():
    bad = []
    for port, path in PROTECTED.items():
        try:
            code, _ = call(port, 'GET', path, with_token=False)
        except Exception as e:
            bad.append('%d %s -> %s' % (port, path, type(e).__name__))
            continue
        if code not in (200, 401):
            bad.append('%d %s -> HTTP %d' % (port, path, code))
    if bad:
        print('GATE_NOT_READY ' + '; '.join(bad))
        return False
    print('GATE_OK ' + ', '.join('%d %s' % (p, q) for p, q in sorted(PROTECTED.items())))
    return True


if not preflight():
    raise SystemExit(2)

streaks = {name: 0 for _, _, name, _, _, _, _ in CHECKS}
fails = {name: [] for _, _, name, _, _, _, _ in CHECKS}
start = time.time()
for rnd in range(1, ROUNDS + 1):
    for app, port, name, method, path, payload, okfn in CHECKS:
        try:
            code, d = call(port, method, path, payload)
            ok = okfn(code, d)
        except Exception as e:
            ok = False
            d = str(e)
        if ok:
            streaks[name] += 1
        else:
            streaks[name] = 0
            fails[name].append('round%d code=%s' % (rnd, code))
    print('round %d/%d done' % (rnd, ROUNDS), flush=True)
elapsed = round(time.time() - start, 1)

all_pass = all(v >= ROUNDS for v in streaks.values())
for app, port, name, *_ in CHECKS:
    print('%-18s %d/10 %s' % (name, streaks[name], 'OK' if streaks[name] >= ROUNDS else 'FAIL ' + ';'.join(fails[name][:3])))
print('VERDICT', 'ALL_PASS' if all_pass else 'HAS_FAILURES')
print('RESULTS_JSON', json.dumps({'rounds': ROUNDS, 'elapsed_sec': elapsed,
      'streaks': streaks, 'fails': fails,
      'verdict': 'ALL_PASS' if all_pass else 'HAS_FAILURES'}, ensure_ascii=False))
