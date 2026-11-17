# 本地推理统一控制台后端（仅标准库）。
# 一个页面管理：KVMem 路线（1234）、LM Studio 路线（1234，与 KVMem 互斥）、ComfyUI 生图（8189，串行）。
import json, os, socket, subprocess, sys, threading, time, webbrowser, ctypes, shutil
import secrets
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import urllib.parse

PANEL_PORT = 8899
MODEL_PORT = 1234
# R10（性能 A/B）：与前端共用 XH_R10 环境变量；后端默认开 loopslow，
# 显式传 plain 可关掉复现基线，传 loopslow 单独开。
_XH_R10_ENV = set(x.strip() for x in (os.environ.get('XH_R10') or '').split(',') if x.strip())
XH_R10 = set() if 'plain' in _XH_R10_ENV else (_XH_R10_ENV or {'loopslow'})
# R11（XH-071）：后端巡检再降频（LM Studio 30→120 s、任务台账回填 90→300 s）。
# 数据新鲜度由三点兜底：① /api/state 的 lms.running 每拍现算；② 面板内加载/卸载模型
# 会 set(LMS_REFRESH) 立刻刷新；③ 作品墙走 /api/outputs 实时扫目录，不等回填。
# XH_R11=plain 可关掉本项，复现改前 30/90 s。
_XH_R11_ENV = set(x.strip() for x in (os.environ.get('XH_R11') or '').split(',') if x.strip())
XH_R11 = set() if 'plain' in _XH_R11_ENV else (_XH_R11_ENV or {'slow3'})
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
# 本机路径配置：仓库内不含开发机绝对路径，取值见 pathcfg.py
# （环境变量 XH_<KEY> > %LOCALAPPDATA%\XingHongPanel\paths.json > 中性默认值）。
if BASE not in sys.path:
    sys.path.insert(0, BASE)
try:
    import pathcfg as _pathcfg
except Exception:
    _pathcfg = None


def _cfg(key, default=''):
    try:
        return _pathcfg.get(key, default) if _pathcfg else default
    except Exception:
        return default

# X1：本地 token 鉴权。启动时生成一次性 token，落 %LOCALAPPDATA%\XingHongPanel\runtime.json；
# 客户端从同文件读取并附加 X-Local-Token 头。
TOKEN_FILE = os.environ.get('XH_TOKEN_FILE') or os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
    'XingHongPanel', 'runtime.json')


def _init_token():
    tok = secrets.token_urlsafe(24)
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
            json.dump({'token': tok}, f, ensure_ascii=False)
    except Exception:
        pass
    return tok


LOCAL_TOKEN = _init_token()


def _has_token(h):
    """X1：校验 X-Local-Token 头（constant-time 比较）；
    回环同源请求（Sec-Fetch-Site=same-origin / 本机 Origin）放行。
    R15-A02：query token 通道已移除（易落入代理/日志）。"""
    if not LOCAL_TOKEN:
        # R2-XH06：token 不可用（目录只写保护等）时拒绝而非放行（防鉴权静默失效）
        return False
    htok = h.headers.get('X-Local-Token')
    if htok is not None and secrets.compare_digest(str(htok), LOCAL_TOKEN):
        return True
    sfs = h.headers.get('Sec-Fetch-Site', '') or ''
    if sfs.lower() == 'same-origin':
        return True
    origin = h.headers.get('Origin', '') or ''
    if origin in ('http://127.0.0.1:%d' % PANEL_PORT,
                  'http://localhost:%d' % PANEL_PORT):
        return True
    return False

def _app_root():
    """frozen 时数据/日志写到 %LOCALAPPDATA%\\XingHongPanel，源码运行写到工程根。"""
    if getattr(sys, 'frozen', False):
        root = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                            'XingHongPanel')
    else:
        root = BASE
    os.makedirs(root, exist_ok=True)
    return root
_APP_ROOT = _app_root()
LOGDIR = os.path.join(_APP_ROOT, 'logs')
CONFIG = os.path.join(HERE, 'web-profiles.json')
os.makedirs(LOGDIR, exist_ok=True)

KV_BIN = _cfg('kvmem_bin')
MODELS = _cfg('models_dir')
MODELS_FLASH = _cfg('models_flash_dir', MODELS)
LMS = _cfg('lms_exe')

COMFY_ROOT = _cfg('comfy_root')
COMFY_PY = os.path.join(COMFY_ROOT, r'.venv\Scripts\python.exe') if COMFY_ROOT else ''
COMFY_MAIN = os.path.join(COMFY_ROOT, 'main.py') if COMFY_ROOT else ''
COMFY_PORT = 8189
COMFY_STORE = _cfg('comfy_store', os.path.join(_APP_ROOT, 'ComfyUI_Storage'))
COMFY_MODELS = os.path.join(COMFY_STORE, 'models')
COMFY_OUT = os.path.join(COMFY_STORE, 'output')
COMFY_PATHS = os.path.join(HERE, 'comfy-paths.yaml')
COMFY_DB = os.path.join(LOGDIR, 'comfy.db')
DATA = os.path.join(_APP_ROOT, 'data')
os.makedirs(DATA, exist_ok=True)
METRICS_JL = os.path.join(DATA, 'metrics.jsonl')
JOBS_JL = os.path.join(DATA, 'jobs.jsonl')
ENERGY_JL = os.path.join(DATA, 'energy.jsonl')
# 即使永久模式也有硬上限，防止无界增长（@2s 约 4.6 天）；能量明细保留更久（@2s 约 30 天）
METRICS_HARD_CAP = 200000
ENERGY_HARD_CAP = 1300000
SETTINGS_F = os.path.join(DATA, 'settings.json')
os.makedirs(COMFY_OUT, exist_ok=True)

# 面板设置（采样间隔/保留时长）
DEFAULTS_SET = {'sample_interval': 2, 'retain_hours': 24, 'permanent': True}
def load_settings():
    try:
        s = json.load(open(SETTINGS_F, encoding='utf-8'))
        return {**DEFAULTS_SET, **s}
    except Exception:
        return dict(DEFAULTS_SET)
def save_settings(s):
    global PANEL_SET
    # X7：限定三字段 schema，非法 400 不落盘；未知键丢弃
    if not isinstance(s, dict):
        return dict(ok=False, error='body must be JSON object')
    out = {}
    if 'sample_interval' in s:
        v = s['sample_interval']
        if not isinstance(v, (int, float)) or isinstance(v, bool) \
                or not (1 <= float(v) <= 3600):
            return dict(ok=False, error='sample_interval must be 1-3600')
        # R15-X03：非整数直接 400（原 int() 截断落盘，采样被悄然改粗）
        if not float(v).is_integer():
            return dict(ok=False, error='sample_interval must be integer')
        out['sample_interval'] = int(v)
    if 'retain_hours' in s:
        v = s['retain_hours']
        if not isinstance(v, (int, float)) or isinstance(v, bool) \
                or not (1 <= float(v) <= 8760):
            return dict(ok=False, error='retain_hours must be 1-8760')
        if not float(v).is_integer():
            return dict(ok=False, error='retain_hours must be integer')
        out['retain_hours'] = int(v)
    if 'permanent' in s:
        if not isinstance(s['permanent'], bool):
            return dict(ok=False, error='permanent must be boolean')
        out['permanent'] = s['permanent']
    if not out:
        return dict(ok=False, error='no valid settings fields')
    cur = load_settings(); cur.update(out)
    # R2-XH05：tmp + os.replace 原子写（中断不留半截 JSON 致配置静默重置）
    tmp = SETTINGS_F + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_F)
    PANEL_SET = cur
    return cur
PANEL_SET = load_settings()

KV_OPTS = ['f16', 'q8_0', 'q5_0', 'q5_1', 'q4_0', 'q4_1']
NO_WIN = 0x08000000
# 隐藏 GUI 孙进程窗口（STARTUPINFO SW_HIDE），与 CREATE_NO_WINDOW 双保险（R009 A02：
# 引擎 exe 再派生的 OpenCL/Vulkan worker 等不继承 CREATE_NO_WINDOW，需 STARTUPINFO 隐藏）
HIDDEN_SI = None
if os.name == 'nt':
    HIDDEN_SI = subprocess.STARTUPINFO()
    HIDDEN_SI.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    HIDDEN_SI.wShowWindow = subprocess.SW_HIDE

# ============================ KVMem 保活代理（1235 → 1234） ============================
# 2026-09-25：llama-kvmem-server 是单 slot 服务端，前缀缓存会在空闲 ~30s 后被服务端
# 自行清空（无参数可关），导致 DSH 每条消息都全量重算 50K+ tokens（~30s 延迟）。
# 代理在 DSH 空闲时用 max_tokens=1 重放最近一次主会话请求（服务端 0 新 token，几十 ms），
# 让缓存常驻。实测：45s 空闲后续问 cache_n=3507 / 仅重算 35 tokens / 189ms。
# 生命周期：随模型启动（kv_start / lms_start）先拉起；随模型停止（kv_stop /
# lms_stop / lms_kill / stop_all）同步退出。孤儿自愈由代理自带：上游 1234 连续
# 10 分钟不可达时自动退出，面板下次启动模型时会重新拉起。
PROXY_SCRIPT = _cfg('kvmem_proxy')
PROXY_PORT = 1235
PROXY_LOG = os.path.join(LOGDIR, 'proxy_out.log')
PROXY_ERR = os.path.join(LOGDIR, 'proxy_err.log')

def _node_exe():
    # 优先用本机配置的 node（paths.json / XH_NODE_EXE），其次 PATH 里的 node.exe。
    for cand in (_cfg('node_exe'), 'node.exe'):
        if not cand:
            continue
        try:
            rc = subprocess.run([cand, '--version'], capture_output=True, timeout=10,
                                creationflags=NO_WIN)
            if rc.returncode == 0:
                return cand
        except Exception:
            continue
    return None

def proxy_running():
    return port_listening(PROXY_PORT)

def ensure_proxy():
    """模型启动路径上调用：代理未运行则先拉起（DSH 的 baseURL 指向 1235）。"""
    if proxy_running():
        return True
    node = _node_exe()
    if not node or not os.path.exists(PROXY_SCRIPT):
        plog('[保活代理] 启动失败：node 或脚本缺失 (node=%s, script=%s)' % (node, PROXY_SCRIPT))
        return False
    try:
        so = open(PROXY_LOG, 'ab')
        se = open(PROXY_ERR, 'ab')
        subprocess.Popen([node, PROXY_SCRIPT, str(PROXY_PORT), str(MODEL_PORT)],
                         stdout=so, stderr=se, creationflags=NO_WIN, close_fds=True,
                         startupinfo=HIDDEN_SI)
        for _ in range(20):
            time.sleep(0.25)
            if proxy_running():
                plog('[保活代理] 已启动 %d → %d' % (PROXY_PORT, MODEL_PORT))
                return True
        plog('[保活代理] 启动后 5s 未监听，见 logs\\proxy_err.log')
        return False
    except Exception as e:
        plog('[保活代理] 启动异常：%r' % e)
        return False

def stop_proxy():
    """模型停止路径上调用：同步退出代理（taskkill 杀掉监听 1235 的进程树）。"""
    pid = pid_on_port(PROXY_PORT)
    if pid:
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                           capture_output=True, timeout=15, creationflags=NO_WIN)
            plog('[保活代理] 已停止 (pid=%s)' % pid)
        except Exception:
            pass

# ============================ 自适应 KV 精度 ============================
# 「规模 256」在本项目的实际含义 = **序列长度 256K（ctx = 262144）**，不是并发数：
#   本面板拉起 KVMem 时是单实例单序列，命令行里没有 -np/--parallel/slots，
#   唯一的规模量就是 -c/ctx，所以规模判据取 prof['ctx']。
#
# 【放在哪个阶段】→ **服务启动初始化阶段**（组装子进程命令行时，见 _kv_start_bg）。
#   原因：llama.cpp 的 -ctk/-ctv 是**进程启动参数**，KV 张量类型在模型加载后不可变更；
#   放到请求处理阶段改，等价于「每次请求重启服务」，必然打断服务 → 与需求冲突。
#   而「规模」在启动时已由档案的 ctx 确定，启动阶段就能拿到，不需要等请求。
#
# 【显存影响】KV 精度直接决定 --kvmem-budget 工作集的**字节数**：
#   q4_0→q8_0 涨 2×，→f16 涨 4×。本机 8GB 显存下 budget=12288 在 q4_0 已属临界
#   （实测 83K 冷 prefill 偶发过一次 CUDA OOM），所以默认开 compensate_budget：
#   提精度时把 budget 按字节数等比缩小，使 GPU 侧 KV 字节量保持恒定 → 不引入新的 OOM 风险。
# 【吞吐影响】KV 越精确，每 token 需读的字节越多 → decode 变慢（q8_0 约 −8~15%、
#   f16 约 −20~35%，长上下文带宽受限时更明显）；prefill 影响较小。
#   compensate_budget 缩小工作集 token 数后，长上下文可能多一次分页，再小幅降速。
ADAPTIVE_F = os.path.join(HERE, 'kv-adaptive.json')
ADAPTIVE_DEFAULTS = {
    # 2026-09-25 用户要求：**默认关闭**，维持 256K + 当前精度（q4_0 / budget 12288）不变。
    # 需要重新启用时，把 kv-adaptive.json 的 enabled 改成 true 即可（此处默认值仅用于兜底）。
    'enabled': False,
    'scale_trigger': 262144,            # 规模阈值：256K 序列长度
    'ram_threshold_gb': 8.0,            # 空闲物理内存阈值（GB），低于此值不切换
    'tiers': ['q4_0', 'q8_0', 'f16'],   # 精度阶梯（低→高），取值须落在 KV_OPTS 内
    'steps': 1,                         # 一次提升几档
    'compensate_budget': True,          # 等比缩 budget，保持显存字节量不变
    'min_budget': 1024,                 # budget 下限，避免缩到不可用
}
KV_BYTES = {'f16': 2.0, 'bf16': 2.0, 'q8_0': 1.0, 'q5_1': 0.75, 'q5_0': 0.75,
            'q4_1': 0.5, 'q4_0': 0.5}   # 每元素字节数，用于 budget 换算
PLOG_F = os.path.join(LOGDIR, 'panel.log')

def adaptive_cfg():
    """默认值 + kv-adaptive.json 覆盖；文件缺失/损坏一律回落默认，绝不抛。"""
    cfg = dict(ADAPTIVE_DEFAULTS)
    try:
        with open(ADAPTIVE_F, encoding='utf-8') as f:
            u = json.load(f)
        if isinstance(u, dict):
            cfg.update(u)
    except Exception:
        pass
    return cfg

