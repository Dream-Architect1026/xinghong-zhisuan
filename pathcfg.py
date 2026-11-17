# -*- coding: utf-8 -*-
"""本地路径配置（仓库内不含任何开发机绝对路径）。

取值优先级：环境变量 XH_<KEY> > %LOCALAPPDATA%\\XingHongPanel\\paths.json > 中性默认值。
开发机把本机真实路径写进 paths.json 即可，行为与旧版硬编码一致；
公开发布版不携带该文件。

可配置键（环境变量名即 XH_ + 大写键名）：
  kvmem_bin         KVMem 引擎可执行文件
  models_dir        模型根目录（web-profiles.json 里 root=models_dir 的相对路径以此为准）
  models_flash_dir  Flash 模型根目录（root=models_flash）
  lms_exe           LM Studio 命令行 lms.exe
  comfy_root        ComfyUI 安装目录
  comfy_store       ComfyUI 存储根（models / input / output 都在其下）
  kvmem_proxy       保活代理脚本（.mjs）
  node_exe          node 可执行文件（留空则用 PATH 里的 node.exe）
"""
import json
import os

APP_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'XingHongPanel')
_CONF = os.path.join(APP_DIR, 'paths.json')
_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(_CONF, 'r', encoding='utf-8') as f:
                _cache = json.load(f) or {}
        except Exception:
            _cache = {}
    return _cache


def get(key, default=''):
    """按 key 取路径：先看 XH_<KEY> 环境变量，再看 paths.json。"""
    env = 'XH_' + key.upper()
    v = os.environ.get(env)
    if v:
        return v
    v = _load().get(key)
    return v if v else default


def comfy_store(default=''):
    """ComfyUI 存储根：未配置时返回 default（调用方通常传 APP_DIR 下的默认目录）。"""
    return get('comfy_store', default)


def comfy_out(sub=''):
    """ComfyUI 输出目录（可再拼子目录，如 'photo'）。未配置时返回空串。"""
    base = get('comfy_out') or (os.path.join(comfy_store(), 'output')
                                if comfy_store() else '')
    return os.path.join(base, sub) if (base and sub) else base


def comfy_input():
    """ComfyUI 输入目录。未配置时返回空串。"""
    return get('comfy_input') or (os.path.join(comfy_store(), 'input')
                                  if comfy_store() else '')


def data_dir():
    """面板运行数据目录（日志/指标/设置）。"""
    return os.path.join(APP_DIR, 'data')
