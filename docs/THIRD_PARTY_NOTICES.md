# 第三方组件与开源许可

本软件以 MIT 许可发布（见 [LICENSE](../LICENSE)），并打包多个第三方组件。下表列出随包分发的组件、版本与许可标识。

| 组件 | 版本 | 许可标识 |
|---|---|---|
| PySide6（Qt for Python 绑定） | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| shiboken6（绑定生成器运行时） | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| NumPy | 2.5.2 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| psutil | 7.2.2 | BSD-3-Clause |
| pyqtgraph | 0.14.0 | MIT |
| PyYAML | 6.0.3 | MIT |
| charset-normalizer | 3.5.1 | MIT |
| pywin32 | 312 | PSF |
| Noto Sans SC（字体子集） | 随包 | SIL Open Font License 1.1 |

运行时与打包工具（不构成对你的许可约束）：CPython（PSF License 2.0）、PyInstaller 引导程序（GPL-2.0 with bootloader exception，仅负责启动）。

## Qt / PySide6 的 LGPL 说明

PySide6 与 shiboken6 以独立 DLL 形式随程序分发（位于 `_internal\PySide6\`、`_internal\shiboken6\`），未静态嵌入，因此可以直接替换为你自行编译的兼容版本。替换后出现异常属自行修改的结果。LGPL-3.0 全文：<https://www.gnu.org/licenses/lgpl-3.0.txt>；GPL-3.0 全文：<https://www.gnu.org/licenses/gpl-3.0.txt>。

## 其他组件要点

- NumPy：BSD 3-Clause，允许修改与再分发，保留版权声明与免责声明即可。
- psutil：BSD 3-Clause。
- pyqtgraph：MIT；其内置 CET 色图数据另有 CC-BY 授权。
- PyYAML、charset-normalizer：MIT。
- pywin32：Python Software Foundation License，本软件只使用其 Windows API 封装。
- Noto Sans SC：SIL OFL 1.1，允许随软件嵌入与再分发，不得单独售卖字体本身。

## 许可证全文位置

- 安装版：`<安装目录>\LICENSES\`（默认 `%LOCALAPPDATA%\Programs\XingHongPanel\LICENSES\`）。
- 便携版：`<解压目录>\LICENSES\`。
- 构建时从各包 `dist-info` 收集 LICENSE/COPYING/NOTICE；若某包声明许可但未附全文，`LICENSES\` 下放 `NOTICE.txt` 给出官方 URL。
- 构建带断言：`LICENSES\` 下的组件目录数不得少于实际打包的第三方组件数，缺一即构建失败。

## 兼容性

本软件以 MIT 发布，与上表各组件的许可无冲突；不静态链接任何 GPL/LGPL 组件。若你认为遗漏了某个组件的署名或全文，请通过 [legal/第三方组件与开源许可.md](../legal/第三方组件与开源许可.md) 中的联系方式告知。