def plog(msg):
    """面板侧日志：落 LOGDIR/panel.log 并同步到 stdout。"""
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        with open(PLOG_F, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass

LAST_ADAPT = {}

def kvmem_adaptive_precision(prof):
    """服务启动前决定 KV 量化档位。

    规则：规模 ≥ scale_trigger(256K) 且 空闲物理内存 > ram_threshold_gb(8GB)
          → KV 精度沿 tiers 升 steps 档（并按 compensate_budget 等比缩 budget）；
          否则**保持档案原精度**（安全回退）。

    返回 dict(ctk, ctv, budget, changed, reason[, free_gb, scale])。
    本函数**不抛异常**：任何意外都回退到档案原值，保证自适应逻辑不会导致启动失败。
    """
    fb = dict(ctk=prof.get('ctk'), ctv=prof.get('ctv'),
              budget=prof.get('budget'), changed=False, reason='disabled')
    try:
        cfg = adaptive_cfg()
        if not cfg.get('enabled', True):
            return fb
        scale = int(prof.get('ctx') or 0)
        if scale < int(cfg.get('scale_trigger') or 0):
            fb['reason'] = 'below-trigger'
            return fb
        ctk = prof.get('ctk'); ctv = prof.get('ctv') or ctk
        tiers = [t for t in (cfg.get('tiers') or []) if t in KV_OPTS]
        if ctk not in tiers or ctv not in tiers:
            fb['reason'] = 'tier-unlisted'
            return fb
        step = max(1, int(cfg.get('steps') or 1))
        nk = tiers[min(len(tiers) - 1, tiers.index(ctk) + step)]
        nv = tiers[min(len(tiers) - 1, tiers.index(ctv) + step)]
        if (nk, nv) == (ctk, ctv):
            fb['reason'] = 'already-top'
            return fb
        free_gb = ram_info()['avail'] / 1e9          # 空闲物理内存（GB，十进制）
        thr = float(cfg.get('ram_threshold_gb') or 0)
        if free_gb <= thr:
            plog('[KV自适应精度] 规模=%d(序列长度) 空闲内存=%.2fGB ≤ 阈值%.1fGB '
                 '→ 保持 %s/%s（内存不足，安全回退，不切换）'
                 % (scale, free_gb, thr, ctk, ctv))
            fb.update(reason='low-ram', free_gb=round(free_gb, 2), scale=scale)
            return fb
        budget = int(prof.get('budget') or 0)
        comp = cfg.get('compensate_budget', True)
        if comp and budget > 0:
            r_old = KV_BYTES.get(ctk, 1.0); r_new = KV_BYTES.get(nk, 1.0)
            nb = int(budget * r_old / r_new)
            block = int(prof.get('block') or 0) or 1
            nb = max(int(cfg.get('min_budget') or 1024), nb // block * block)
            budget = max(block, nb)
        plog('[KV自适应精度] 规模=%d(序列长度) 空闲内存=%.2fGB > 阈值%.1fGB '
             '→ KV 精度 %s/%s ⇒ %s/%s（已切换）；budget %s ⇒ %s（%s）'
             % (scale, free_gb, thr, ctk, ctv, nk, nv, prof.get('budget'), budget,
                '等比缩，显存字节量不变' if comp else '未补偿，显存将上涨'))
        return dict(ctk=nk, ctv=nv, budget=budget, changed=True, reason='promoted',
                    free_gb=round(free_gb, 2), scale=scale)
    except Exception as e:
        plog('[KV自适应精度] 判定异常 %r → 保持档案原精度 %s/%s'
             % (e, prof.get('ctk'), prof.get('ctv')))
        return fb


# Qwen-Image 2.1（8GB 已验证组合）
QI = [
 ('diffusion_models', 'qwen_image_2.1_int8_convrot.safetensors'),
 ('text_encoders', 'qwen3vl_8b_w4a8.safetensors'),
 ('vae', 'qwen_image_2.1_vae_bf16.safetensors'),
]

# Qwen-Image 2.1 Uncensored（GGUF Q4_K_M 无审查扩散模型，专项研究；TE 改用 w4a8 版——
# 原配 int8_convrot 文本编码器本机文件已损坏（数据错位、配置张量被权重占据），w4a8 同为
# qwen3vl-8B、可驻 GPU 且编码更快；VAE 复用）
QI_UC = [
 ('diffusion_models', 'qwen-image-2.1-UC-Q4_K_M.gguf'),
 ('text_encoders', 'qwen3vl_8b_w4a8.safetensors'),
 ('vae', 'qwen_image_2.1_vae_bf16.safetensors'),
]

# MiniMax H3 Singularity（无审查版 FL2VA，文生/图生通用，全模态音视频）
H3_FILES = [
 ('diffusion_models', 'Minimax-H3-Singularity-Q3_K_M.gguf'),
 ('text_encoders', 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'),
 ('vae', 'minimax_h3_video_vae_int8_convrot.safetensors'),
 ('vae', 'minimax_h3_audio_vae_fp32.safetensors'),
 ('loras', 'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors'),
]

H3_W, H3_H = 768, 768   # 本机 8GB 首选；官方训练 1344x768，5s=124帧

# ============================ 系统监控 ============================
class FILETIME(ctypes.Structure):
    _fields_ = [('lo', wintypes.DWORD), ('hi', wintypes.DWORD)]

class MEMEX(ctypes.Structure):
    _fields_ = [('len', wintypes.DWORD), ('load', wintypes.DWORD),
                ('tot', ctypes.c_ulonglong), ('avail', ctypes.c_ulonglong),
                ('totPF', ctypes.c_ulonglong), ('availPF', ctypes.c_ulonglong),
                ('totV', ctypes.c_ulonglong), ('availV', ctypes.c_ulonglong),
                ('availExt', ctypes.c_ulonglong)]

def fti(ft): return ft.hi * 2**32 + ft.lo

def sys_times():
    i, k, u = FILETIME(), FILETIME(), FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    return fti(i), fti(k), fti(u)

def ram_info():
    m = MEMEX(); m.len = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return dict(total=m.tot, avail=m.avail)

GPU_Q = ('memory.total,memory.used,utilization.gpu,utilization.memory,temperature.gpu,'
         'power.draw,power.limit,clocks.sm,clocks.mem')

# R02（25 轮）：空闲 CPU 热点定位——原实现每 2 s 调一次 nvidia-smi 子进程，
# 单次约 0.2–0.4 s CPU，按 2 s 周期折算约占单核 10%。改走驱动自带的 NVML
# （ctypes 调用，微秒级），数值口径不变（显存 MiB / 功率 W / 频率 MHz）；
# NVML 不可用时回退 nvidia-smi，并加 6 s 缓存避免同周期重复拉起子进程。
_GPU_CACHE = {'t': 0.0, 'v': None}
_NV = {'tried': False, 'ok': False, 'lib': None, 'handle': None}


class _NVUtil(ctypes.Structure):
    _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]


class _NVMem(ctypes.Structure):
    _fields_ = [('total', ctypes.c_uint64), ('free', ctypes.c_uint64),
                ('used', ctypes.c_uint64)]


def _nvml():
    if _NV['tried']:
        return _NV['ok']
    _NV['tried'] = True
    try:
        lib = ctypes.WinDLL('nvml.dll')
        lib.nvmlInit_v2()
        lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [
            ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
        lib.nvmlDeviceGetUtilizationRates.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_NVUtil)]
        lib.nvmlDeviceGetMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_NVMem)]
        lib.nvmlDeviceGetTemperature.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerUsage.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetEnforcedPowerLimit.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetClockInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        for _fn in ('nvmlDeviceGetUtilizationRates', 'nvmlDeviceGetMemoryInfo',
                    'nvmlDeviceGetTemperature', 'nvmlDeviceGetPowerUsage',
                    'nvmlDeviceGetEnforcedPowerLimit', 'nvmlDeviceGetClockInfo'):
            getattr(lib, _fn).restype = ctypes.c_int
        h = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h)) != 0:
            return False
        _NV['lib'] = lib
        _NV['handle'] = h
        _NV['ok'] = True
    except Exception:
        _NV['ok'] = False
    return _NV['ok']


def _gpu_nvml():
    lib, h = _NV['lib'], _NV['handle']
    u = _NVUtil()
    lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u))
    m = _NVMem()
    lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m))
    t = ctypes.c_uint()
    lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t))
    pw = ctypes.c_uint()
    lib.nvmlDeviceGetPowerUsage(h, ctypes.byref(pw))
    lim = ctypes.c_uint()
    lib.nvmlDeviceGetEnforcedPowerLimit(h, ctypes.byref(lim))
    sm = ctypes.c_uint()
    lib.nvmlDeviceGetClockInfo(h, 1, ctypes.byref(sm))   # NVML_CLOCK_SM
    cm = ctypes.c_uint()
    lib.nvmlDeviceGetClockInfo(h, 2, ctypes.byref(cm))   # NVML_CLOCK_MEM
    # R33（量纲审计 BUG-A）：nvmlDeviceGetPowerUsage 偶发以 µW 返回（同一次调用
    # powerLimit 仍为 mW）。仅当量程已知且读数 > 量程×3 时按 µW 纠正；量程缺失
    # 不猜，归一后仍超出物理区间（>1000 W）一律判未知（None），不写错数。
    lim_w = lim.value / 1000.0 if lim.value else 0.0
    pw_w = pw.value / 1000.0
    if lim_w > 20.0 and pw_w > lim_w * 3.0:
        pw_w = pw.value / 1000000.0
    power_w = round(pw_w, 2) if (0.0 <= pw_w <= 1000.0) else None
    # BUG-C：clkSm 越界（如 65535）不可由整数倍换算恢复，按物理区间判无效。
    clk_sm = sm.value if 0 <= sm.value <= 5000 else None
    clk_mem = cm.value if 0 <= cm.value <= 30000 else None
    return dict(memTotal=round(m.total / 1048576.0),
                memUsed=round(m.used / 1048576.0),
                gpuUtil=u.gpu, memUtil=u.memory, temp=t.value,
                power=power_w,
                powerLimit=round(lim.value / 1000.0, 2),
                clkSm=clk_sm, clkMem=clk_mem)


def gpu_info():
    if _nvml():
        try:
            return _gpu_nvml()
        except Exception:
            pass
    now = time.time()
    if _GPU_CACHE['v'] is not None and now - _GPU_CACHE['t'] < 6:
        return _GPU_CACHE['v']
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=' + GPU_Q,
                              '--format=csv,noheader,nounits'], capture_output=True,
                             text=True, timeout=8, creationflags=NO_WIN).stdout.strip().split(',')
        def num(i, d=0.0):
            try: return float(out[i].strip())
            except Exception: return d
        _pw, _sm, _cm = num(5), num(7), num(8)
        v = dict(memTotal=num(0), memUsed=num(1), gpuUtil=num(2), memUtil=num(3),
                 temp=num(4),
                 power=_pw if 0.0 <= _pw <= 1000.0 else None,
                 powerLimit=num(6),
                 clkSm=_sm if 0.0 <= _sm <= 5000 else None,
                 clkMem=_cm if 0.0 <= _cm <= 30000 else None)
        _GPU_CACHE.update(t=now, v=v)
        return v
    except Exception:
        if _GPU_CACHE['v'] is not None:
            return _GPU_CACHE['v']
        return dict(memTotal=8151, memUsed=0, gpuUtil=0, memUtil=0, temp=0,
                    power=0, powerLimit=0, clkSm=0, clkMem=0)

SYS = {}
HIST = []
HIST_LOCK = threading.Lock()
T0_WALL = time.time()

def jl_append(path, obj):
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(obj, ensure_ascii=False) + '\n')
    except Exception:
        pass

def jl_read_recent(path, n=200):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2); size = f.tell(); block = min(size, n*400)
            f.seek(size-block)
            lines = f.read().decode('utf-8','replace').splitlines()
            out=[]
            for ln in lines:
                try: out.append(json.loads(ln))
                except Exception: pass
            return out[-n:]
    except Exception:
        return []

def trim_jl(path, max_lines):
    try:
        lines = open(path, 'rb').read().splitlines()
        if len(lines) > max_lines:
            with open(path, 'wb') as f:
                f.write(b'\n'.join(lines[-max_lines:]) + b'\n')
    except Exception:
        pass

def monitor():
    global T0_WALL
    prev = sys_times()
    # 启动时加载最近 240 条监控（图表立即有历史）
    recent = jl_read_recent(METRICS_JL, 1200)
    # 启动时立即按硬上限裁剪一次，不必等 200 tick（历史堆积立即收敛）
    threading.Thread(target=trim_jl, args=(METRICS_JL, METRICS_HARD_CAP), daemon=True).start()
    threading.Thread(target=trim_jl, args=(ENERGY_JL, ENERGY_HARD_CAP), daemon=True).start()
    now = time.time()
    with HIST_LOCK:
        for s in recent:
            if 'wall' in s:
                s['ts'] = round(s['wall'] - T0_WALL, 1)
            HIST.append(s)
    tick_n = 0
    while True:
      try:
        iv = float(PANEL_SET.get('sample_interval', 2))
        time.sleep(iv); tick_n += 1
        cur = sys_times()
        di, dk, du = cur[0]-prev[0], cur[1]-prev[1], cur[2]-prev[2]
        denom = dk + du
        cpu = 100.0 * (denom - di) / denom if denom else 0.0
        prev = cur
        g = gpu_info(); r = ram_info(); wall = time.time()
        snap = dict(ts=round(wall-T0_WALL, 1), wall=round(wall, 2),
                    cpu=round(cpu, 1),
                    ramUsed=round((r['total']-r['avail'])/1e9, 2),
                    ramTotal=round(r['total']/1e9, 2), **g)
        with HIST_LOCK:
            HIST.append(snap)
            if len(HIST) > 1200: HIST.pop(0)
        SYS.clear(); SYS.update(snap)
        jl_append(METRICS_JL, snap)
        # 能量明细独立记录（仅 wall/power，体积小、保留更久）
        if snap.get('power') is not None:
            jl_append(ENERGY_JL, dict(wall=snap['wall'], power=snap['power']))
        # 每 200 tick 裁剪一次；永久模式也有硬上限，防止无界增长
        if tick_n % 200 == 0:
            rh = float(PANEL_SET.get('retain_hours', 24))
            maxl = int(rh*3600/max(iv, 0.5))
            if PANEL_SET.get('permanent', True):
                maxl = max(maxl, METRICS_HARD_CAP)
            threading.Thread(target=trim_jl, args=(METRICS_JL, maxl), daemon=True).start()
            threading.Thread(target=trim_jl, args=(ENERGY_JL, ENERGY_HARD_CAP), daemon=True).start()
      except Exception:
        # 单次采集失败不得杀死监控线程，退避后继续
        time.sleep(2)

threading.Thread(target=monitor, daemon=True, name='xh_monitor').start()

# ============================ 通用 ============================
def load_cfg():
    if not os.path.exists(CONFIG):
        save_cfg(DEFAULTS)
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)

def save_cfg(profiles):
    tmp = CONFIG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)

_CACHE = {}
def cached(key, ttl, fn):
    now = time.time()
    e = _CACHE.get(key)
    if e is not None and now - e[0] < ttl:
        return e[1]
    v = fn()
    _CACHE[key] = (now, v)
    return v
def cache_clear(*keys):
    if not keys:
        _CACHE.clear()
        _CT.update(t=0.0, v=None)          # R11：端口内核表缓存一并失效
        _PS.update(t=0.0, txt='', lst=None)  # R11：进程快照缓存一并失效
        try:
            for _e in _ST_LAYERS.values():
                _e.update(t=0.0, v=None)
            _STATE_C.update(t=0.0, v=None)
        except NameError:  # 定义顺序：cache_clear 早于 _ST_LAYERS
            pass
    else:
        for k in keys:
            _CACHE.pop(k, None)

# R11（XH-071）：端口表改读内核。改前实测 netstat -ano 单次 96 ms，
# /api/state 每 3 s 重建一次，光这一项就占单核约 3%。GetExtendedTcpTable 直读
# iphlpapi，同样信息 <3 ms；失败时才回退 netstat，行为不变。
_CT = {'t': 0.0, 'v': None}
_DLL = {}


def _win_dll(name):
    # ctypes.WinDLL() 每次调用要重新加载导出表（实测 ~10 ms），句柄缓存放模块级
    d = _DLL.get(name)
    if d is None:
        d = ctypes.WinDLL(name)
        _DLL[name] = d
    return d


def _tcp_table_ctypes():
    """{port: pid}，仅 IPv4 LISTENING；不可用时返回 None。"""
    try:
        class _ROW(ctypes.Structure):
            _fields_ = [('dwState', wintypes.DWORD), ('dwLocalAddr', wintypes.DWORD),
                        ('dwLocalPort', wintypes.DWORD), ('dwRemoteAddr', wintypes.DWORD),
                        ('dwRemotePort', wintypes.DWORD), ('dwOwningPid', wintypes.DWORD)]
        iph = _win_dll('iphlpapi.dll')
        size = wintypes.DWORD(0)
        # AF_INET=2, TCP_TABLE_OWNER_PID_LISTENER=3；122=ERROR_INSUFFICIENT_BUFFER
        ret = iph.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
        if ret != 122:
            return None
        buf = (ctypes.c_char * size.value)()
        ret = iph.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 3, 0)
        if ret != 0:
            return None
        n = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
        rows = ctypes.cast(ctypes.byref(buf, 4), ctypes.POINTER(_ROW))
        m = {}
        for i in range(n):
            row = rows[i]
            m[socket.ntohs(row.dwLocalPort & 0xFFFF)] = int(row.dwOwningPid)
        return m
    except Exception:
        return None


def _netstat_text():
    try:
        return subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                              timeout=10, creationflags=NO_WIN).stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def _netstat_listening_map():
    m = {}
    for line in _netstat_text().splitlines():
        if 'LISTENING' not in line:
            continue
        parts = line.split()
        try:
            m[int(parts[1].rsplit(':', 1)[1])] = int(parts[-1])
        except Exception:
            pass
    return m


def listening_map():
    # 读内核表、不发 SYN，规避本机 loopback 未监听端口 SYN 被丢弃（每次 connect 卡约 2s）
    now = time.time()
    if _CT['v'] is not None and now - _CT['t'] < 1.5:
        return _CT['v']
    m = _tcp_table_ctypes()
    if m is None:
        m = _netstat_listening_map()
    _CT.update(t=now, v=m)
    return m


def port_listening(port):
    return port in listening_map()


def port_pid(port):
    """返回监听端口的 PID；来源同上（1.5 s 缓存）。"""
    return listening_map().get(port)

_LMS_CLI_LOCK = threading.Lock()

def hidden(args, timeout=40):
    """子进程输出走临时文件，不走管道。
    2026-10-02：LM Studio 的 CLI 会派生常驻子进程并继承管道 —— subprocess.run 超时后
    杀掉了 CLI 自己，却仍会去读那根管道（永不返回）。实测把 14 个 lms 挂死、面板所有
    HTTP 线程被占满、/api/state 30s 超时（界面空白）。文件重定向没有这个依赖。
    """
    import tempfile
    of = ef = None
    try:
        of = tempfile.TemporaryFile()
        ef = tempfile.TemporaryFile()
        p = subprocess.Popen(args, stdout=of, stderr=ef, stdin=subprocess.DEVNULL,
                             creationflags=NO_WIN, startupinfo=HIDDEN_SI)
        try:
            rc = p.wait(timeout=timeout)
        except Exception:
            try:
                subprocess.run(['taskkill', '/F', '/PID', str(p.pid)],
                               capture_output=True, timeout=10, creationflags=NO_WIN)
            except Exception:
                pass
            rc = -1
        of.seek(0); ef.seek(0)
        return rc, of.read().decode('utf-8', 'replace'), ef.read().decode('utf-8', 'replace')
    except Exception as e:
        return -1, '', str(e)
    finally:
        for f in (of, ef):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass

