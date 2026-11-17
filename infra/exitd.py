# -*- coding: utf-8 -*-
"""退出守护进程 + 启动对账（R5 分层退出）。

背景：`stop_all()` 在 LM Studio 常驻时实测 ~31 s。R4 让用户盯着面板等半分钟被否掉——
默认退出必须 2 s 内走完。于是拆成两段：

  · 面板进程：确认退出 → 写 exit.log 的 pending 行 → detach 拉起本模块守护进程 → 立刻退出；
  · 守护进程（`--exit-daemon <payload>`）：自己 importlib 加载 backend/panel-server.py
    调 stop_all() 的各原语，逐服务记耗时；超预算按端口/镜像名强杀；最后补一行
    pending_after_exit=false（mode=detached、真实结束时间、result=ok|forced|timeout）。

为什么不能用 HTTP `/api/stopall`：后端与面板同进程，面板一退后端就没了。守护进程只能
自己加载后端模块——各 stop 原语都有按端口/镜像名的兜底，脱离原进程仍然有效。

下次启动 `reconcile()` 读 exit_pending.json：上次没停完而托管服务还活着，就在启动日志
（startup.log）与状态页各写一行「已重新接管 / 已清理」，读完即清标记；停完了就只记
「上次退出由守护进程完成」。状态页提示由 STATE['msg'] 提供（为空则不显示）。

分层：infra；只依赖标准库 + infra.store。
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time

from infra import store

__all__ = ['STATE', 'pending_path', 'spawn', 'run', 'reconcile', 'probe_managed']

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

BUDGET = 50.0            # 秒：停服预算（守护进程自身起动 ~2 s，合计仍 <60 s 验收线）
SERVICE_KEYS = ('kv', 'lms', 'comfy', 'proxy')

# 启动对账结果（ui/pages_status.py 只读；msg 为空 = 状态页不提示）
STATE = {'done': False, 'pending': False, 'alive': [], 'action': '', 'msg': '',
         'result': '', 'end': None, 'src': ''}


def _dir():
    d = os.path.join(os.environ.get('LOCALAPPDATA') or
                     os.path.join(os.path.expanduser('~'), 'AppData', 'Local'),
                     'XingHongPanel')
    os.makedirs(d, exist_ok=True)
    return d


def pending_path():
    """上次退出留下的标记文件（既是守护进程的入参，也是下次启动的对账依据）。"""
    return os.path.join(_dir(), 'exit_pending.json')


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _prefix():
    """返回 (启动守护进程的 argv 前缀, cwd)；无法判定时 (None, None)。"""
    if getattr(sys, 'frozen', False):
        exe = os.path.abspath(sys.executable)
        return [exe], os.path.dirname(exe)
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ''
    if not script.lower().endswith('.py'):
        return None, None
    return [sys.executable, script], os.path.dirname(script)


def spawn(payload):
    """写 pending 标记并 detach 拉起守护进程；成功返回 True。

    不再等任何托管服务：面板拿到 True 就可以立刻退出。脱离 job 失败时退一级
    （只 DETACHED_PROCESS），两条都不行才算失败——调用方据此回落「等它停完」。
    """
    path = pending_path()
    try:
        _write(path, dict(payload, pending_after_exit=True, daemon_pid=None))
    except Exception:
        return False
    argv, cwd = _prefix()
    if not argv:
        return False
    kw = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    for flags in (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
                  DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP):
        try:
            p = subprocess.Popen(argv + ['--exit-daemon', path], creationflags=flags, **kw)
        except OSError:
            continue          # 0x5：当前 job 不允许 breakaway，退一级重试
        except Exception:
            return False
        try:
            _write(path, dict(payload, pending_after_exit=True, daemon_pid=p.pid))
        except Exception:
            pass
        return True
    return False


# ── 守护进程本体 ──────────────────────────────────────────────────────────

def run(path):
    """`--exit-daemon` 入口：异常不往外抛（面板已经退了），但必须留痕——
    「守护进程自己崩了」和「服务没停完」一样要能在下一次启动对账里看见。"""
    try:
        _run(path)
    except Exception:
        import traceback
        try:
            store.startup_log('exit-daemon-error',
                              err=traceback.format_exc().replace('\n', ' | ')[:400])
        except Exception:
            pass
        try:
            j = _read(path)
            j.update({'pending_after_exit': True, 'result': 'error'})
            _write(path, j)
        except Exception:
            pass


def _run(path):
    payload = _read(path)
    t0 = float(payload.get('t0') or time.time())
    src = str(payload.get('src') or 'close')
    _write(path, dict(payload, pending_after_exit=True, daemon_pid=os.getpid(),
                      daemon_started=time.time()))
    done_at = {}
    errs = []
    timed_out = False
    forced = False
    result = 'no_backend'
    final_alive = {k: True for k in SERVICE_KEYS}
    mod = _load_backend()
    if mod is not None:
        steps = (('kv', ('kv_stop',)), ('lms', ('lms_unload', 'lms_kill')),
                 ('comfy', ('cf_stop',)), ('proxy', ('stop_proxy',)))
        th = threading.Thread(target=_steps, args=(mod, steps, t0, done_at, errs),
                              daemon=True)
        th.start()
        th.join(BUDGET)
        timed_out = th.is_alive()
        final_alive = _settle(mod)
        result = 'ok'
        if timed_out or any(final_alive.values()):
            forced = True
            _force(final_alive, mod)
            final_alive = _settle(mod, 5.0)
            result = 'timeout' if timed_out else 'forced'
        for k, on in final_alive.items():
            if not on and done_at.get(k) is None:
                done_at[k] = time.time() - t0
    left = [k for k in SERVICE_KEYS if final_alive.get(k)]
    if left:
        # 强杀之后还有服务活着（例如 LM Studio 提权运行、taskkill 被拒）：
        # 不许记成「已停完」——如实标 pending，下次启动对账会说「未停完，已重新接管」。
        result = 'timeout' if timed_out else 'incomplete'
    services = {k: (None if final_alive.get(k) else done_at.get(k)) for k in SERVICE_KEYS}
    # 顺序：先落 pending 标记，再写 exit.log 完成行。exit.log 的完成行是「最终事实」，
    # 谁看到它就知道 exit_pending.json 已是终值——否则「退出→立刻重开」时，新进程刚对完账
    # 就被收尾中的守护进程覆盖标记，下次启动会漏报（R5 实测踩到）。
    try:
        _write(path, dict(payload, pending_after_exit=bool(left), end=time.time(),
                          result=result, forced=forced, timeout=timed_out,
                          left=left, errors=errs[:8]))
    except Exception:
        pass
    store.exit_log(source=src, t0=t0, t1=time.time(), services=services,
                   forced=forced, timeout=timed_out, mode='detached',
                   pending=bool(left), result=result)


def _steps(mod, steps, t0, out, errs):
    for key, fns in steps:
        for fn in fns:
            try:
                getattr(mod, fn)()
            except Exception as e:
                errs.append('%s:%r' % (fn, e))
        out[key] = time.time() - t0


def _alive(mod):
    out = {}
    for key, fn in (('kv', 'kv_alive'), ('lms', 'lms_running'),
                    ('comfy', 'cf_alive'), ('proxy', 'proxy_running')):
        try:
            out[key] = bool(getattr(mod, fn)())
        except Exception:
            out[key] = True   # 判定不了就按「还活着」处理，宁可多杀一刀也不谎报已停
    return out


def _settle(mod, seconds=3.0):
    """进程不是一 taskkill 就消失（端口/句柄要时间释放），给一小段收尾窗口再判定。

    不这么做会把「正在死」误报成「没停完」，下次启动就白提示一次「未停完」。
    """
    end = time.time() + seconds
    alive = _alive(mod)
    while any(alive.values()) and time.time() < end:
        time.sleep(0.2)
        alive = _alive(mod)
    return alive


def _load_backend():
    """找到并加载 backend/panel-server.py（模块级只读配置，不绑端口、不起线程）。"""
    cands = []
    if getattr(sys, 'frozen', False):
        cands.append(os.path.join(getattr(sys, '_MEIPASS', ''), 'backend'))
        cands.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cands.append(os.path.join(here, 'backend'))
        cands.append(here)
    for d in cands:
        p = os.path.join(d, 'panel-server.py')
        if os.path.exists(p):
            return _import_backend(p)
    return None


def _import_backend(path):
    """加载后端。后端模块级 `_init_token()` 会重写 runtime.json，而面板此刻可能还在
    发最后几个请求（读了我们写的 token 就会被 401）——所以导入期间把 token 文件指到
    私有临时路径，真正的 runtime.json 一个字节都不碰（R5：不再写-再还原，没有竞态窗口）。"""
    prev = os.environ.get('XH_TOKEN_FILE')
    tmp = os.path.join(_dir(), '_exit_daemon_token.json')
    os.environ['XH_TOKEN_FILE'] = tmp
    try:
        spec = importlib.util.spec_from_file_location('xh_exit_backend', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if prev is None:
            os.environ.pop('XH_TOKEN_FILE', None)
        else:
            os.environ['XH_TOKEN_FILE'] = prev
        try:
            os.remove(tmp)
        except OSError:
            pass
    return mod


def _kill_image(*names):
    for n in names:
        try:
            subprocess.run(['taskkill', '/F', '/IM', n], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass


def _kill_pid(pid):
    if not pid:
        return
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=20, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def _port_of(mod, name, fallback):
    try:
        return int(getattr(mod, name))
    except Exception:
        return fallback


def _force(alive, mod):
    """最后一刀：按镜像名/端口杀，不再问后端。"""
    if alive.get('kv'):
        _kill_image('llama-kvmem-server.exe')
    if alive.get('lms'):
        _kill_image('LM Studio.exe', 'llama-server.exe')
    if alive.get('comfy'):
        _kill_pid(_pid_on_port(mod, _port_of(mod, 'COMFY_PORT', 8189)))
    if alive.get('proxy'):
        _kill_pid(_pid_on_port(mod, _port_of(mod, 'PROXY_PORT', 1235)))
    time.sleep(0.6)


def _pid_on_port(mod, port):
    try:
        return int(getattr(mod, 'pid_on_port')(port) or 0)
    except Exception:
        return 0


# ── 下次启动的对账 ────────────────────────────────────────────────────────

def _port_up(port, timeout=0.03):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _proc_names():
    """一次 Toolhelp32 快照拿全部进程名（小写集合）。启动对账要快，不拉 tasklist 子进程。"""
    import ctypes
    import ctypes.wintypes as wt
    TH32CS_SNAPPROCESS = 0x00000002

    class PE(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD),
                    ('th32ProcessID', wt.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                    ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', wt.LONG),
                    ('dwFlags', wt.DWORD), ('szExeFile', wt.WCHAR * 260)]

    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not h or h == ctypes.c_void_p(-1).value:
        return set()
    pe = PE()
    pe.dwSize = ctypes.sizeof(PE)
    out = set()
    try:
        ok = k.Process32FirstW(h, ctypes.byref(pe))
        while ok:
            out.add(str(pe.szExeFile).lower())
            ok = k.Process32NextW(h, ctypes.byref(pe))
    finally:
        k.CloseHandle(h)
    return out


def probe_managed():
    """不加载后端，只用进程名 + 端口判断四个托管服务是否还在（对账用）。"""
    procs = _proc_names()
    out = []
    if 'llama-kvmem-server.exe' in procs:
        out.append('KVMem')
    if 'lm studio.exe' in procs:
        out.append('LM Studio')
    if _port_up(8189):
        out.append('ComfyUI')
    if _port_up(1235):
        out.append('保活代理')
    return out


def reconcile():
    """启动时对账：读上次退出标记 → 写一行启动日志 → 状态页提示 → 清标记。"""
    path = pending_path()
    if not os.path.isfile(path):
        STATE['done'] = True
        return dict(STATE)
    j = _read(path)
    pend = bool(j.get('pending_after_exit'))
    alive = probe_managed()
    if pend:
        if alive:
            action, msg = 'takeover', '上次退出时托管服务未停完，已重新接管（%s）' % '、'.join(alive)
        else:
            action, msg = 'cleaned', '上次退出时托管服务未停完，已清理'
    else:
        action, msg = 'done', ''
    STATE.update({'done': True, 'pending': pend, 'alive': alive, 'action': action,
                  'msg': msg, 'result': str(j.get('result') or ''),
                  'end': j.get('end'), 'src': str(j.get('src') or '')})
    store.startup_log('exit-reconcile', pending=('true' if pend else 'false'),
                      alive=('|'.join(alive) or '-'), action=action,
                      result=(STATE['result'] or '-'), src=(STATE['src'] or '-'))
    try:
        os.remove(path)
    except Exception:
        pass
    return dict(STATE)
