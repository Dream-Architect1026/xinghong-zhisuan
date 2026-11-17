# -*- coding: utf-8 -*-
"""统一时间格式化（星虹智算）。

收口前 6 处各写各的 strftime（含 3 种重复的 `%Y-%m-%d %H:%M:%S`），
格式串散落在 5 个文件位置，改一处格式没人知道另一处还在用旧写法。
这里把「格式串」与「取本地时间的方式」都收成一份，调用方只说要哪种口径：

    stamp()     2026-11-12 13:05:07     秒级时间戳（日志 / 退出留痕 / 会话记录）
    minute(ts)  2026-11-12 13:05        到分钟（文件 mtime 展示）
    day()       2026-11-12              日期（按天分文件的文件名）
    fstamp()    20261112_130507         文件名安全的时间戳（诊断包）
    md_hm(ts)   11-12 13:05             月-日 时:分（任务表）

口径约定（改之前先读 R38-26 第 2 步报告）：
- 一律本地时间，一律 `time.localtime` + `time.strftime`，不用 astimezone()、
  不做任何时区换算——收口前后由同一个 C 库本地时间函数产出，跨夏令时边界的
  行为与改前一致（机器时区 Asia/Shanghai 本身无 DST）。
- `stamp()`/`day()`/`fstamp()` 传 None 表示「现在」，与旧写法
  `time.strftime(fmt)`、`datetime.now().strftime(fmt)` 等价（都是 OS 本地墙钟）。
- 硬约束（禁止合并）：本模块只管日志 / 文件名 / 事件级时间戳，不含坐标轴刻度
  格式；刻度在 `TimeAxisItem.tickStrings`，属每帧热路径、逐值 try/except 兜空串，
  刻意与日志格式分开。理由：tickStrings 每帧按任意 fmt 逐 tick 调用，单次失败只能
  返回空串而不能抛，且调用频率比日志高几个数量级；若在此共用一份实现，要么把热
  路径拖上日志侧的额外判断，要么让日志侧继承"失败吞掉"的语义、掩盖真实错误。
"""
import time

# C1：模块导出清单（tools/check_imports.py 校验）
__all__ = ['FMT_DATETIME', 'FMT_MINUTE', 'FMT_DAY', 'FMT_STAMP', 'FMT_MD_HM',
           'stamp', 'minute', 'day', 'fstamp', 'md_hm']

FMT_DATETIME = '%Y-%m-%d %H:%M:%S'
FMT_MINUTE = '%Y-%m-%d %H:%M'
FMT_DAY = '%Y-%m-%d'
FMT_STAMP = '%Y%m%d_%H%M%S'
FMT_MD_HM = '%m-%d %H:%M'


def _now():
    return time.time()


def stamp(ts=None):
    """秒级本地时间：2026-11-12 13:05:07。ts=None → 现在。"""
    return time.strftime(FMT_DATETIME, time.localtime(_now() if ts is None else ts))


def minute(ts):
    """到分钟：2026-11-12 13:05。"""
    return time.strftime(FMT_MINUTE, time.localtime(ts))


def day(ts=None):
    """日期：2026-11-12。ts=None → 现在。"""
    return time.strftime(FMT_DAY, time.localtime(_now() if ts is None else ts))


def fstamp(ts=None):
    """文件名安全：20261112_130507。ts=None → 现在。"""
    return time.strftime(FMT_STAMP, time.localtime(_now() if ts is None else ts))


def md_hm(ts):
    """月-日 时:分：11-12 13:05。"""
    return time.strftime(FMT_MD_HM, time.localtime(ts))