def _tasklist_text():
    try:
        return subprocess.run(['tasklist'], capture_output=True, timeout=8,
                              creationflags=NO_WIN).stdout.decode('utf-8','replace')
    except Exception:
        return ''


# R11（XH-071）：进程清单改读 Toolhelp 快照。改前 tasklist 全量 583 ms、
# tasklist /FI 638 ms —— /api/state 每次重建都被这两条子进程拖到 0.6 s+。
# 快照走 kernel32，<3 ms；失败才回退 tasklist。
_PS = {'t': 0.0, 'txt': '', 'lst': None}


def _proc_list_ctypes():
    try:
        class _PE(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ProcessID', wintypes.DWORD),
                        ('th32DefaultHeapID', ctypes.c_void_p),
                        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD),
                        ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                        ('szExeFile', wintypes.WCHAR * 260)]
        k32 = _win_dll('kernel32.dll')
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        h = k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
        if not h or h == 0xFFFFFFFFFFFFFFFF:
            return None
        out = []
        pe = _PE()
        pe.dwSize = ctypes.sizeof(_PE)
        ok = k32.Process32FirstW(h, ctypes.byref(pe))
        while ok:
            out.append((int(pe.th32ProcessID), pe.szExeFile))
            ok = k32.Process32NextW(h, ctypes.byref(pe))
        k32.CloseHandle(h)
        return out
    except Exception:
        return None


def _process_snapshot():
    now = time.time()
    # R11：Toolhelp 全量快照单次 ~10 ms，1.5 s 一取对 /api/state 仍是每拍重算；
    # 放宽到 5 s（进程启停/面板动作都会 cache_clear 立即失效）。
    if _PS['txt'] and now - _PS['t'] < 5.0:
        return _PS['lst'], _PS['txt']
    lst = _proc_list_ctypes()
    txt = ('\n'.join(n for _p, n in lst) if lst is not None else _tasklist_text())
    _PS.update(t=now, txt=txt, lst=lst)
    return lst, txt


def proc_count(name):
    # 1.5 s 内所有进程计数共用一次快照（改前是共用一次 tasklist 子进程）
    return _process_snapshot()[1].count(name)


def proc_name(pid):
    lst, _txt = _process_snapshot()
    if lst is None:
        return ''
    for p, n in lst:
        if p == pid:
            return n
    return ''

# ============================ KVMem 档案 ============================
def _model_path(rel, root='models_dir'):
    """模型相对路径 → 绝对路径（相对路径以 models_dir / models_flash_dir 为基准）。"""
    rel = str(rel or '')
    if not rel or os.path.isabs(rel):
        return rel
    base = MODELS_FLASH if root == 'models_flash' else MODELS
    return os.path.join(base, rel) if base else rel


def _resolve_prof(prof):
    """返回档案副本，model/mmproj 已按 root 解析为绝对路径（不改写配置文件）。"""
    p = dict(prof)
    root = p.get('root') or 'models_dir'
    p['model'] = _model_path(p.get('model'), root)
    p['mmproj'] = _model_path(p.get('mmproj'), root)
    return p


DEFAULTS = [
 dict(id='flash64', label='Flash-9B Q4 · 256K', root='models_flash',
      model=r'QwenPaw-Flash-9B-heretic-MTP-GGUF\QwenPaw-Flash-9B-heretic-MTP-Q4_K_M.gguf',
      mmproj='', alias='local/flash9b-long',       ctx=262144, n=6144, ctk='q4_0', ctv='q4_0',
      budget=12288, reserve=4096, block=128, threads=0),
 dict(id='qwen9bq4', label='Qwen3.5-9B Q4 · 256K', root='models_dir',
      model=r'Qwen3.5-9B-GGUF\Qwen3.5-9B-Q4_K_M.gguf',
      mmproj='', alias='local/qwen9b-q4-long',       ctx=262144, n=6144, ctk='q4_0', ctv='q4_0',
      budget=12288, reserve=4096, block=128, threads=0),
]

KV = {'id': None, 'proc': None, 'ready': False, 'msg': '', 'started': None, 'fh': None}
KV_LOCK = threading.RLock()

def kv_alive():
    # 2026-10-02：自己有进程句柄时只认它。原来无条件按进程名计数，任何外来的
    # llama-kvmem-server（例如手工测试实例）都会让面板以为自己的引擎还活着。
    if KV['proc'] is not None:
        return KV['proc'].poll() is None
    # R11：1234 没人监听就不可能有引擎（省一次全量进程快照）
    if not port_listening(MODEL_PORT):
        return False
    return proc_count('llama-kvmem-server.exe') > 0

def _engine_startup_error(pid):
    """引擎自己写出 KVMEM_STARTUP_ERROR 即启动失败（典型：1234 被 LM Studio 抢绑）。"""
    try:
        with open(os.path.join(LOGDIR, pid + '_err.log'), 'rb') as f:
            return b'KVMEM_STARTUP_ERROR' in f.read()
    except Exception:
        return False

def wait_ready(pid):
    # 2026-10-02：原来只要 1234 上有 /v1/models 就算就绪 —— LM Studio 抢绑时会替它应答，
    # 面板于是误报 ready，DSH 拿 local/* 去问 LM Studio 全部 400。现在要求：
    #   ① 应答里有本档案的 alias；② 引擎日志里没有 KVMEM_STARTUP_ERROR；③ 自己的进程没退。
    alias = ''
    try:
        alias = next((p.get('alias') or '' for p in load_cfg() if p['id'] == pid), '')
    except Exception:
        pass
    for _ in range(45):
        time.sleep(2)
        if KV['proc'] is not None and KV['proc'].poll() is not None:
            KV['ready'] = False
            KV['msg'] = ('启动失败：引擎已退出（%s）'
                         % ('端口 %d 被占用' % MODEL_PORT if _engine_startup_error(pid)
                            else '未知原因，见 logs\\' + pid + '_err.log'))
            return
        if not kv_alive() or KV['id'] != pid:
            KV['ready'] = False; return
        if port_listening(MODEL_PORT):
            try:
                d = json.load(urllib.request.urlopen(
                    'http://127.0.0.1:%d/v1/models' % MODEL_PORT, timeout=3))
                ids = [m.get('id', '') for m in (d.get('data') or [])]
                if ids and (not alias or any(alias in i for i in ids)):
                    KV['ready'] = True; return
                if ids:
                    KV['msg'] = ('端口 %d 上不是本档案（应答 %s ≠ %s），等待引擎绑端口'
                                 % (MODEL_PORT, ','.join(ids[:2]), alias or '?'))
            except Exception:
                pass
    KV['ready'] = False
    KV['msg'] = '90 秒未就绪，见 logs\\' + pid + '_err.log'

def kv_stop():
    with KV_LOCK:
        if kv_alive():
            try:
                KV['proc'].terminate(); time.sleep(1.5)
                if KV['proc'].poll() is None: KV['proc'].kill()
            except Exception:
                pass
        try:
            subprocess.run(['taskkill','/F','/IM','llama-kvmem-server.exe'],
                           capture_output=True, timeout=20, creationflags=NO_WIN)
        except Exception:
            pass
        fh = KV.get('fh')
        if fh:
            for _f in fh:
                try: _f.close()
                except Exception: pass
        KV.update(id=None, proc=None, ready=False, msg='已停止', started=None, fh=None)
    stop_proxy()
    return dict(ok=True, msg='KVMem 已停止')

def kv_start(pid):
    # 快速校验后立即返回；显存释放与进程启动放到后台线程，避免 HTTP 请求长时间阻塞
    with KV_LOCK:
        prof = next((p for p in load_cfg() if p['id'] == pid), None)
        if not prof:
            return dict(ok=False, msg='没有该档案')
        prof = _resolve_prof(prof)
        if not os.path.exists(prof['model']):
            return dict(ok=False, msg='模型文件不存在：' + prof['model'])
        if kv_alive() and KV['id'] == pid:
            return dict(ok=True, msg='已在运行')
        if LMS_STARTING.is_set():
            return dict(ok=False, msg='LM Studio 正在启动，请等它完成后再切 KVMem')
        if KV.get('starting'):
            return dict(ok=False, msg='另一个模型正在启动，请稍候')
        KV['starting'] = pid
        KV.update(id=pid, ready=False, msg='启动中…', started=time.time())
        threading.Thread(target=_kv_start_bg, args=(pid,), daemon=True,
                         name='xh_kvstart').start()
    return dict(ok=True, msg='启动中：' + prof['label'])

def _kv_start_bg(pid):
    try:
        with KV_LOCK:
            prof = next((p for p in load_cfg() if p['id'] == pid), None)
            if not prof:
                return
            prof = _resolve_prof(prof)
            if prof['mmproj'] and not os.path.exists(prof['mmproj']):
                prof['mmproj'] = ''
            kv_stop()
            if lms_running():
                try: lms_unload()
                except Exception: pass
                lms_kill()
            freed = False
            for _ in range(20):
                try:
                    subprocess.run(['taskkill','/F','/IM','llama-server.exe'],
                                   capture_output=True, timeout=10, creationflags=NO_WIN)
                except Exception:
                    pass
                time.sleep(2)
                if not port_listening(MODEL_PORT) and gpu_info()['memUsed'] < 1500:
                    freed = True; break
            if cf_alive():
                cf_free()
            if not freed and (port_listening(MODEL_PORT) or gpu_info()['memUsed'] >= 1500):
                KV.update(id=None, ready=False,
                          msg='显存/端口未释放（已用 %d MiB），启动失败' % gpu_info()['memUsed'])
                return
            # 2026-10-02：这里不再 sleep —— LM Studio（--run-as-service）被 kill 后约 2s
            # 会自愈回来抢 1234，必须"确认端口释放就立刻绑"。
            # 自适应 KV 精度：启动前按「规模 + 空闲内存」决定档位（见 kvmem_adaptive_precision）
            ad = kvmem_adaptive_precision(prof)
            LAST_ADAPT.clear(); LAST_ADAPT.update(ad)
            args = [KV_BIN, '-m', prof['model'], '-a', prof['alias'],
                    '--host', '127.0.0.1', '--port', str(MODEL_PORT),
                    '-c', str(prof['ctx']), '-n', str(prof['n']),
                    '-ctk', ad['ctk'], '-ctv', ad['ctv'],
                    '--kvmem-budget', str(ad['budget']),
                    '--kvmem-gen-reserve', str(prof['reserve']),
                    '--kvmem-block-tokens', str(prof['block']), '--jinja']
            if prof['mmproj']:
                args += ['--mmproj', prof['mmproj']]
            if int(prof.get('threads', 0)) > 0:
                args += ['-t', str(prof['threads'])]
            so = open(os.path.join(LOGDIR, pid + '_out.log'), 'wb')
            se = open(os.path.join(LOGDIR, pid + '_err.log'), 'wb')
            try:
                proc = subprocess.Popen(args, stdout=so, stderr=se, creationflags=NO_WIN,
                                        close_fds=True, startupinfo=HIDDEN_SI)
            except Exception as e:
                KV.update(msg='启动失败：' + str(e)); return
            KV.update(id=pid, proc=proc, ready=False,
                      msg=('启动中…（KV 精度 %s→%s）' % (prof['ctk'], ad['ctk'])
                           if ad.get('changed') else '启动中…'),
                      started=time.time(), fh=(so, se))
            # 2026-10-02：还是被抢（LM Studio 自愈）→ 清一次端口，重试一次。
            time.sleep(3)
            if proc.poll() is not None and _engine_startup_error(pid):
                plog('[KV启动] 端口 %d 被抢绑，清端口后重试一次' % MODEL_PORT)
                try: lms_kill()
                except Exception: pass
                for _ in range(10):
                    if not port_listening(MODEL_PORT): break
                    time.sleep(0.5)
                so.close(); se.close()
                so = open(os.path.join(LOGDIR, pid + '_out.log'), 'wb')
                se = open(os.path.join(LOGDIR, pid + '_err.log'), 'wb')
                try:
                    proc = subprocess.Popen(args, stdout=so, stderr=se, creationflags=NO_WIN,
                                        close_fds=True, startupinfo=HIDDEN_SI)
                except Exception as e:
                    KV.update(msg='启动失败：' + str(e)); return
                KV.update(id=pid, proc=proc, ready=False, msg='启动中…（端口重试）',
                          started=time.time(), fh=(so, se))
            # 保活代理改到引擎启动之后再拉：原来它在 Popen 之前起（最长 5s），
            # 正好把"端口已释放 → 引擎绑端口"的窗口让给了 ~2s 就自愈的 LM Studio。
            ensure_proxy()
        wait_ready(pid)   # 锁外轮询就绪
    finally:
        KV['starting'] = None

# ============================ LM Studio ============================
def lms_running():
    """新版 LM Studio 将 server 内嵌主进程（不再 spawn 独立 llama-server.exe）。
    判定：llama-server.exe 存在，或 1234 端口被 LM Studio.exe 监听。"""
    if proc_count('llama-server.exe') > 0:
        return True
    pid = port_pid(MODEL_PORT)
    if not pid:
        return False
    # R11：原 tasklist /FI PID（实测 638 ms/次）改为读同一份进程快照（<1 ms）
    return 'LM Studio.exe' in proc_name(pid)

def _lms_ps_raw():
    rc, out, err = hidden([LMS, 'ps'], 20)
    txt = out + err
    if 'No models' in txt:
        return []
    return [l.strip() for l in txt.splitlines() if l.strip()]
def _lms_ls_raw():
    rc, out, err = hidden([LMS, 'ls'], 30)
    return (out + err).strip()

# 2026-10-02：LM Studio 的 CLI 查询改到【后台线程】刷新缓存，/api/state 只读缓存。
# 面板的模型清单/已加载数据必须靠 lms CLI 拿（用户要求：不调它面板就没数据），但绝
# 不能在 HTTP 请求线程里同步等它 —— 于是后台每 3s 刷一次，请求路径永不阻塞。
LMS_PS = {'v': []}
LMS_LS = {'v': ''}
LMS_REFRESH = threading.Event()

LMS_LS_CACHE = os.path.join(DATA, 'lms-ls-cache.txt')

def _lms_alive():
    """LM Studio 主进程是否在跑。用来决定能不能问 CLI。"""
    return proc_count('LM Studio.exe') > 0

def _lms_ls_cached():
    try:
        with open(LMS_LS_CACHE, encoding='utf-8') as f:
            LMS_LS['v'] = f.read().strip()
    except Exception:
        pass

_lms_ls_cached()

def _lms_refresh_once():
    # 2026-10-20：lms CLI（ps/ls）在 LM Studio 未运行时会打印 "Waking up LM Studio service..."
    # 并把 LM Studio 以 --run-as-service 无头拉起。原来每 3s 无脑调用一次，等于让 LM Studio
    # 永远常驻后台（关了也在 3s 内被唤醒）。改为只在 LM Studio 已在运行时才问 CLI；
    # 关闭时沿用上次 ls 结果（已落盘，重启面板后模型清单仍在）。
    if not _lms_alive():
        LMS_PS['v'] = []
        return
    try:
        LMS_PS['v'] = _lms_ps_raw()
    except Exception:
        pass
    try:
        txt = _lms_ls_raw()
        if txt:
            LMS_LS['v'] = txt
            try:
                with open(LMS_LS_CACHE, 'w', encoding='utf-8') as f:
                    f.write(txt)
            except Exception:
                pass
    except Exception:
        pass

def lms_cache_loop():
    while True:
        _lms_refresh_once()
        # R10（loopslow）：LM Studio 未做推理路由时，3 s 一次 tasklist 子进程纯属空转；
        # 放宽到 30 s，只在用户切到 LM Studio 路线时影响状态芯片的新鲜度。
        # R11（slow3）：再放宽到 120 s；面板内 load/unload 仍会 set(LMS_REFRESH) 立刻刷新。
        LMS_REFRESH.wait(120.0 if 'slow3' in XH_R11
                         else (30.0 if 'loopslow' in XH_R10 else 3.0))
        LMS_REFRESH.clear()

def lms_ps():
    return LMS_PS['v']

def lms_ls():
    return LMS_LS['v']

threading.Thread(target=lms_cache_loop, daemon=True, name='xh_lms').start()

LMS_STARTING = threading.Event()   # lms server start 进行中：期间拒绝 KVMem 启动与模型加载

