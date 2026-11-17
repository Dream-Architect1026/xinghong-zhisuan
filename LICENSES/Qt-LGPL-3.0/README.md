# Qt 许可正文（PySide6 / shiboken6 补充）

上游 `PySide6`、`shiboken6` 的 wheel 只带 `LicenseRef-Qt-Commercial.txt`，
**没有** LGPL/GPL 全文。按 LGPL-3.0 分发本软件时必须随包给全文，因此在这里补一份，
构建时由 `build_xinghong.py` 原样复制到安装目录/便携包的 `LICENSES\Qt-LGPL-3.0\`。

## 适用组件

| 组件 | 版本 | 元数据声明 | 本项目选用 |
|---|---|---|---|
| PySide6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | LGPL-3.0 |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | LGPL-3.0 |

## 为什么同时带 GPL-3.0

LGPLv3 是建立在 GPLv3 之上的附加许可集合，正文本身要求同时提供 GPLv3 全文
（"You should also get your employer ... to sign a 'copyright disclaimer' ..." 一节
与第 3 条均引用 GPLv3）。只放 LGPL-3.0.txt 不完整。

## 文件与来源

| 文件 | 来源 | 获取日期 | SHA256 |
|---|---|---|---|
| `LGPL-3.0.txt` | <https://www.gnu.org/licenses/lgpl-3.0.txt> | 2026-11-13 | `E3A994D82E644B03A792A930F574002658412F62407F5FEE083F2555C5F23118` |
| `GPL-3.0.txt` | <https://www.gnu.org/licenses/gpl-3.0.txt> | 2026-11-13 | `3972DC9744F6499F0F9B2DBF76696F2AE7AD8AF9B23DDE66D6AF86C9DFB36986` |

两处均为 GNU 官方发布的纯文本，未做任何修改（未改行尾、未删节）。
GPL-3.0.txt 与部分旧副本仅差 `http` / `https` 链接写法，属 GNU 官方更新，
内容条款一致。

## 使用边界

- 本项目以**动态链接、未修改**方式使用 PySide6 / shiboken6，相关 DLL 位于
  `_internal\PySide6\`、`_internal\shiboken6\`，用户可以自行替换为兼容版本。
- 上述 LGPL 义务**不因本项目自身采用 MIT 而消失**；再分发二进制时仍需按 LGPL-3.0
  （或另行取得的 Qt 商业许可）履行。
- Qt 源码获取方式见 <https://www.qt.io/terms-conditions> 与 Qt 官方发布渠道。
