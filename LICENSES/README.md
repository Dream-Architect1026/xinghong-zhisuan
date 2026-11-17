# 许可

## 本项目自身

本仓库自己的代码采用 **MIT 许可**，版权归 © 2026 X.H，全文见根目录 [`LICENSE`](../LICENSE)。
出包时这份 `LICENSE` 会复制到安装目录/便携包的根（`{app}\LICENSE`），与第三方许可分开。

MIT 只覆盖本项目自己的代码，**不改变**下列第三方组件的许可；第三方组件各受各自的许可约束，
再分发时请同时满足它们的条件。已发布二进制包的日常使用条款另见安装包内《用户协议》。

## 第三方组件

星虹智算随包分发下列第三方组件。完整文件在安装目录的 `_internal\LICENSES\` 下（便携包同样），
由构建脚本从包元数据自动收集生成，勿手改；本文件是它的索引与说明。
其中 GPL/LGPL 类组件（Qt / PySide6 / shiboken6）的条款不因本项目采用 MIT 而改变，
仍按各自的 LGPL/GPL 条件执行。

| 组件 | 版本 | 许可标识（取自包元数据） | 包内目录 |
|---|---|---|---|
| PySide6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `PySide6-6.12.0\` |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `shiboken6-6.12.0\` |
| numpy | 2.5.2 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `numpy-2.5.2\` |
| psutil | 7.2.2 | BSD-3-Clause | `psutil-7.2.2\` |
| pyqtgraph | 0.14.0 | MIT（附带的 CET 色图数据为 CC-BY） | `pyqtgraph-0.14.0\` |
| PyYAML | 6.0.3 | MIT | `PyYAML-6.0.3\` |
| charset-normalizer | 3.5.1 | MIT | `charset-normalizer-3.5.1\` |
| pywin32 | 312 | PSF | `pywin32-312\` |
| Noto Sans SC（字体子集） | 随包 | SIL Open Font License 1.1 | `NotoSansSC-OFL\` |
| Qt 许可正文（PySide6 / shiboken6 补充） | 6.12.0 | LGPL-3.0 + GPL-3.0（全文已随附） | `Qt-LGPL-3.0\` |

补充说明：

- **Qt / PySide6 / shiboken6**：上游 wheel 的 `licenses\` 只带 `LicenseRef-Qt-Commercial.txt`，
  **LGPL-3.0 与 GPL-3.0 全文已由本项目补入 `Qt-LGPL-3.0\`**（来源、获取日期、SHA256 见
  该目录的 `README.md`），构建时原样复制进安装目录/便携包的 `LICENSES\Qt-LGPL-3.0\`。
  条款以上游为准（<https://www.qt.io/terms-conditions>）。要注意的是，本项目自身改用 MIT
  **不会**把 Qt 的义务变成 MIT：若在 MIT 之外再分发二进制，仍需按所选 LGPL/GPL 条件履行
  （含提供许可文本、允许替换库等），或另行取得 Qt 商业许可。
- **Noto Sans SC**：只随包一个子集，未附 OFL 全文，`NOTICE.txt` 里给了官方全文地址
  （<https://openfontlicense.org/open-font-license-official-text/>）。
- **numpy**：目录内是各子模块与内联代码的许可集合（`COPYING`、`LICENSE*`、
  `lapack_lite` / `pocketfft` / 随机数生成器等），逐项生效。
- 应用内《第三方组件与开源许可》页与 `LICENSES\README.txt` 是同一份数据的运行时/文件版。

## 不在本仓库的组件

LM Studio、ComfyUI、llama.cpp 等由用户自行安装、本客户端只通过本地端口调用，不随包分发，
其许可与分发条件见各自项目。