def lms_start():
    if KV.get('starting'):
        return dict(ok=False, msg='KVMem 正在启动，请稍候再启动 LM Studio')
    kv_stop(); time.sleep(1)
    if cf_alive():
        cf_free()
    lms_kill()
    ensure_proxy()   # LM Studio 路线同样经 1235 代理（keepalive 对其无害）
    LMS_STARTING.set()
    try:
        hidden([LMS, 'server', 'start', '--port', str(MODEL_PORT)], 60)
    finally:
        LMS_STARTING.clear()
    ok = lms_running()
    return dict(ok=ok, msg='LM Studio 服务已启动' if ok else '启动失败，请在 LM Studio 手动开启服务')

def lms_stop():
    hidden([LMS, 'server', 'stop'], 30)
    try:
        subprocess.run(['taskkill','/F','/IM','llama-server.exe'],
                       capture_output=True, timeout=20, creationflags=NO_WIN)
    except Exception:
        pass
    stop_proxy()
    return dict(ok=True, msg='LM Studio 服务已停止')

def lms_kill():
    """彻底停掉 LM Studio：先停 server，再杀主进程（--run-as-service 常驻会自愈，必须先杀主进程）。"""
    hidden([LMS, 'server', 'stop'], 20)
    for exe in ('LM Studio.exe', 'llama-server.exe'):
        try:
            subprocess.run(['taskkill', '/F', '/IM', exe],
                           capture_output=True, timeout=20, creationflags=NO_WIN)
        except Exception:
            pass
    for _ in range(15):
        time.sleep(2)
        if not port_listening(MODEL_PORT) and not proc_count('LM Studio.exe'):
            break
    ok = not port_listening(MODEL_PORT)
    stop_proxy()
    return dict(ok=ok, msg='LM Studio 已彻底关闭（端口 1234 已释放）' if ok
                else 'LM Studio 仍在占用端口 1234，请手动关闭')

def lms_load(model):
    if LMS_STARTING.is_set():
        return dict(ok=False, msg='LM Studio 服务正在启动，请稍候再加载模型')
    rc, out, err = hidden([LMS, 'load', model, '--port', str(MODEL_PORT)], 180)
    LMS_REFRESH.set()   # 立刻刷新"已加载"清单
    return dict(ok=(rc == 0), msg=(out + err)[:300])

def lms_unload():
    hidden([LMS, 'unload', '--all'], 60)
    LMS_REFRESH.set()
    return dict(ok=True, msg='已卸载 LM Studio 模型')

# ============================ ComfyUI ============================
CF = {'proc': None, 'ready': False, 'started': None, 'msg': '', 'fh': None}
CF_LOCK = threading.RLock()

def _cf_alive_raw():
    if CF['proc'] is not None and CF['proc'].poll() is None:
        return True
    return COMFY_PORT in listening_map()
def cf_alive():
    # 探测结果缓存 1s（变更 POST 后统一失效），离线时避免每次 state 都等 TCP 超时
    return cached('cf_alive', 1.0, _cf_alive_raw)

def cf_http(path, timeout=5):
    return urllib.request.urlopen('http://127.0.0.1:%d%s' % (COMFY_PORT, path), timeout=timeout)

def cf_ready_now():
    try:
        json.load(cf_http('/system_stats')); return True
    except Exception:
        return False

VIDEO_NODES = {
    'MiniMaxH3ImageToVideo', 'MiniMaxH3AddGuide', 'MiniMaxH3ReferenceToVideo',
    'MiniMaxH3SigmaShift', 'MiniMaxH3FunControlNetApply', 'EmptyMiniMaxH3LatentAV',
    'MiniMaxChunkFeedForward', 'MiniMaxLowVRAMAttention', 'TESpeedMiniMaxH3',
    'CreateVideo', 'SaveVideo', 'SaveWEBM', 'ConcatenateVideo', 'LoadVideo'}
IMAGE_NODES = {'TextEncodeQwenImage21', 'SaveImage'}

def _queue_kind(wf):
    # 从工作流节点 class_type 判断生图/生视频；video 判定优先
    classes = set()
    try:
        for node in wf.values():
            if isinstance(node, dict) and node.get('class_type'):
                classes.add(node['class_type'])
    except Exception:
        pass
    if any(c.startswith('MiniMax') for c in classes) or (classes & VIDEO_NODES):
        return 'video'
    if classes & IMAGE_NODES:
        return 'image'
    return 'unknown'

def _cf_queue_raw():
    # 返回 (busy, kind)；kind: video/image/unknown/None(空闲)
    try:
        q = json.load(cf_http('/queue', timeout=5))
    except Exception:
        return False, None
    items = (q.get('queue_running') or []) + (q.get('queue_pending') or [])
    for item in items:
        try:
            return True, _queue_kind(item[2])
        except Exception:
            return True, 'unknown'
    return False, None

def cf_queue():
    # R11（XH-071）：ComfyUI 在线时 /queue 是本机 loopback 上的固定 ~45 ms 往返，
    # 1 s 缓存等于每次 state 重建都必付一次。放宽到 12 s：面板内提交/中断都会
    # cache_clear 立即失效，外部任务的「生成中」角标最多晚 12 s，肉眼无感。
    return cached('cf_queue', 12.0, _cf_queue_raw)
def cf_busy():
    return cf_queue()[0]
def cf_kind():
    return cf_queue()[1]

def cf_interrupt():
    try:
        req = urllib.request.Request('http://127.0.0.1:%d/interrupt' % COMFY_PORT, data=b'{}',
              headers={'Content-Type':'application/json'})
        urllib.request.urlopen(req, timeout=8)
        return dict(ok=True, msg='已请求中断当前生成')
    except Exception as e:
        return dict(ok=False, msg='中断失败：' + str(e))

def record_job(rec):
    rec.setdefault('wall', round(time.time(), 2))
    jl_append(JOBS_JL, rec)

def list_jobs(n=100):
    jobs = jl_read_recent(JOBS_JL, n)
    jobs.reverse()
    return jobs

def cf_wait():
    for _ in range(45):
        time.sleep(2)
        if CF['proc'] is not None and CF['proc'].poll() is not None:
            CF['ready'] = False; CF['msg'] = 'ComfyUI 启动后退出'; return
        if cf_ready_now():
            CF['ready'] = True; CF['msg'] = '就绪'; return
    CF['ready'] = False; CF['msg'] = '90 秒未就绪'

def cf_start():
    with CF_LOCK:
        if port_listening(COMFY_PORT):
            CF.update(proc=None, ready=True, msg='已连接（Comfy Desktop 后端）')
            return dict(ok=True, msg='已连接现有 ComfyUI')
        so = open(os.path.join(LOGDIR, 'comfy_out.log'), 'wb')
        se = open(os.path.join(LOGDIR, 'comfy_err.log'), 'wb')
        # 模型目录配置由 COMFY_STORE 现场生成，避免在仓库里写死绝对路径；
        # 生成失败时退回随包的 comfy-paths.yaml。
        extra_cfg = COMFY_PATHS
        try:
            gen = os.path.join(DATA, 'comfy_extra_model_paths.yaml')
            with open(gen, 'w', encoding='utf-8') as f:
                f.write("storage:\n"
                        "  base_path: '%s'\n"
                        "  is_default: true\n"
                        "  checkpoints: checkpoints/\n"
                        "  diffusion_models: diffusion_models/\n"
                        "  text_encoders: text_encoders/\n"
                        "  vae: vae/\n"
                        "  upscale_models: upscale_models/\n"
                        % COMFY_MODELS.replace('\\', '/'))
            extra_cfg = gen
        except Exception as e:
            plog('[ComfyUI] 生成模型路径配置失败，改用随包配置：%r' % e)
        args = [COMFY_PY, "-X", "utf8", COMFY_MAIN, '--port', str(COMFY_PORT),
                '--extra-model-paths-config', extra_cfg,
                '--input-directory', os.path.join(COMFY_STORE, 'input'),
                '--output-directory', COMFY_OUT,
                '--database-url', 'sqlite:///' + COMFY_DB.replace('\\', '/')]
        try:
            proc = subprocess.Popen(args, cwd=COMFY_ROOT, stdout=so, stderr=se,
                                    creationflags=NO_WIN, close_fds=True,
                                    startupinfo=HIDDEN_SI)
        except Exception as e:
            return dict(ok=False, msg='启动失败：' + str(e))
        CF.update(proc=proc, ready=False, started=time.time(), msg='启动中…', fh=(so, se))
        threading.Thread(target=cf_wait, daemon=True, name='xh_cfwait').start()
        return dict(ok=True, msg='ComfyUI 启动中…')

def cf_stop():
    with CF_LOCK:
        if CF['proc'] is not None:
            try:
                CF['proc'].terminate(); time.sleep(1.2)
                if CF['proc'].poll() is None: CF['proc'].kill()
            except Exception:
                pass
    # 兜底：杀掉仍占用 8189 的进程树（处理重复启动 / 残留）
    pid = None
    for _ in range(6):
        pid = pid_on_port(COMFY_PORT)
        if not pid: break
        subprocess.run(['taskkill','/F','/T','/PID',str(pid)], capture_output=True,
                       timeout=15, creationflags=NO_WIN)
        time.sleep(1.5)
    gone = not pid_on_port(COMFY_PORT)
    with CF_LOCK:
        fh = CF.get('fh')
        if fh:
            for _f in fh:
                try: _f.close()
                except Exception: pass
        CF['fh'] = None
        CF.update(proc=None, ready=False, started=None,
                  msg='已停止' if gone else '端口仍占用')
    return dict(ok=gone, msg='ComfyUI 已停止' if gone else '停止失败：端口仍被占用，请检查残留进程')

def pid_on_port(port):
    try:
        p = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                           timeout=15, creationflags=NO_WIN)
        for line in p.stdout.decode('utf-8', 'replace').splitlines():
            a = line.split()
            if len(a) >= 5 and a[0].lower() == 'tcp' and a[1].endswith(':%d' % port) \
               and a[3].lower() == 'listening':
                return int(a[-1])
    except Exception:
        pass
    return None

def cf_free():
    if port_listening(COMFY_PORT):
        try:
            req = urllib.request.Request('http://127.0.0.1:%d/free' % COMFY_PORT,
                  data=json.dumps({'unload_models': True, 'free_memory': True}).encode(),
                  headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=15)
        except Exception:
            pass
        time.sleep(4)
    if gpu_info()['memUsed'] >= 4000:
        pid = pid_on_port(COMFY_PORT)
        if pid:
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True,
                           timeout=15, creationflags=NO_WIN)
            time.sleep(5)
    with CF_LOCK:
        fh = CF.get('fh')
        if fh:
            for _f in fh:
                try: _f.close()
                except Exception: pass
        CF['fh'] = None
        CF.update(proc=None, ready=False, started=None, msg='已释放')
    ok = gpu_info()['memUsed'] < 4000
    return dict(ok=ok, msg='生图显存已释放' if ok else '生图显存仍未完全释放')

def qi_present(variant='int8'):
    out = {}
    files = QI_UC if variant == 'uc-gguf' else (H3_FILES if variant == 'h3' else QI)
    for sub, name in files:
        out[sub + '/' + name] = os.path.exists(os.path.join(COMFY_MODELS, sub, name))
    return out

def cf_outputs():
    try:
        fs = [f for f in os.listdir(COMFY_OUT) if f.lower().endswith(('.png', '.jpg', '.webp'))]
        # R09（XH-065）：同一份来源白名单，避免状态接口绕过作品墙过滤
        fs = [f for f in fs if _out_program_ok(f, f.lower(), os.path.join(COMFY_OUT, f),
                                               os.stat(os.path.join(COMFY_OUT, f)))]
        return sorted(fs, key=lambda f: os.path.getmtime(os.path.join(COMFY_OUT, f)), reverse=True)[:24]
    except Exception:
        return []

def cf_video_outputs():
    try:
        fs = [f for f in os.listdir(COMFY_OUT) if f.lower().endswith(('.mp4', '.webm', '.mkv'))]
        fs = [f for f in fs if _out_program_ok(f, f.lower(), os.path.join(COMFY_OUT, f),
                                               os.stat(os.path.join(COMFY_OUT, f)))]
        return sorted(fs, key=lambda f: os.path.getmtime(os.path.join(COMFY_OUT, f)), reverse=True)[:24]
    except Exception:
        return []

def cf_all_outputs(limit=100):
    """资源池：递归读取 output/photo、output/video，name 为相对子路径，按修改时间倒序。"""
    try:
        out = []
        for root, _d, files in os.walk(COMFY_OUT):
            for f in files:
                low = f.lower()
                if not low.endswith(('.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm', '.mkv')):
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, COMFY_OUT).replace('\\', '/')
                top = rel.split('/')[0]
                kind = 'video' if low.endswith(('.mp4', '.webm', '.mkv')) else 'image'
                section = 'upscale' if top == 'upscale' else ('video' if top == 'video' else ('image' if top == 'photo' else top))
                st = os.stat(p)
                # R09（XH-065）：作品墙只收「程序自己产出」的文件，见 _out_program_ok 注释
                if not _out_program_ok(rel, low, p, st):
                    continue
                out.append(dict(name=rel, kind=kind, section=section, size=st.st_size, mtime=st.st_mtime))
        out.sort(key=lambda r: r['mtime'], reverse=True)
        return out[:limit]
    except Exception:
        return []


_PROG_OUT_CACHE = {}


def _out_program_ok(rel, low, p, st):
    """R09（XH-065）：历史墙来源白名单。

    证据链：ComfyUI 的 SaveImage 节点会把整张工作流写进 PNG 的 tEXt/iTXt "prompt"
    块（本机 207 张程序产物 100% 带此块，见 round_209_xh 审计）；用户随手放进输出
    目录的照片没有该块。因此：
      - PNG：必须带程序写的 prompt/workflow 元数据，否则不上墙；
      - 视频：没有可嵌入的等价证据，只认程序固定写入的 video/、upscale/ 两个目录；
      - 其它格式（jpg/webp）：无法证明来源，宁缺不漏。
    缓存按 (相对路径, mtime, size)，避免每次 10 s 轮询重读文件头。
    """
    top = rel.split('/')[0].lower() if '/' in rel else ''
    key = (rel, st.st_mtime, st.st_size)
    if key in _PROG_OUT_CACHE:
        return _PROG_OUT_CACHE[key]
    ok = False
    try:
        if low.endswith('.png'):
            with open(p, 'rb') as _f:
                _head = _f.read(400000)
            ok = (b'tEXtprompt' in _head or b'zTXtprompt' in _head
                  or b'iTXtprompt' in _head or b'tEXtworkflow' in _head)
        elif low.endswith(('.mp4', '.webm', '.mkv')):
            ok = top in ('video', 'upscale')
        else:
            ok = False
    except Exception:
        ok = False
    if len(_PROG_OUT_CACHE) > 4000:
        _PROG_OUT_CACHE.clear()
    _PROG_OUT_CACHE[key] = ok
    return ok


# R10（XH-067）：历史作品删除。
_DEL_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm', '.mkv')


def _del_resolve(rel):
    """把客户端提交的相对名解析成 output 白名单内的绝对路径。

    三重校验：非绝对路径/无 .. /无盘符或 ADS 冒号 → realpath 后必须仍在
    COMFY_OUT 之内 → 扩展名在允许删除的媒体类型内。任何一条不满足返回 None，
    保证「选错路径也删不到 output 之外的用户文件」。
    """
    if not isinstance(rel, str):
        return None
    rel = rel.strip().replace('\\', '/')
    if not rel or rel.startswith('/') or ':' in rel:
        return None
    if any(_p in ('', '.', '..') for _p in rel.split('/')):
        return None
    if os.path.splitext(rel)[1].lower() not in _DEL_EXT:
        return None
    root = os.path.realpath(COMFY_OUT)
    full = os.path.realpath(os.path.normpath(os.path.join(root, rel)))
    if not full.startswith(root + os.sep):
        return None
    return full


def out_delete(names):
    """R10（XH-067）：删除程序产出的历史作品（单张或批量清空）。

    只删文件、不删目录；每条都要求 realpath 在 COMFY_OUT 内且通过
    _out_program_ok（PNG 带工作流元数据 / 视频位于 video|upscale），
    不满足的条目记入 failed 但不影响其它条目。删除成功后同步清掉该条收藏，
    并失效 _PROG_OUT_CACHE，后续 /api/outputs 轮询不会再返回它。
    需 X-Local-Token（do_POST 统一校验）。
    """
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        return dict(ok=False, error='names must be list')
    if not names or len(names) > 500:
        return dict(ok=False, error='names count out of range')
    root = os.path.realpath(COMFY_OUT)
    deleted, failed = [], []
    fav = fav_load()
    fav_changed = False
    for raw in names:
        full = _del_resolve(raw)
        if full is None:
            failed.append(dict(name=raw if isinstance(raw, str) else repr(raw),
                               reason='path not allowed'))
            continue
        if not os.path.isfile(full):
            failed.append(dict(name=raw, reason='not found'))
            continue
        rel = os.path.relpath(full, root).replace('\\', '/')
        try:
            st = os.stat(full)
            if not _out_program_ok(rel, rel.lower(), full, st):
                failed.append(dict(name=rel, reason='not program output'))
                continue
            os.remove(full)
        except Exception as e:
            failed.append(dict(name=rel, reason='remove failed: %s' % e))
            continue
        deleted.append(rel)
        _PROG_OUT_CACHE.pop((rel, st.st_mtime, st.st_size), None)
        if rel in fav:
            fav = [x for x in fav if x != rel]
            fav_changed = True
    if fav_changed:
        try:
            fav_save(fav)
        except Exception:
            pass
    return dict(ok=bool(deleted), deleted=deleted, failed=failed,
                n=len(deleted), nfail=len(failed),
                msg='已删除 %d 项%s' % (len(deleted),
                                       ('，%d 项未删除' % len(failed)) if failed else ''))

