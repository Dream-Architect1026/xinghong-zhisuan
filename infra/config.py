# -*- coding: utf-8 -*-
"""跨层共享的运行时开关与配置对象（C1-B3：B2 跨层传参开销的唯一落点）。

背景：B2 把页面/定时器/托盘从 build_ui 搬出时，把这些对象当参数逐层传
（页面签名 1 处 + 调用点 kwarg 名/值 2 处），累计多出 44 个出现点。
B3 把它们收到本模块：**同一进程只导入一次，对象身份不变**，页面/定时器/托盘
直接 import，不再经 launcher 中转。

约定：
  · 这里只放「运行时真源」对象；env 在 import 时读一次（与搬迁前 launcher 模块级
    读取时机一致，仍在 QApplication 构造之前）。
  · 可变对象（进阶层 / 节拍表 / 静音门 / 退出方式）由使用方原地改写
    （`d[k] = v`），禁止重新绑定名字——重新绑定会让其他模块看到旧对象。
  · 分层：infra（最底层）。ui / app 只允许向下 import 本模块，本模块不得 import 任何业务层。
"""
import os

__all__ = ['XH_ADV', 'XH_CADENCE', 'XH_COST', 'XH_MUTE', 'XH_EXIT_HOW',
           'XH_COVER', 'XH_WALL_N', 'XH_PROBE', 'XH_PROFILE']

# 进阶配置的运行时快照（启动期由 Prefs 覆盖；配置页保存后原地更新）。
XH_ADV = {'anim': 260, 'reduce': False,
          'pollInterval': 2, 'chartPoints': 1200, 'smooth': '平滑',
          'logLevel': 'INFO', 'notify': True, 'dnd': False}

# 节拍唯一真源：轮询间隔只在 _xh_cadence() 里算成「有效毫秒」后写入这里。
XH_CADENCE = {}

# R02/25 轮诊断开关：仅用于定位空闲 CPU 热点，正常启动不设置即全开。
# 取值可组合：nogauge / nochart / nodot
XH_PROBE = os.environ.get('XH_CPU_PROBE', '')

# R04：非空则为进程内计时输出文件路径
XH_PROFILE = os.environ.get('XH_PROFILE', '')

# R14（仅归因用，出货默认不设）：限制墙内条目数；强制 cover 代替 letterbox
XH_WALL_N = int(os.environ.get('XH_WALL_N') or 0)
XH_COVER = bool(os.environ.get('XH_COVER'))

# 非空则记录每次轮询的「网络+JSON 解析（工作线程）」与「UI 回调」耗时（毫秒）
XH_COST = os.environ.get('XH_COST', '')

# 托盘「静音通知」勾选态（模块级共享，菜单与告警通道都读）
XH_MUTE = {'on': False}

# 正常退出方式（写进 _graceful_exit.json 供看门狗区分崩溃）
XH_EXIT_HOW = {'v': 'window_close'}
