# -*- coding: utf-8 -*-
"""本地落盘唯一出口（C1-B3）：会话 JSONL / Agent 步骤 / 同意日志 / 密钥擦除。

原位置：xinghong_launcher.py
  · L392–405   _consent_log
  · L6373–6410 XX_SESSION_DIR / _XX_SECRETS / xx_secret_register / _xx_scrub /
               xx_session_write
  · L6423–6448 XX_AGENT_DIR / xx_agent_load / xx_agent_save

搬迁原则：**逐字搬**。脱敏正则（sk-/rsk-、bearer）与已登记密钥集合原样带过来，
一条都不简化——它是密钥不落盘的最后一道，改错不会报错，只会悄悄漏。
唯一的结构性改动：xx_agent_load 的合法工具名从参数传入（原模块级 XX_AGENT_NAMES），
避免 infra 反向依赖 app。

分层：infra（最底层）；只使用标准库与同层 xh_time。
"""
import json
import os
import re
import time

import xh_time

__all__ = ['XX_SESSION_DIR', 'XX_AGENT_DIR', 'xx_secret_register', 'xx_scrub',
           'xx_session_write', 'xx_agent_load', 'xx_agent_save', 'consent_log',
           'exit_log', 'startup_log']

# ── P2：小星会话落盘（本地 JSONL，绝不写 Key）─────────────────────────────
# 路径固定 %LOCALAPPDATA%\XingHongPanel\ai_sessions；字段白名单 + 已加载 Key 兜底擦除。
XX_SESSION_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local'),
    'XingHongPanel', 'ai_sessions')
_XX_SECRETS = set()

# ── P3：小星的 Agent 步骤定义 ───────────────────────────────────────────────
XX_AGENT_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local'),
    'XingHongPanel', 'ai_agents')


def xx_secret_register(v):
    """UI 每次从 SecretStore 取到 Key 后登记一次：只用于落盘前擦除，不复制到会话。"""
    _s = str(v or '')
    if len(_s) >= 6:
        _XX_SECRETS.add(_s)


def xx_scrub(v):
    _s = str(v if v is not None else '')
    for _sec in list(_XX_SECRETS):
        if _sec and _sec in _s:
            _s = _s.replace(_sec, '[REDACTED]')
    _s = re.sub(r'(sk|rsk)-[A-Za-z0-9_\-]{8,}', '[REDACTED]', _s)
    _s = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}', r'\1[REDACTED]', _s)
    return _s


def xx_session_write(role, text, meta=None):
    """追加一条会话记录。meta 只接受 model/ms；其余字段一律丢弃。"""
    _rec = {'ts': xh_time.stamp(),
            'role': str(role or 'user'), 'text': xx_scrub(text)}
    for _k in ('model', 'ms'):
        if meta and _k in meta:
            _v = meta[_k]
            _rec[_k] = _v if isinstance(_v, int) else xx_scrub(_v)
    os.makedirs(XX_SESSION_DIR, exist_ok=True)
    _f = os.path.join(XX_SESSION_DIR, xh_time.day() + '.jsonl')
    with open(_f, 'a', encoding='utf-8') as _fp:
        _fp.write(json.dumps(_rec, ensure_ascii=False) + '\n')
    return _f


def xx_agent_load(names):
    """读步骤定义；不认识的工具名直接丢弃，避免版本回退后执行旧步骤。"""
    _f = os.path.join(XX_AGENT_DIR, 'agent_steps.json')
    try:
        with open(_f, encoding='utf-8') as _fp:
            _j = json.load(_fp)
        return [{'tool': str(_s.get('tool')), 'arg': str(_s.get('arg') or '')}
                for _s in (_j.get('steps') or []) if _s.get('tool') in names]
    except Exception:
        return []


def xx_agent_save(steps):
    _f = os.path.join(XX_AGENT_DIR, 'agent_steps.json')
    os.makedirs(XX_AGENT_DIR, exist_ok=True)
    with open(_f, 'w', encoding='utf-8') as _fp:
        json.dump({'steps': [{'tool': _s.get('tool'), 'arg': _s.get('arg') or ''}
                             for _s in steps]}, _fp, ensure_ascii=False, indent=1)
    return _f


def consent_log(stored, expect, action):
    """协议同意判定留痕：每次启动追加一行，便于复现「到底弹没弹、为什么弹」。

    落 %LOCALAPPDATA%\\XingHongPanel\\consent.log（与 boot_timing.jsonl 同目录）。
    写失败静默——日志不能影响启动。
    """
    try:
        d = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'XingHongPanel')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'consent.log'), 'a', encoding='utf-8') as f:
            f.write('%s\tstored=%r\texpect=%r\taction=%s\n'
                    % (time.strftime('%Y-%m-%d %H:%M:%S'), stored, expect, action))
    except Exception:
        pass


def exit_log(*, source, t0, t1, services, forced, timeout, mode='wait', pending=False,
             result='ok'):
    """退出耗时留痕：每次退出追加一行，定位「关个软件要半天」是哪个服务拖的。

    落 %LOCALAPPDATA%\\XingHongPanel\\exit.log（与 consent.log 同目录）。
    字段：时间 / 来源 / 开始 / 结束 / 总耗时 / 四服务各自耗时（- = 退出时仍未停）/
    forced（强杀过）/ timeout（超上限）/ 之后是 R5 追加的三列：
      mode=wait|detached     退出走的是「等它停完」还是「先退、后台收尾」
      pending_after_exit=…   这一行写下时托管服务是否还没停完（detached 的退出行必为 true）
      result=…               pending / ok / forced / timeout / spawn_failed
    先写 pending 行、守护进程停完再补一行 pending=false——两行都在，能对出真实耗时。
    写失败静默。
    """
    try:
        d = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'XingHongPanel')
        os.makedirs(d, exist_ok=True)
        _t = lambda v: '-' if v is None else '%.2fs' % float(v)
        line = ('%s\tsrc=%s\tstart=%s\tend=%s\telapsed=%.2fs\tkv=%s\tlms=%s\t'
                'comfy=%s\tproxy=%s\texit_forced=%s\texit_timeout=%s\t'
                'mode=%s\tpending_after_exit=%s\tresult=%s\n'
                % (time.strftime('%Y-%m-%d %H:%M:%S'), source,
                   time.strftime('%H:%M:%S', time.localtime(t0)),
                   time.strftime('%H:%M:%S', time.localtime(t1)), t1 - t0,
                   _t(services.get('kv')), _t(services.get('lms')),
                   _t(services.get('comfy')), _t(services.get('proxy')),
                   'true' if forced else 'false', 'true' if timeout else 'false',
                   mode, 'true' if pending else 'false', result))
        with open(os.path.join(d, 'exit.log'), 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass


def startup_log(msg, **fields):
    """启动期留痕（R5 对账专用）：落 %LOCALAPPDATA%\\XingHongPanel\\startup.log。

    格式：时间 \\t 消息 \\t k=v…。与 exit.log 分开，避免退出记录被启动行淹没；
    写失败静默——日志不能影响启动。
    """
    try:
        d = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'XingHongPanel')
        os.makedirs(d, exist_ok=True)
        _kv = ' '.join('%s=%s' % (k, v) for k, v in sorted(fields.items()))
        with open(os.path.join(d, 'startup.log'), 'a', encoding='utf-8') as f:
            f.write('%s\t%s\t%s\n'
                    % (time.strftime('%Y-%m-%d %H:%M:%S'), msg, _kv))
    except Exception:
        pass