# ============================ 实时日志 ============================
def tail_log(path, n_lines=120):
    try:
        if not os.path.exists(path):
            return []
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - 81920))  # 最多读 80KB 尾部
            raw = f.read().decode('utf-8', 'replace')
        return raw.splitlines()[-n_lines:]
    except Exception:
        return []

def kv_log_lines(n=120):
    pid = KV['id']
    if not pid:
        return ['[未运行] 当前没有正在运行的 KVMem 实例']
    lines = tail_log(os.path.join(LOGDIR, pid + '_out.log'), n)
    errs = tail_log(os.path.join(LOGDIR, pid + '_err.log'), n)
    if not lines and not errs:
        return ['[运行中] 暂无日志输出（' + pid + '）']
    return lines + (['--- stderr ---'] if errs else []) + errs

def comfy_log_lines(n=120):
    if not port_listening(COMFY_PORT):
        return ['[未运行] ComfyUI 未启动']
    lines = tail_log(os.path.join(LOGDIR, 'comfy_out.log'), n)
    errs = tail_log(os.path.join(LOGDIR, 'comfy_err.log'), n)
    if not lines and not errs:
        return ['[运行中] 暂无日志输出']
    return lines + (['--- stderr ---'] if errs else []) + errs

# ============================ 杰作收藏 ============================
FAV_F = os.path.join(DATA, 'favorites.json')
def fav_load():
    try:
        return json.load(open(FAV_F, encoding='utf-8'))
    except Exception:
        return []
def fav_save(v):
    # R2-XH04：tmp + os.replace 原子写（写入中断不留半截 JSON）
    tmp = FAV_F + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(v, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FAV_F)
VIDEO_EXT = ('.mp4', '.webm', '.mkv')
def _fav_kind(name):
    top = name.replace('\\', '/').split('/')[0].lower()
    if top == 'video': return 'video'
    if top == 'photo': return 'image'
    return 'video' if name.lower().endswith(VIDEO_EXT) else 'image'
FAV_MAX = 8
def fav_toggle(name):
    # R2-XH04：name 校验——非字符串/空/越界序列拒绝（实测空名曾写入 [""]）
    if not isinstance(name, str):
        return dict(ok=False, error='invalid name', fav=fav_load())
    name = name.strip()
    if not name or '..' in name or name.startswith('/'):
        return dict(ok=False, error='invalid name', fav=fav_load())
    old = fav_load()
    if name in old:
        v = [x for x in old if x != name]
    else:
        kind = _fav_kind(name)
        v = list(old)
        same = [x for x in v if _fav_kind(x) == kind]
        if len(same) >= FAV_MAX:
            v = [x for x in v if x != same[-1]]   # 顶替同 kind 最旧（队尾）
        v = [name] + v
    fav_save(v)
    return dict(ok=True, fav=v)
def fav_info():
    v = fav_load()
    items = []
    for name in v:
        p = os.path.join(COMFY_OUT, name)
        items.append(dict(name=name, kind=_fav_kind(name), exists=os.path.exists(p)))
    return dict(fav=v, items=items,
                nImage=sum(1 for i in items if i['kind'] == 'image'),
                nVideo=sum(1 for i in items if i['kind'] == 'video'),
                max=FAV_MAX)

# ============================ 磁盘健康 ============================
def disk_info():
    try:
        # 系统盘（原写死 C:\，非 C 盘系统会统计错盘）
        drv = os.environ.get('SystemDrive') or (os.path.splitdrive(_APP_ROOT)[0] or 'C:')
        u = shutil.disk_usage(drv + '\\')
        return dict(total=u.total, used=u.used, free=u.free,
                    usedPct=round(u.used / u.total * 100, 1),
                    freeGB=round(u.free / 1e9, 1))
    except Exception:
        return dict(total=0, used=0, free=0, usedPct=0, freeGB=0)

def build_wf(prompt, neg, w, h, steps, cfg, seed, prefix='Agent'):
    return {
     'unet': {'class_type': 'UNETLoader', 'inputs': {'unet_name': QI[0][1], 'weight_dtype': 'default'}},
     'clip': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': QI[1][1], 'type': 'qwen_image'}},
     'vae': {'class_type': 'VAELoader', 'inputs': {'vae_name': QI[2][1]}},
     'enc': {'class_type': 'TextEncodeQwenImage21', 'inputs': {'clip': ['clip', 0],
            'prompt': prompt, 'negative_prompt': neg, 'resolution': 1024}},
     'lat': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': w, 'height': h, 'batch_size': 1}},
     'sam': {'class_type': 'KSampler', 'inputs': {'model': ['unet', 0], 'positive': ['enc', 0],
            'negative': ['enc', 1], 'latent_image': ['lat', 0], 'seed': seed, 'steps': steps,
            'cfg': cfg, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}},
     'dec': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sam', 0], 'vae': ['vae', 0]}},
     'save': {'class_type': 'SaveImage', 'inputs': {'images': ['dec', 0], 'filename_prefix': 'photo/' + prefix}},
    }

def build_wf_gguf(prompt, neg, w, h, steps, cfg, seed, prefix='UC'):
    return {
     'unet': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': QI_UC[0][1]}},
     # w4a8 文本编码器约 4-5GB，可驻 GPU、编码快（编码后 ComfyUI 自动 offload 给扩散模型腾位）
     'clip': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': QI_UC[1][1], 'type': 'qwen_image'}},
     'vae': {'class_type': 'VAELoader', 'inputs': {'vae_name': QI_UC[2][1]}},
     'enc': {'class_type': 'TextEncodeQwenImage21', 'inputs': {'clip': ['clip', 0],
            'prompt': prompt, 'negative_prompt': neg, 'resolution': 1024}},
     'lat': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': w, 'height': h, 'batch_size': 1}},
     'sam': {'class_type': 'KSampler', 'inputs': {'model': ['unet', 0], 'positive': ['enc', 0],
            'negative': ['enc', 1], 'latent_image': ['lat', 0], 'seed': seed, 'steps': steps,
            'cfg': cfg, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}},
     'dec': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sam', 0], 'vae': ['vae', 0]}},
     'save': {'class_type': 'SaveImage', 'inputs': {'images': ['dec', 0], 'filename_prefix': 'photo/' + prefix}},
    }

def build_wf_h3(prompt, w, h, seconds, seed, prefix='H3', first_frame=None, last_frame=None):
    # 官方对齐：124帧≈5s，17格对齐（max(5,round(s*24)) + (5-...%17)%17）
    f = max(5, round(float(seconds) * 24))
    length = f + (5 - (f % 17)) % 17
    wf = {
     'unet': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': H3_FILES[0][1]}},
     # H3 文本编码器 15.69GB 超 8GB 显存 → CPU 一次性编码；type=minimax
     'clip': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': H3_FILES[1][1],
            'type': 'minimax', 'device': 'cpu'}},
     'vvae': {'class_type': 'VAELoader', 'inputs': {'vae_name': H3_FILES[2][1]}},
     'avae': {'class_type': 'VAELoader', 'inputs': {'vae_name': H3_FILES[3][1]}},
     'h3': {'class_type': 'MiniMaxH3ImageToVideo', 'inputs': {
            'clip': ['clip', 0], 'vae': ['vvae', 0], 'prompt': prompt,
            'width': w, 'height': h, 'length': length}},
     'lora': {'class_type': 'LoraLoaderModelOnly', 'inputs': {
            'model': ['unet', 0], 'lora_name': H3_FILES[4][1], 'strength_model': 1.0}},
     'noise': {'class_type': 'RandomNoise', 'inputs': {'noise_seed': seed}},
     'sched': {'class_type': 'BasicScheduler', 'inputs': {
            'model': ['lora', 0], 'scheduler': 'simple', 'steps': 4, 'denoise': 1.0}},
     'sampler': {'class_type': 'KSamplerSelect', 'inputs': {'sampler_name': 'res_multistep'}},
     'guider': {'class_type': 'BasicGuider', 'inputs': {
            'model': ['lora', 0], 'conditioning': ['h3', 0]}},
     'sam': {'class_type': 'SamplerCustomAdvanced', 'inputs': {
            'noise': ['noise', 0], 'guider': ['guider', 0], 'sampler': ['sampler', 0],
            'sigmas': ['sched', 0], 'latent_image': ['h3', 1]}},
     'vdec': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sam', 0], 'vae': ['vvae', 0]}},
     'adec': {'class_type': 'VAEDecodeAudio', 'inputs': {'samples': ['sam', 0], 'vae': ['avae', 0]}},
     'vid': {'class_type': 'CreateVideo', 'inputs': {'images': ['vdec', 0],
            'audio': ['adec', 0], 'fps': 24.0}},
     'save': {'class_type': 'SaveVideo', 'inputs': {'video': ['vid', 0],
            'filename_prefix': 'video/' + prefix, 'format': 'auto'}},
    }
    def _li_ref(rel):
        parts = str(rel).replace('\\', '/').split('/')
        fn = parts[-1]; sub = '/'.join(parts[:-1])
        return fn + (' [' + sub + ']' if sub else '')
    if first_frame:
        wf['ff'] = {'class_type': 'LoadImage', 'inputs': {'image': _li_ref(first_frame)}}
        wf['h3']['inputs']['first_frame'] = ['ff', 0]
    if last_frame:
        wf['lf'] = {'class_type': 'LoadImage', 'inputs': {'image': _li_ref(last_frame)}}
        wf['h3']['inputs']['last_frame'] = ['lf', 0]
    return wf, length

# ============================ 超分放大（图片 + 视频） ============================
SEEDVR2_UNET = 'seedvr2_3b_nvfp4.safetensors'
SEEDVR2_VAE  = 'seedvr2_ema_vae_fp16.safetensors'
UPSCALE_TOP  = 'upscale'   # 放大结果独立顶级目录，与 photo、video 同级（图+视频都放这里）

def _li_ref(rel):
    return str(rel).replace(chr(92), '/')

def upscale_model_files():
    d = os.path.join(COMFY_MODELS, 'upscale_models')
    out = []
    try:
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(('.pth', '.safetensors', '.ckpt')):
                out.append(f)
    except Exception:
        pass
    return out

def _walk_input(exts):
    d = os.path.join(COMFY_STORE, 'input')
    out = []
    for root, _dd, files in os.walk(d):
        for f in sorted(files):
            if f.lower().endswith(exts):
                out.append(os.path.relpath(os.path.join(root, f), d).replace('\\', '/'))
    return out

def input_image_files():
    try:
        return _walk_input(('.png', '.jpg', '.jpeg', '.webp'))
    except Exception:
        return []

def input_video_files():
    try:
        return _walk_input(('.mp4', '.webm', '.mkv', '.mov', '.avi'))
    except Exception:
        return []

def seedvr2_ready():
    return (os.path.exists(os.path.join(COMFY_MODELS, 'diffusion_models', SEEDVR2_UNET))
            and os.path.exists(os.path.join(COMFY_MODELS, 'vae', SEEDVR2_VAE)))

def vosr2_ready():
    return os.path.exists(os.path.join(COMFY_ROOT, 'models', 'vosr2', 'VOSR2',
                                       'checkpoints', 'ema_model.safetensors'))

# ---------------- 工作流：ESRGAN（图片） ----------------
def _wf_esrgan_image(image, model, prefix, scale=4):
    wf = {}
    wf['load'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
    wf['ml']   = {'class_type': 'UpscaleModelLoader', 'inputs': {'model_name': model}}
    wf['up']   = {'class_type': 'ImageUpscaleWithModel',
                  'inputs': {'upscale_model': ['ml', 0], 'image': ['load', 0]}}
    # R01（25 轮）：ESRGAN 模型原生 4×；不再无视所选倍率。非 4× 时在 4× 结果上
    # 用 ImageScaleBy（lanczos）回缩/放大到目标倍率，兑现"按所选倍率输出"。
    try:
        _s = float(scale)
    except (TypeError, ValueError):
        _s = 4.0
    if abs(_s - 4.0) > 1e-6:
        wf['fit'] = {'class_type': 'ImageScaleBy',
                     'inputs': {'image': ['up', 0], 'upscale_method': 'lanczos',
                                'scale_by': round(_s / 4.0, 4)}}
        wf['save'] = {'class_type': 'SaveImage', 'inputs': {'images': ['fit', 0],
                      'filename_prefix': UPSCALE_TOP + '/' + prefix}}
    else:
        wf['save'] = {'class_type': 'SaveImage', 'inputs': {'images': ['up', 0],
                      'filename_prefix': UPSCALE_TOP + '/' + prefix}}
    return wf, 'save', '.png'

# ---------------- 工作流：SeedVR2（图片） ----------------
def _wf_seedvr2_image(image, scale, prefix):
    wf = {}
    wf['unet'] = {'class_type': 'UNETLoader', 'inputs': {'unet_name': SEEDVR2_UNET, 'weight_dtype': 'default'}}
    wf['vae']  = {'class_type': 'VAELoader', 'inputs': {'vae_name': SEEDVR2_VAE}}
    wf['load'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
    wf['rsz']  = {'class_type': 'ResizeImageMaskNode', 'inputs': {'input': ['load', 0],
                  'resize_type': 'scale by multiplier', 'resize_type.multiplier': float(scale),
                  'scale_method': 'lanczos'}}
    wf['pre']  = {'class_type': 'SeedVR2Preprocess', 'inputs': {'resized_images': ['rsz', 0]}}
    wf['enc']  = {'class_type': 'VAEEncodeTiled', 'inputs': {'pixels': ['pre', 0], 'vae': ['vae', 0],
                  'tile_size': 512, 'overlap': 128, 'temporal_size': 64, 'temporal_overlap': 8}}
    wf['cond'] = {'class_type': 'SeedVR2Conditioning', 'inputs': {'model': ['unet', 0],
                  'vae_conditioning': ['enc', 0]}}
    wf['ks']   = {'class_type': 'KSampler', 'inputs': {'model': ['unet', 0], 'positive': ['cond', 0],
                  'negative': ['cond', 1], 'latent_image': ['enc', 0],
                  'seed': int(time.time()) % 2147483647, 'control_after_generate': 'fixed',
                  'steps': 1, 'cfg': 1.0, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}}
    wf['dec']  = {'class_type': 'VAEDecodeTiled', 'inputs': {'samples': ['ks', 0], 'vae': ['vae', 0],
                  'tile_size': 512, 'overlap': 128, 'temporal_size': 64, 'temporal_overlap': 8}}
    wf['post'] = {'class_type': 'SeedVR2PostProcessing', 'inputs': {'images': ['dec', 0],
                  'original_resized_images': ['rsz', 0], 'color_correction_method': 'lab'}}
    wf['save'] = {'class_type': 'SaveImage', 'inputs': {'images': ['post', 0],
                  'filename_prefix': UPSCALE_TOP + '/' + prefix}}
    return wf, 'save', '.png'

# ---------------- 工作流：SeedVR2（视频） ----------------
def _wf_seedvr2_video(video, scale, prefix):
    wf = {}
    wf['unet'] = {'class_type': 'UNETLoader', 'inputs': {'unet_name': SEEDVR2_UNET, 'weight_dtype': 'default'}}
    wf['vae']  = {'class_type': 'VAELoader', 'inputs': {'vae_name': SEEDVR2_VAE}}
    wf['load'] = {'class_type': 'LoadVideo', 'inputs': {'file': video}}
    wf['comp'] = {'class_type': 'GetVideoComponents', 'inputs': {'video': ['load', 0]}}
    wf['rsz']  = {'class_type': 'ResizeImageMaskNode', 'inputs': {'input': ['comp', 0],
                  'resize_type': 'scale by multiplier', 'resize_type.multiplier': float(scale),
                  'scale_method': 'lanczos'}}
    wf['pre']  = {'class_type': 'SeedVR2Preprocess', 'inputs': {'resized_images': ['rsz', 0]}}
    wf['enc']  = {'class_type': 'VAEEncodeTiled', 'inputs': {'pixels': ['pre', 0], 'vae': ['vae', 0],
                  'tile_size': 512, 'overlap': 128, 'temporal_size': 64, 'temporal_overlap': 8}}
    wf['cond'] = {'class_type': 'SeedVR2Conditioning', 'inputs': {'model': ['unet', 0],
                  'vae_conditioning': ['enc', 0]}}
    wf['ks']   = {'class_type': 'KSampler', 'inputs': {'model': ['unet', 0], 'positive': ['cond', 0],
                  'negative': ['cond', 1], 'latent_image': ['enc', 0],
                  'seed': int(time.time()) % 2147483647, 'control_after_generate': 'fixed',
                  'steps': 1, 'cfg': 1.0, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}}
    wf['dec']  = {'class_type': 'VAEDecodeTiled', 'inputs': {'samples': ['ks', 0], 'vae': ['vae', 0],
                  'tile_size': 512, 'overlap': 128, 'temporal_size': 64, 'temporal_overlap': 8}}
    wf['post'] = {'class_type': 'SeedVR2PostProcessing', 'inputs': {'images': ['dec', 0],
                  'original_resized_images': ['rsz', 0], 'color_correction_method': 'none'}}
    wf['cv']   = {'class_type': 'CreateVideo', 'inputs': {'images': ['post', 0],
                  'fps': ['comp', 2], 'audio': ['comp', 1]}}
    wf['save'] = {'class_type': 'SaveVideo', 'inputs': {'video': ['cv', 0],
                  'filename_prefix': UPSCALE_TOP + '/' + prefix,
                  'format': 'auto', 'codec': 'auto'}}
    return wf, 'save', '.mp4'

# ---------------- 工作流：VOSR2（图片） ----------------
def _vosr2_loader_settings(wf):
    wf['loader'] = {'class_type': 'TESpeedVOSR2Loader', 'inputs': {
                    'model_bundle': 'VOSR2', 'precision': 'auto', 'memory_policy': 'auto'}}
    wf['settings'] = {'class_type': 'TESpeedVOSR2Settings', 'inputs': {
                    'quality_profile': 'speed', 'tile_strategy': 'auto', 'tile_size': 512,
                    'tile_overlap': 32, 'vae_tile_size': 1024, 'vae_tile_overlap': 32,
                    'image_batch': 1, 'frame_batch': 2, 'dino_batch': 2, 'temporal_cache': False,
                    'cache_threshold': 0.003, 'cache_refresh': 4, 'memory_policy': 'auto',
                    'color_alignment': 'wavelet'}}

def _wf_vosr2_image(image, scale, prefix):
    wf = {}
    wf['load'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
    _vosr2_loader_settings(wf)
    wf['up'] = {'class_type': 'TESpeedVOSR2Image', 'inputs': {
                'model': ['loader', 0], 'images': ['load', 0], 'scale': int(scale),
                'seed': 666, 'settings': ['settings', 0]}}
    wf['save'] = {'class_type': 'SaveImage', 'inputs': {'images': ['up', 0],
                  'filename_prefix': UPSCALE_TOP + '/' + prefix}}
    return wf, 'save', '.png'

# ---------------- 工作流：VOSR2（视频） ----------------
def _wf_vosr2_video(video, scale, prefix):
    wf = {}
    wf['load'] = {'class_type': 'LoadVideo', 'inputs': {'file': video}}
    wf['comp'] = {'class_type': 'GetVideoComponents', 'inputs': {'video': ['load', 0]}}
    _vosr2_loader_settings(wf)
    wf['up'] = {'class_type': 'TESpeedVOSR2Video', 'inputs': {
                'model': ['loader', 0], 'images': ['comp', 0], 'scale': int(scale),
                'seed': 666, 'settings': ['settings', 0]}}
    wf['cv']   = {'class_type': 'CreateVideo', 'inputs': {'images': ['up', 0],
                  'fps': ['comp', 2], 'audio': ['comp', 1]}}
    wf['save'] = {'class_type': 'SaveVideo', 'inputs': {'video': ['cv', 0],
                  'filename_prefix': UPSCALE_TOP + '/' + prefix,
                  'format': 'auto', 'codec': 'auto'}}
    return wf, 'save', '.mp4'

def build_wf_upscale(media, engine, scale=2, kind='image', prefix='panel', esrgan_model=None):
    if kind == 'video':
        if engine == 'seedvr2': return _wf_seedvr2_video(media, scale, prefix)
        if engine == 'vosr2':   return _wf_vosr2_video(media, scale, prefix)
        return None
    if engine == 'esrgan':  return _wf_esrgan_image(media, esrgan_model, prefix, scale)
    if engine == 'seedvr2': return _wf_seedvr2_image(media, scale, prefix)
    if engine == 'vosr2':   return _wf_vosr2_image(media, scale, prefix)
    return None

def cf_upscale(p):
    kind = str(p.get('kind', 'image'))
    engine = str(p.get('engine', 'seedvr2'))
    # X6：参数校验前置（类型/引擎白名单闭环），非法立即返回，不触发引擎拉起
    if kind not in ('image', 'video'):
        return dict(ok=False, error='unknown kind: %r' % kind)
    if engine not in ('esrgan', 'seedvr2', 'vosr2'):
        return dict(ok=False, error='unknown engine: %r' % engine)
    try:
        scale = int(p.get('scale', 2))
    except (TypeError, ValueError):
        return dict(ok=False, error='scale must be integer')
    if not 1 <= scale <= 10:
        return dict(ok=False, error='scale must be 1-10')
    esrgan_model = str(p.get('model', ''))
    if kind == 'video':
        media0 = str(p.get('video', '')).strip()
        if not media0:
            return dict(ok=False, code='no_video', msg='请选择要放大的输入视频')
        media = _li_ref(media0)
    else:
        media0 = str(p.get('image', '')).strip()
        if not media0:
            return dict(ok=False, code='no_image', msg='请选择要放大的输入图片')
        media = _li_ref(media0)
    if engine == 'esrgan' and esrgan_model not in upscale_model_files():
        return dict(ok=False, code='no_model', msg='未找到选定的 ESRGAN 模型：' + esrgan_model)
    if engine == 'seedvr2' and not seedvr2_ready():
        return dict(ok=False, code='missing_engine', msg='SeedVR2 模型缺失，请先下载')
    if engine == 'vosr2' and not vosr2_ready():
        return dict(ok=False, code='missing_engine', msg='VOSR2 模型缺失，请先下载')
    if not free_vram():
        return dict(ok=False, code='vram_busy', msg='LLM 显存未释放，已中止')
    if not port_listening(COMFY_PORT):
        cf_start()
    for _ in range(45):
        time.sleep(2)
        if cf_ready_now(): break
    else:
        return dict(ok=False, code='comfy_down', msg='ComfyUI 未就绪')
    try:
        wf, outkey, ext = build_wf_upscale(media, engine, scale, kind,
                                          str(p.get('prefix', 'panel')), esrgan_model)
        req = urllib.request.Request('http://127.0.0.1:%d/prompt' % COMFY_PORT,
              data=json.dumps({'prompt': wf}).encode(), headers={'Content-Type': 'application/json'})
        pid = json.load(urllib.request.urlopen(req, timeout=15))['prompt_id']
    except urllib.error.HTTPError as e:
        return dict(ok=False, code='submit_fail', msg='ComfyUI 拒绝工作流：' + e.read().decode()[:400])
    except Exception as e:
        return dict(ok=False, code='submit_fail', msg='提交失败：' + str(e)[:200])
    t0 = time.time(); fn = None; _lost = 0
    for _ in range(300):
        time.sleep(2)
        try:
            hist = json.load(cf_http('/history/' + pid, timeout=8))
            _lost = 0
        except Exception:
            # R07：原 `continue` 在 ComfyUI 被杀后空转到 600 秒才报「超时或无输出」。
            # 连续 3 次取不到即判离线，立刻给出原因与做法。
            _lost += 1
            if _lost >= 3:
                return dict(ok=False, code='comfy_lost',
                            msg='放大中断：ComfyUI 连接失败（已离线或被关闭）。'
                                '请到「状态」页点「启动 ComfyUI」，等状态变在线后重试')
            continue
        if pid in hist:
            st = hist[pid].get('status', {})
            if st.get('status_str') == 'error':
                return dict(ok=False, code='run_error', msg='放大运行出错，详见 ComfyUI 控制台')
            out = hist[pid]['outputs'].get(outkey, {})
            if isinstance(out, dict):
                for lst in out.values():
                    if isinstance(lst, list) and lst and isinstance(lst[0], dict) and lst[0].get('filename'):
                        it = lst[0]
                        fn = ((it.get('subfolder','') + '/') if it.get('subfolder') else '') + it['filename']
                        break
            break
    if not fn:
        return dict(ok=False, code='timeout', msg='放大超时或无输出')
    return dict(ok=True, msg='放大完成', file=fn, url='/output/' + fn, ext=ext,
                elapsed=round(time.time()-t0,1), prompt_id=pid)

def free_vram():
    # 目的：确保 LLM（KVMem / LM Studio 模型）不占显存，好让 ComfyUI 生图。
    # 判定只看 LLM 服务状态：全局显存阈值会把 ComfyUI 自身模型、LM Studio 后台
    # 服务外壳误判成 LLM；也不要对空转的 LM 服务发 unload（会唤醒它启动 server）。
    if kv_alive():
        kv_stop()
        for _ in range(15):
            if not kv_alive():
                break
            time.sleep(2)
    # 仅当 LM Studio ML server 真正在跑（监听 1234 / 有 llama-server）时才停
    if lms_running():
        lms_stop()
        for _ in range(10):
            if not lms_running():
                break
            time.sleep(2)
    return (not kv_alive()) and (not lms_running())

def cf_generate(p):
    variant = str(p.get('variant', 'int8'))
    # X6：参数校验前置——未知 variant 显式拒绝（白名单闭环），不静默回退
    if variant not in ('int8', 'uc-gguf', 'h3'):
        return dict(ok=False, error='unknown variant: %r' % variant)
    prompt = str(p.get('prompt', '')).strip()
    if not prompt:
        return dict(ok=False, error='prompt must be non-empty string')
    try:
        w = int(p.get('width', 1024)); h = int(p.get('height', 1024))
    except (TypeError, ValueError):
        return dict(ok=False, error='width/height must be integers')
    if not (64 <= w <= 2048) or not (64 <= h <= 2048):
        return dict(ok=False, error='width/height must be 64-2048')
    try:
        steps = int(p.get('steps', 20))
    except (TypeError, ValueError):
        return dict(ok=False, error='steps must be integer')
    if not 1 <= steps <= 200:
        return dict(ok=False, error='steps must be 1-200')
    try:
        cfg = float(p.get('cfg', 1.0))
    except (TypeError, ValueError):
        return dict(ok=False, error='cfg must be number')
    if cfg < 0 or cfg > 32:
        return dict(ok=False, error='cfg must be 0-32')
    try:
        seed = int(p.get('seed', int(time.time()) % 2147483647))
    except (TypeError, ValueError):
        return dict(ok=False, error='seed must be integer')
    if variant == 'h3':
        try:
            seconds = float(p.get('seconds', 5))
        except (TypeError, ValueError):
            return dict(ok=False, error='seconds must be number')
        if not 1 <= seconds <= 30:
            return dict(ok=False, error='seconds must be 1-30')
    present = qi_present(variant)
    if not all(present.values()):
        miss = [k for k, v in present.items() if not v]
        return dict(ok=False, code='no_model', msg='生图模型未就绪，缺少：' + '；'.join(miss))
    if not free_vram():
        return dict(ok=False, code='vram_busy', msg='LLM 显存未释放，已中止（未生图）')
    if not port_listening(COMFY_PORT):
        cf_start()
    for _ in range(45):
        time.sleep(2)
        if cf_ready_now(): break
    else:
        return dict(ok=False, code='comfy_down', msg='ComfyUI 未就绪')
    try:
        length = None
        if variant == 'h3':
            wf, length = build_wf_h3(str(p.get('prompt', '')), w, h,
                                     float(p.get('seconds', 5)), seed,
                                     str(p.get('prefix', 'H3')),
                                     p.get('first_frame'), p.get('last_frame'))
            outkey = 'save'; ext = '.mp4'
        elif variant == 'uc-gguf':
            wf = build_wf_gguf(str(p.get('prompt', '')), str(p.get('negative', 'low quality, blurry, deformed, watermark, text')),
                          w, h, steps, cfg, seed)
            outkey = 'save'; ext = '.png'
        else:
            wf = build_wf(str(p.get('prompt', '')), str(p.get('negative', '低质量，模糊，变形，水印，文字')),
                          w, h, steps, cfg, seed)
            outkey = 'save'; ext = '.png'
        req = urllib.request.Request('http://127.0.0.1:%d/prompt' % COMFY_PORT,
              data=json.dumps({'prompt': wf}).encode(), headers={'Content-Type': 'application/json'})
        pid = json.load(urllib.request.urlopen(req, timeout=15))['prompt_id']
        t0 = time.time(); fn = None; run_error = None
        wait_loops = 240 if variant == 'h3' else 180
        _lost = 0
        for _ in range(wait_loops):
            time.sleep(2)
            # R07：ComfyUI 中途被关掉/重启时 /history 直接连接失败。原来异常冒到 do_POST
            # 只回 {'error':'internal error'}，界面显示「失败：internal error」——用户看不懂。
            # 连续 3 次（约 6 秒）取不到就判定断连，返回可操作的中文原因。
            try:
                hist = json.load(cf_http('/history/' + pid, timeout=8))
                _lost = 0
            except Exception:
                _lost += 1
                if _lost >= 3:
                    _msg = ('生成中断：ComfyUI 连接失败（已离线或被关闭）。'
                            '请到「状态」页点「启动 ComfyUI」，等状态变在线后重新生成')
                    record_job(dict(kind=variant, status='error',
                                    elapsed=round(time.time() - t0, 1), msg=_msg,
                                    params=dict(width=w, height=h, steps=steps, cfg=cfg)))
                    return dict(ok=False, code='comfy_lost', msg=_msg)
                continue
            if pid in hist:
                _st = hist[pid].get('status', {})
                if _st.get('status_str') == 'error':
                    _err = ''
                    for _m in _st.get('messages', []):
                        if isinstance(_m, (list, tuple)) and len(_m) >= 2 and 'error' in str(_m[0]).lower():
                            _err = str(_m[1])[:200]; break
                    run_error = _err or 'ComfyUI 运行错误（详见控制台）'
                    break
                out = hist[pid]['outputs'].get(outkey, {})
                # SaveImage → {'images':[{filename}]}；SaveVideo → {'video':[{filename}]}
                def _rel_of(info):
                    return ((info.get('subfolder', '') + '/') if info.get('subfolder') else '') + info.get('filename', '')
                if isinstance(out, dict):
                    for lst in out.values():
                        if lst and lst[0].get('filename'):
                            fn = _rel_of(lst[0]); break
                elif isinstance(out, list) and out and out[0].get('filename'):
                    fn = _rel_of(out[0])
                if fn:
                    break
        elapsed = round(time.time() - t0, 1)
        if variant == 'h3':
            pparams = dict(width=w, height=h, steps=steps, cfg=cfg,
                          seconds=float(p.get('seconds', 5)), length=length,
                          mode=('i2v' if p.get('first_frame') else 't2v'))
        else:
            pparams = dict(width=w, height=h, steps=steps, cfg=cfg)
        if run_error:
            record_job(dict(kind=variant, status='error', elapsed=elapsed, msg=run_error, params=dict(pparams)))
            return dict(ok=False, code='run_error', msg=run_error)
        if not fn:
            record_job(dict(kind=variant, status='timeout', elapsed=elapsed, params=dict(pparams)))
            return dict(ok=False, msg='生成超时')
        rec = dict(kind=variant, status='success', file=fn, elapsed=elapsed, params=pparams)
        record_job(rec)
        return dict(ok=True, file=fn, url='/output/' + fn, elapsed=elapsed, params=pparams)
    except Exception as e:
        record_job(dict(kind=variant, status='error', msg=str(e)))
        return dict(ok=False, msg='生成失败：' + str(e))

# ============================ 生成记录自动补录（任何途径触发） =========================
RECON_T0 = time.time() - 60.0   # 60s 缓冲：捕获启动瞬间刚完成/在跑的任务
def _baseline_files():
    out=set()
    if os.path.isdir(COMFY_OUT):
        for root,_,files in os.walk(COMFY_OUT):
            for f in files:
                out.add(os.path.relpath(os.path.join(root,f),COMFY_OUT).replace('\\','/'))
    return out
BASELINE_FILES=_baseline_files()

def _wf_params(wf):
    p={}
    for n in wf.values():
        if not isinstance(n,dict): continue
        ct=n.get('class_type'); ins=n.get('inputs',{}) or {}
        if ct in ('EmptyLatentImage','EmptySD3LatentImage','EmptyMiniMaxH3LatentAV','EmptyHunyuanLatentVideo'):
            if ins.get('width'):p['width']=ins['width']
            if ins.get('height'):p['height']=ins['height']
        if ct in ('KSampler','KSamplerAdvanced','MiniMaxChunkFeedForward'):
            if ins.get('steps'):p['steps']=ins['steps']
            if ins.get('cfg') is not None:p['cfg']=ins.get('cfg')
        if ct and 'MiniMax' in ct:
            for kk in ('length','num_frames','seconds'):
                if ins.get(kk) is not None:p[kk]=ins[kk]
    return p

def _hist_files(outputs):
    res=[]
    for o in (outputs or {}).values():
        if isinstance(o,dict):
            for key in ('images','gifs','videos','video'):
                for info in (o.get(key) or []):
                    if isinstance(info,dict) and info.get('filename'): res.append(info)
    return res

def _hist_ts(item,ev):
    try:
        for m in item.get('status',{}).get('messages',[]):
            if isinstance(m,list) and m[0]==ev and len(m)>=3 and isinstance(m[2],dict) and 'timestamp' in m[2]:
                return m[2]['timestamp']
    except Exception: pass
    return None

def reconcile_jobs():
    existing={j.get('file') for j in jl_read_recent(JOBS_JL,2000) if j.get('file')}
    # 1) ComfyUI /history（权威，含参数与时间戳）
    try: H=json.load(cf_http('/history?max_items=300',timeout=8))
    except Exception: H={}
    for pid,item in (H or {}).items():
        try:
            pwf=item.get('prompt'); wf=pwf[2] if isinstance(pwf,list) and len(pwf)>2 else pwf
            if not isinstance(wf,dict): continue
            end=_hist_ts(item,'execution_success') or _hist_ts(item,'execution_error')
            wall=(end/1000.0) if end else time.time()
            if wall < RECON_T0: continue    # 启动前的旧任务，不回填
            kind=_queue_kind(wf); params=_wf_params(wf)
            st=_hist_ts(item,'execution_start')
            el=round((end-st)/1000.0,1) if (st and end) else None
            for info in _hist_files(item.get('outputs')):
                rel=((info.get('subfolder','')+'/') if info.get('subfolder') else '')+info['filename']
                if rel in existing: continue
                existing.add(rel)
                record_job(dict(kind=kind,status='success',file=rel,elapsed=el,params=params,
                                source='history',prompt_id=pid,wall=round(wall,2)))
        except Exception: continue
    # 2) 文件夹新增兜底（history 被清空/重启也能发现；无参数无耗时）
    for top,kind in (('photo','image'),('video','video')):
        d=os.path.join(COMFY_OUT,top)
        if not os.path.isdir(d): continue
        for root,_,files in os.walk(d):
            for f in files:
                full=os.path.join(root,f)
                rel=os.path.relpath(full,COMFY_OUT).replace('\\','/')
                if rel in BASELINE_FILES or rel in existing: continue
                if os.path.getmtime(full) < RECON_T0: continue
                existing.add(rel)
                record_job(dict(kind=kind,status='success',file=rel,source='folder-watch',
                                wall=round(os.path.getmtime(full),2)))

def reconcile_loop():
    while True:
        # R10（loopslow）：每次 reconcile 要读 2000 条台账 + 拉 ComfyUI /history + 扫目录，
        # 12 s 一次在稳态里是纯背景开销；90 s 对台账回填没有可感知影响。
        # R11（slow3）：300 s。作品墙不依赖回填（/api/outputs 实时扫目录），
        # 所以界面不会「卡住不动」；只有生成记录台账的回填最多晚 5 分钟。
        time.sleep(300 if 'slow3' in XH_R11
                   else (90 if 'loopslow' in XH_R10 else 12))
        try:
            if cf_alive(): reconcile_jobs()
        except Exception: pass
threading.Thread(target=reconcile_loop, daemon=True, name='xh_recon').start()

# ============================ 快速基准 ============================
def bench():
    if not port_listening(MODEL_PORT):
        return dict(ok=False, msg='1234 无服务')
    try:
        mid = json.load(urllib.request.urlopen(
            'http://127.0.0.1:%d/v1/models' % MODEL_PORT, timeout=4))['data'][0]['id']
    except Exception:
        mid = ''
    body = json.dumps(dict(model=mid, stream=True, max_tokens=128, temperature=0,
                           messages=[dict(role='user', content='用100字介绍北京的秋天。')])).encode()
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:%d/v1/chat/completions' % MODEL_PORT, data=body,
            headers={'Content-Type': 'application/json'})
        t0 = time.time(); first = None; chunks = 0
        resp = urllib.request.urlopen(req, timeout=120)
        for raw in iter(resp.readline, b''):
            line = raw.decode('utf-8', 'replace').strip()
            if line.startswith('data:') and first is None:
                first = time.time() - t0
            if line.startswith('data:') and '"content"' in line:
                chunks += 1
        total = time.time() - t0
        gen_t = max(total - (first or 0), 0.001)
        return dict(ok=True, ttft=round(first or 0, 2), total=round(total, 1),
                    genTokS=round(128 / gen_t, 1), contentChunks=chunks,
                    note='genTokS 按128上限估算，提前结束会偏高')
    except Exception as e:
        return dict(ok=False, msg='基准失败：' + str(e))

# ============================ 健康检查 / 智能建议 ============================
def dir_size(path):
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try: total += os.path.getsize(os.path.join(root, f))
                except Exception: pass
    except Exception: pass
    return total

def route_advice(ctx_tokens=None):
    if ctx_tokens is None:
        return '填入本次上下文 token 数，自动给出路线建议。'
    c = int(ctx_tokens)
    if c <= 32000:
        return '≤32K：走 LM Studio 原生路线，速度与质量最稳。'
    if c <= 48000:
        return '32–48K：LM Studio 原生 + KV q4_0，可流畅运行。'
    return '>48K：切 KVMem 路线（工作集 4K 起步），速度不随总长度塌方；长文档先做摘要。'

# R38：告警阈值。健康页（health）与隐藏态告警通道（alerts）共用同一组数字，
# 避免两处各写各的。ram 例外：健康页 80% 记 warn 只是提示，托盘通知要到 90% 才弹。
ALERT_TH = dict(temp_warn=75, temp_err=85, vram_warn=70, vram_err=85, ram_warn=90)


def health(ctx_tokens=None):
    g = gpu_info(); d = disk_info(); r = ram_info()
    checks = []
    def add(name, status, msg): checks.append(dict(name=name, status=status, msg=msg))
    t = g['temp']
    add('GPU 温度', 'ok' if t < ALERT_TH['temp_warn'] else 'warn' if t < ALERT_TH['temp_err'] else 'err',
        str(round(t)) + '℃' + ('（正常）' if t < ALERT_TH['temp_warn'] else '（偏高，注意散热）' if t < ALERT_TH['temp_err'] else '（危险）'))
    mp = g['memUsed'] / max(g['memTotal'], 1) * 100
    add('显存占用', 'ok' if mp < ALERT_TH['vram_warn'] else 'warn' if mp < ALERT_TH['vram_err'] else 'err',
        str(round(g['memUsed'])) + ' / ' + str(round(g['memTotal'])) + ' MiB（' + str(round(mp)) + '%）')
    add('C 盘剩余', 'ok' if d['freeGB'] > 80 else 'warn' if d['freeGB'] > 30 else 'err',
        str(d['freeGB']) + ' GB 可用')
    used_ram = (r['total'] - r['avail']) / 1e9
    add('系统内存', 'ok' if used_ram / (r['total']/1e9) < .8 else 'warn',
        str(round(used_ram)) + ' / ' + str(round(r['total']/1e9)) + ' GB')
    pl = port_listening(MODEL_PORT)
    # X9：核心推理服务离线改记 warn（-12），不再记中性 info
    add('1234 LLM 服务', 'ok' if pl else 'warn', '运行中' if pl else '未启动')
    pc = port_listening(COMFY_PORT)
    add('8189 ComfyUI', 'ok' if pc else 'warn', '在线' if pc else '离线')
    pp = port_listening(PANEL_PORT)
    add('8899 面板服务', 'ok' if pp else 'err', '在线' if pp else '异常（面板不可达）')
    w = sum(1 for c in checks if c['status'] == 'warn')
    e = sum(1 for c in checks if c['status'] == 'err')
    score = max(0, 100 - w * 12 - e * 30)
    storage = [('数据 data', dir_size(DATA)), ('日志 logs', dir_size(LOGDIR)),
               ('输出 output', dir_size(COMFY_OUT))]
    return dict(score=score, checks=checks,
                storage=[dict(name=n, bytes=b, mb=round(b/1e6, 1)) for n, b in storage],
                advice=route_advice(ctx_tokens))

# ============================ 状态聚合 ============================
_STATE_C = {'t': 0.0, 'v': None}

# R11（XH-071）：分层缓存。改前 state() 每次重建都同步跑 tasklist/netstat 子进程
# （实测 cold 647 ms，p95 762 ms）。拆三层后：
#   svc  2 s —— 引擎/ComfyUI 活态、忙闲、端口（改前最贵的一段，现读内核表 <3 ms）
#   io   8 s —— 输出清单、模型文件存在性（作品墙/芯片对 8 s 陈旧无感）
#   cfg 30 s —— 配置档案（任何 POST 成功都会 cache_clear 立即失效）
# 整体响应仍保留 3 s 缓存，避免同一拍内重复序列化。
_ST_LAYERS = dict(
    svc=dict(t=0.0, v=None, ttl=2.0),
    io=dict(t=0.0, v=None, ttl=8.0),
    cfg=dict(t=0.0, v=None, ttl=30.0),
)


def _layer(name, fn):
    e = _ST_LAYERS[name]
    now = time.time()
    if e['v'] is not None and now - e['t'] < e['ttl']:
        return e['v']
    v = fn()
    e.update(t=now, v=v)
    return v


def _st_cfg():
    profs = []
    for p in load_cfg():
        q = _resolve_prof(p)
        q['running'] = bool(KV['id'] and KV['id'] == q['id'])
        q['ready'] = bool(q['running'] and KV['ready'])
        q['fileExists'] = os.path.exists(q.get('model', ''))
        # X8：模型路径脱敏——只下发 basename + fileExists，绝对路径不下发
        q['model'] = os.path.basename(str(q.get('model', '')))
        profs.append(q)
    return profs


def _st_io():
    return dict(present=qi_present('int8'), presentUC=qi_present('uc-gguf'),
                presentH3=qi_present('h3'),
                outputs=cf_outputs(), videos=cf_video_outputs())


_LMS_EDGE = {'running': None}


def _st_svc():
    alive = kv_alive()
    _cfon = cf_alive()
    run = lms_running()
    # R11：LM Studio 由外部拉起时，120 s 后台巡检会让「已加载模型」最多陈旧 2 分钟。
    # 这里做边沿触发——一旦看到 running 由无到有，立刻唤醒后台刷新（最多等一拍 /api/state）。
    _prev = _LMS_EDGE.get('running')
    _LMS_EDGE['running'] = run
    if _prev is not None and run != _prev:
        # R11：外部启动/停止 LM Studio 时，不等 120 s 巡检——边沿一到就唤醒后台刷新，
        # 保证「已加载模型」不会在界面上看起来卡住不动（最多差一拍 /api/state）。
        LMS_REFRESH.set()
    io = _layer('io', _st_io)
    return dict(
        kv=dict(id=KV['id'], alive=alive, ready=KV['ready'], msg=KV['msg'],
                starting=KV.get('starting')),
        lms=dict(running=run, loaded=lms_ps(), list=lms_ls()),
        comfy=dict(alive=_cfon, ready=CF['ready'], msg=CF['msg'],
                   busy=(cf_busy() if _cfon else False),
                   kind=(cf_kind() if _cfon else None), **io),
        port1234=port_listening(MODEL_PORT),
        proxy=dict(running=proxy_running(), port=PROXY_PORT),
    )


def state():
    # R02（25 轮）：实测 /api/state 单次 562 ms，前端每 4 s 拉一次即占单核约 14%，
    # 且单线程 HTTP 期间会阻塞其它请求。R11：改分层缓存后冷重建 <3 ms。
    _now = time.time()
    if _STATE_C['v'] is not None and _now - _STATE_C['t'] < 3.0:
        return _STATE_C['v']
    svc = _layer('svc', _st_svc)
    profs = _layer('cfg', _st_cfg)
    # running/ready 依赖实时 KV 状态，不能吃 30 s 的 cfg 缓存
    alive = svc['kv']['alive']
    for q in profs:
        q['running'] = bool(alive and KV['id'] == q['id'])
        q['ready'] = bool(q['running'] and KV['ready'])
    res = dict(profiles=profs, settings=PANEL_SET, sys=SYS, kvOpts=KV_OPTS,
               adapt=LAST_ADAPT, **svc)
    _STATE_C.update(t=_now, v=res)
    return res

def metrics(since=None):
    with HIST_LOCK:
        h = list(HIST)
        if h and 'wall' in h[0]:
            base = min(x.get('wall', 0) for x in h if 'wall' in x)
            for x in h:
                if 'wall' in x:
                    x['ts'] = round(x['wall'] - base, 1)
        if since is not None:
            h = [x for x in h
                 if float(x.get('wall') or x.get('ts') or 0) > since]
        return dict(sys=SYS, history=h)

def alerts():
    """R38：隐藏态告警通道。只读最近一条采样，不扫目录、不算健康分、不探端口。

    服务离线不在此列——用户在托盘常主动停服，弹窗会变成噪音，留给健康页。
    ts 是这条采样的 wall（秒），前端用它算「采样→弹窗」的端到端延迟。
    """
    with HIST_LOCK:
        row = HIST[-1] if HIST else None
    if not row:
        return dict(ok=True, ts=0.0, alerts=[])
    out = []
    t = float(row.get('temp') or 0)
    if t >= ALERT_TH['temp_err']:
        out.append(dict(key='temp', level='err', name='GPU 温度', msg='%d℃（危险）' % round(t)))
    elif t >= ALERT_TH['temp_warn']:
        out.append(dict(key='temp', level='warn', name='GPU 温度', msg='%d℃（偏高）' % round(t)))
    mt = float(row.get('memTotal') or 0); mu = float(row.get('memUsed') or 0)
    if mt > 0:
        mp = mu / mt * 100
        if mp >= ALERT_TH['vram_err']:
            out.append(dict(key='vram', level='err', name='显存占用', msg='%d%%（已满）' % round(mp)))
        elif mp >= ALERT_TH['vram_warn']:
            out.append(dict(key='vram', level='warn', name='显存占用', msg='%d%%' % round(mp)))
    rt = float(row.get('ramTotal') or 0); ru = float(row.get('ramUsed') or 0)
    if rt > 0:
        rp = ru / rt * 100
        if rp >= ALERT_TH['ram_warn']:
            out.append(dict(key='ram', level='warn', name='系统内存', msg='%d%%' % round(rp)))
    return dict(ok=True, ts=float(row.get('wall') or 0), alerts=out)


import csv, io as _io
def csv_metrics():
    rows = jl_read_recent(METRICS_JL, 50000)
    cols = ['wall','memUsed','memTotal','gpuUtil','memUtil','temp','power','powerLimit',
            'clkSm','clkMem','cpu','ramUsed','ramTotal']
    head = ['time'] + cols[1:]
    buf = _io.StringIO(); w = csv.writer(buf); w.writerow(head)
    for r in rows:
        import datetime as _dt
        tm = _dt.datetime.fromtimestamp(r.get('wall',0)).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([tm] + [r.get(c,'') for c in cols[1:]])
    return buf.getvalue()

def csv_jobs():
    rows = list_jobs(5000)
    head = ['time','kind','status','file','elapsed','width','height','steps','seconds','mode']
    buf = _io.StringIO(); w = csv.writer(buf); w.writerow(head)
    import datetime as _dt
    for r in rows:
        pm = r.get('params',{}) or {}
        tm = _dt.datetime.fromtimestamp(r.get('wall',0)).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([tm, r.get('kind',''), r.get('status',''), r.get('file',''),
                    r.get('elapsed',''), pm.get('width',''), pm.get('height',''),
                    pm.get('steps',''), pm.get('seconds',''), pm.get('mode','')])
    return buf.getvalue()

# ---------- 电量（Wh）分时段统计：读持久化全量 metrics.jsonl ----------
import datetime as _dt
# R33：能耗只累计物理有效的 power；越界样本（µW 混流等）判无效并计数，
# 不做猜测性换算（「不猜」原则），供条件自证读取命中数。
_ENERGY_DIAG = {'n': 0, 'invalid': 0}

def _energy_pts(since=None):
    rows = jl_read_recent(ENERGY_JL, ENERGY_HARD_CAP)
    if since is not None:
        rows = [r for r in rows if r.get('wall') and r['wall'] >= since]
    _ENERGY_DIAG['n'] = 0
    _ENERGY_DIAG['invalid'] = 0
    out = []
    for r in rows:
        if not r.get('wall'):
            continue
        try:
            p = float(r.get('power'))
        except (TypeError, ValueError):
            continue
        _ENERGY_DIAG['n'] += 1
        if not (0.0 <= p <= 1000.0):
            _ENERGY_DIAG['invalid'] += 1
            continue
        out.append(dict(r, power=p))
    return out

def _energy_integrate(seg):
    wh = 0.0
    for a, b in zip(seg, seg[1:]):
        d = b['wall'] - a['wall']
        if 0 < d < 600:  # 排除关机造成的异常大间隔
            wh += (a['power'] + b['power']) / 2.0 * d / 3600.0
    return wh

_ENERGY_CACHE = {}
def energy_stat(range_key, win_secs=600):
    _cacheable = range_key != 'session'
    _now = time.time()
    if _cacheable:
        _hit = _ENERGY_CACHE.get(range_key)
        if _hit and _now - _hit[0] < 30:
            return _hit[1]
    pts = _energy_pts()
    if not pts:
        return dict(ok=True, wh=0, dur=0, avg=0, start=None, n=0)
    last = pts[-1]['wall']; start0 = 0
    if range_key == 'today':
        x = _dt.datetime.fromtimestamp(last)
        start0 = _dt.datetime(x.year, x.month, x.day).timestamp()
    elif range_key == 'week':
        x = _dt.datetime.fromtimestamp(last); m = x - _dt.timedelta(days=x.weekday())
        start0 = _dt.datetime(m.year, m.month, m.day).timestamp()
    elif range_key == 'month':
        x = _dt.datetime.fromtimestamp(last)
        start0 = _dt.datetime(x.year, x.month, 1).timestamp()
    elif range_key == 'session':
        start0 = last - max(1, int(win_secs))
    seg = [r for r in pts if r['wall'] >= start0]
    wh = _energy_integrate(seg)
    gaps = [(b['wall'] - a['wall']) for a, b in zip(seg, seg[1:])]
    dur = sum(g for g in gaps if 0 < g < 600)  # 有效采样时长，排除关机间隔
    avg = (wh / dur * 3600.0) if dur > 0 else 0
    span = (seg[-1]['wall'] - seg[0]['wall']) if len(seg) > 1 else 0
    _res = dict(ok=True, wh=round(wh, 3), dur=round(dur, 1), avg=round(avg, 1),
                span=round(span, 1), start=seg[0]['wall'] if seg else None, n=len(seg),
                n_invalid=_ENERGY_DIAG['invalid'])
    if _cacheable:
        _ENERGY_CACHE[range_key] = (_now, _res)
    return _res

def csv_energy():
    pts = _energy_pts(); order = []; days = {}
    for r in pts:
        d = _dt.datetime.fromtimestamp(r['wall']).strftime('%Y-%m-%d')
        if d not in days: days[d] = []; order.append(d)
        days[d].append(r)
    buf = _io.StringIO(); w = csv.writer(buf)
    w.writerow(['date', 'energy_Wh', 'duration_s', 'avg_power_W', 'samples'])
    for d in order:
        seg = days[d]; wh = _energy_integrate(seg)
        gaps = [(b['wall'] - a['wall']) for a, b in zip(seg, seg[1:])]
        dur = sum(g for g in gaps if 0 < g < 600)
        avg = (wh / dur * 3600.0) if dur > 0 else 0
        w.writerow([d, round(wh, 3), round(dur, 1), round(avg, 1), len(seg)])
    return buf.getvalue()

# ============================ HTTP ============================
class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200, ctype='application/json; charset=utf-8'):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode('utf-8')
        # R17（共享后端性能修复）：本机 TCP loopback 实测单次 socket 写固定 10.5 ms
        # （26 B 与 2.6 KB 相同，UDP 仅 0.07 ms，说明是栈过滤而非载荷）。原实现
        # send_response/end_headers 写一次头 + 写一次体 = 21 ms/请求；合并为单次 write。
        # Content-Length 仍显式下发，Connection: close 显式声明，语义与原 HTTP/1.0 一致。
        try:
            phrase = self.responses[code][0]
        except Exception:
            phrase = 'OK'
        head = ('%s %d %s\r\nContent-Type: %s\r\nCache-Control: no-store\r\n'
                'Content-Length: %d\r\nConnection: close\r\n\r\n'
                % (self.protocol_version, code, phrase, ctype, len(body)))
        self.close_connection = True
        self.wfile.write(head.encode('latin-1') + body)

    def log_message(self, *a):
        pass

    # X5：异常统一网关——traceback 只写本地日志，响应一律结构化 JSON
    def _log_exc(self, tag, e):
        try:
            import traceback
            with open(os.path.join(LOGDIR, 'server_error.log'), 'a', encoding='utf-8') as f:
                f.write('[%s] %s\n%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'),
                                           tag, traceback.format_exc()))
        except Exception:
            pass

    def _api_token_ok(self, p):
        """X1：/api/* 除 /api/health 精确路径外均需本地 token（回环同源放行）。
        R15-X05：原前缀豁免过宽（/api/healthfake 可绕过），改精确匹配。"""
        if p.startswith('/api/') and p != '/api/health':
            return _has_token(self)
        return True

    def serve_file(self, path, ctype):
        try:
            with open(path, 'rb') as f:
                self._send(f.read(), ctype=ctype)
        except OSError:
            self._send(b'not found', 404, 'text/plain')

    def do_GET(self):
        try:
            p = self.path.split('?')[0]
            if not self._api_token_ok(p):
                self._send(dict(ok=False, error='unauthorized'), 401)
                return
            if p in ('/', '/index.html'):
                self.serve_file(os.path.join(HERE, 'index.html'), 'text/html; charset=utf-8')
            elif p.startswith('/vendor/'):
                # X25：realpath 双向守卫（编码穿越/符号链接均拒绝）
                _vroot = os.path.realpath(os.path.join(HERE, 'vendor'))
                _rel = urllib.parse.unquote(p[len('/vendor/'):].split('?')[0])
                _fp = os.path.realpath(os.path.normpath(os.path.join(_vroot, _rel)))
                if not _fp.startswith(_vroot + os.sep) \
                        or os.path.splitext(_fp)[1].lower() != '.js':
                    self._send(b'forbidden', 403, 'text/plain')
                else:
                    self.serve_file(_fp, 'application/javascript')
            elif p.startswith('/assets/'):
                # X25 + R15-X04：realpath 双向守卫限制在 assets 子目录内；
                # 扩展名白名单（仿 /vendor/），未知类型 403 而非 octet-stream 兜底
                _root = os.path.realpath(os.path.join(HERE, 'assets'))
                _fp = os.path.realpath(os.path.normpath(
                    os.path.join(HERE, urllib.parse.unquote(p.lstrip('/')))))
                if not _fp.startswith(_root + os.sep):
                    self._send(b'forbidden', 403, 'text/plain')
                    return
                _ext = os.path.splitext(_fp)[1].lower()
                _ct = {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.gif':'image/gif',
                       '.svg':'image/svg+xml','.webp':'image/webp','.js':'application/javascript',
                       '.css':'text/css','.woff2':'font/woff2','.woff':'font/woff'}.get(_ext)
                if _ct is None:
                    self._send(b'forbidden', 403, 'text/plain')
                    return
                self.serve_file(_fp, _ct)
            elif p.startswith('/output/'):
                # X25：realpath 双向守卫
                rel = urllib.parse.unquote(p[len('/output/'):].split('?')[0])
                _oroot = os.path.realpath(COMFY_OUT)
                full = os.path.realpath(os.path.normpath(os.path.join(_oroot, rel)))
                if not full.startswith(_oroot + os.sep):
                    self.send_error(403); return
                low = full.lower()
                ctype = ('video/mp4' if low.endswith(('.mp4', '.webm', '.mkv'))
                         else 'image/jpeg' if low.endswith(('.jpg', '.jpeg'))
                         else 'image/webp' if low.endswith('.webp') else 'image/png')
                self.serve_file(full, ctype)
            elif p == '/api/jobs':
                # R018-XH02：n 数值校验（原非法静默回落 100）
                try:
                    n = int(self.path.split('n=')[1].split('&')[0])
                except Exception:
                    self._send(dict(ok=False, error='invalid n'), code=400)
                    return
                if not (1 <= n <= 1000):
                    self._send(dict(ok=False, error='n out of range'), code=400)
                    return
                self._send(dict(jobs=list_jobs(n)))
            elif p == '/api/settings/get':
                # R15-X10：GET 读设置（此前仅 POST 路由注册，GET 落 404）
                self._send(dict(ok=True, settings=PANEL_SET))
            elif p == '/api/state':
                self._send(state())
            elif p == '/api/metrics':
                # R02（25 轮）：支持 since 增量拉取——首帧全量（约 290 KB），
                # 之后仅返回 ts 之后的新点（约数百字节），把 1 Hz 轮询的序列化开销降到可忽略。
                since = None
                if 'since=' in self.path:
                    try:
                        since = float(self.path.split('since=')[1].split('&')[0])
                    except Exception:
                        since = None
                self._send(metrics(since))
            elif p == '/api/log/kv':
                self._send(dict(ok=True, lines=kv_log_lines()))
            elif p == '/api/log/comfy':
                self._send(dict(ok=True, lines=comfy_log_lines()))
            elif p == '/api/outputs':
                try:
                    limit = int(self.path.split('limit=')[1].split('&')[0])
                except Exception:
                    limit = 100
                self._send(dict(ok=True, outputs=cf_all_outputs(limit)))
            elif p == '/api/fav':
                self._send(fav_info())
            elif p == '/api/disk':
                self._send(disk_info())
            elif p == '/api/alerts':
                # R38：隐藏态告警通道专用（约 300 B），不含曲线/作品/日志
                self._send(alerts())
            elif p == '/api/health':
                ctx = None
                if 'ctx=' in self.path:
                    try: ctx = int(self.path.split('ctx=')[1].split('&')[0])
                    except Exception: ctx = None
                self._send(health(ctx))
            elif p == '/api/comfy/upscale/list':
                self._send(dict(ok=True, models=upscale_model_files(), images=input_image_files(),
                                videos=input_video_files(), seedvr2=seedvr2_ready(), vosr2=vosr2_ready()))
            elif p == '/api/energy':
                # R018-XH01：range 白名单 + win 数值校验（原非法静默兜底/500）
                rk = self.path.split('range=')[1].split('&')[0] if 'range=' in self.path else 'all'
                if rk not in ('all', 'today', 'week', 'month', 'session'):
                    self._send(dict(ok=False, error='invalid range'), code=400)
                    return
                try:
                    win = int(self.path.split('win=')[1].split('&')[0]
                              if 'win=' in self.path else 600)
                except Exception:
                    self._send(dict(ok=False, error='invalid win'), code=400)
                    return
                if not (1 <= win <= 86400):
                    self._send(dict(ok=False, error='win out of range'), code=400)
                    return
                self._send(energy_stat(rk, win))
            elif p == '/api/export/energy.csv':
                self._send('﻿'.encode() + csv_energy().encode('utf-8'),
                           ctype='text/csv; charset=utf-8')
            elif p == '/api/export/metrics.csv':
                self._send('﻿'.encode() + csv_metrics().encode('utf-8'),
                           ctype='text/csv; charset=utf-8')
            elif p == '/api/export/jobs.csv':
                self._send('﻿'.encode() + csv_jobs().encode('utf-8'),
                           ctype='text/csv; charset=utf-8')
            else:
                self._send(dict(ok=False, msg='not found'), 404)
        except Exception as e:
            self._log_exc('GET ' + self.path, e)
            self._send(dict(ok=False, error='internal error'), 500)

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(n) if n else b'{}'
            p = self.path.split('?')[0]
            if not self._api_token_ok(p):
                self._send(dict(ok=False, error='unauthorized'), 401)
                return
            # X4：JSON 解析失败 / 非 dict body → 规范 400
            try:
                data = json.loads(raw.decode('utf-8') or '{}')
            except Exception:
                self._send(dict(ok=False, error='invalid JSON'), 400)
                return
            if not isinstance(data, dict):
                self._send(dict(ok=False, error='body must be JSON object'), 400)
                return
            routes = {
             '/api/kv/start': lambda: kv_start(data.get('id', '')),
             '/api/kv/stop': kv_stop,
             '/api/lms/start': lms_start,
             '/api/lms/stop': lms_stop,
             '/api/lms/kill': lms_kill,
             '/api/lms/load': lambda: lms_load(data.get('model', '')),
             '/api/lms/unload': lms_unload,
             '/api/comfy/start': cf_start,
             '/api/comfy/stop': cf_stop,
             '/api/comfy/free': cf_free,
             '/api/comfy/interrupt': cf_interrupt,
             '/api/comfy/generate': lambda: cf_generate(data),
             '/api/comfy/upscale': lambda: cf_upscale(data),
             '/api/settings/get': lambda: PANEL_SET,
             '/api/settings/save': lambda: save_settings(data),
             '/api/jobs': lambda: _jobs_route(data),
             '/api/fav/toggle': lambda: fav_toggle(data.get('name','')),
             '/api/outputs/delete': lambda: out_delete(
                 data.get('names') if isinstance(data.get('names'), list)
                 else ([data['name']] if data.get('name') else [])),
             '/api/bench': bench,
             '/api/save': lambda: save_profile(data),
             '/api/stopall': stop_all,
             '/api/proxy/start': lambda: dict(ok=ensure_proxy(), running=proxy_running(), msg='保活代理已启动' if proxy_running() else '保活代理启动失败，见 logs\\proxy_err.log'),
             '/api/proxy/stop': lambda: (stop_proxy(), dict(ok=True, running=proxy_running(), msg='保活代理已停止'))[-1],
            }
            fn = routes.get(p)
            if not fn:
                self._send(dict(ok=False, msg='not found'), 404)
                return
            res = fn()
            # X6/X5：校验类错误（带 error 键）映射 HTTP 400；
            # R020-M1+R021-M1：业务失败一律 400——ok=False（含仅 msg 键与 code 键两类）不再返回 200。
            # 内置客户端按 body ok 判定不受影响，第三方按状态码判断的调用方不再误判成功
            if isinstance(res, dict) and (res.get('ok') is False or 'error' in res):
                self._send(res, 400)
            else:
                cache_clear(); self._send(res, 200)
        except Exception as e:
            self._log_exc('POST ' + self.path, e)
            self._send(dict(ok=False, error='internal error'), 500)

    # X10：OPTIONS 不再 501（回环 CORS 预检）
    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header('Allow', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers',
                             'Content-Type, X-Local-Token')
            self.send_header('Access-Control-Max-Age', '86400')
            self.end_headers()
        except Exception as e:
            self._log_exc('OPTIONS', e)

def _jobs_route(data):
    """R15-X02：n 非法（非整数字面量）→ 400，不再 int() 崩溃 500。"""
    try:
        n = int(data.get('n', 100))
    except (TypeError, ValueError):
        return dict(ok=False, error='n must be integer')
    return dict(jobs=list_jobs(n))

def stop_all():
    kv_stop(); lms_unload()
    lms_kill()
    try:
        cf_stop()  # R1-X03：停止全部托管服务须含 ComfyUI（原漏停，与文案不符）
    except Exception as e:
        plog('stop_all cf_stop failed: %s' % e)
    stop_proxy()
    return dict(ok=True, msg='已停止 KVMem、LM Studio、ComfyUI 并释放显存')

def save_profile(data):
    pid = data.get('id'); cfgs = load_cfg()
    for p in cfgs:
        if p['id'] == pid:
            for k in ('alias', 'mmproj'):
                if k in data: p[k] = str(data[k])
            for k in ('ctx', 'n', 'budget', 'reserve', 'block', 'threads'):
                if k in data:
                    try: p[k] = int(data[k])
                    except Exception: pass
            for k in ('ctk', 'ctv'):
                if data.get(k) in KV_OPTS: p[k] = data[k]
    save_cfg(cfgs)
    return dict(ok=True, msg='已保存')

class _PanelHTTPServer(ThreadingHTTPServer):
    # R38-26：Windows 上 allow_reuse_address 等价 SO_REUSEPORT，第二个进程绑同一端口
    # 也会成功 → 双监听（实测 Python 3.14 / Win：True 时构造不抛错）。必须关掉。
    # 代价是进程刚退出的 TIME_WAIT 会挡住重启，用下面的有限重试窗口兜住。
    allow_reuse_address = False


BIND_RETRY_TOTAL = 10.0   # 秒；重试窗口
BIND_RETRY_STEP = 0.25    # 秒；单次间隔


def _bind_panel(retry_total=BIND_RETRY_TOTAL, retry_step=BIND_RETRY_STEP):
    """在有限窗口内重试绑定；耗尽则抛 OSError，绝不退回双监听。"""
    t0 = time.time()
    tries = 0
    while True:
        tries += 1
        try:
            return _PanelHTTPServer(('127.0.0.1', PANEL_PORT), H)
        except OSError as exc:
            left = retry_total - (time.time() - t0)
            if left <= 0:
                plog('面板端口 %d 绑定失败（%d 次重试 / %.1fs 窗口）：%s；放弃启动后端'
                     % (PANEL_PORT, tries, retry_total, exc))
                raise
            if tries == 1:
                plog('面板端口 %d 被占用（%s），进入 %.1fs 重试窗口'
                     % (PANEL_PORT, exc, retry_total))
            time.sleep(min(retry_step, left))


def main():
    srv = _bind_panel()
    if '--no-open' not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open('http://127.0.0.1:%d/' % PANEL_PORT)).start()
    srv.serve_forever()

if __name__ == '__main__':
    try:
        main()
    except OSError:
        webbrowser.open('http://127.0.0.1:%d/' % PANEL_PORT)
