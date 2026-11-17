# 版本记录

版本号唯一来源为 `ux_core.py` 的 `APP_VERSION`；`build_xinghong.py` 由它生成 `version.txt`、`product_name.txt`、`version_info_xinghong.txt` 与安装器 `_name.iss`。

## 1.5.2（当前）

- 启动耗时告警与面板身份自检收口，避免把别的服务误判为本面板。
- 本机路径改为配置驱动：KVMem 可执行文件、模型目录、`lms.exe`、ComfyUI 根与存储、保活代理、node 均支持 `paths.json` / `XH_*` 环境变量，仓库内不再含开发机绝对路径。
- `web-profiles.json` 的模型路径改为相对路径 + `root` 基准目录；`comfy-paths.yaml` 的模型基础目录改由启动时按配置生成。
- 磁盘健康改为按系统盘统计。
- 发布准备：补充 README、docs、截图与第三方许可说明。

## 1.5.0

- QSettings 统一为一组 org/app（`X.H` / `XingHongPanel`）。
- 安装包补齐 `LICENSES\` 目录与组件清单断言，缺一不产出安装包。
- 托盘菜单动作挂到菜单上，修复右键菜单为空。
- ComfyUI 模型路径配置改为随包分发，修复部署包点「启动 ComfyUI」报文件缺失。

## 1.4.3

- 修复生图页「启动/停止 ComfyUI」按钮的 `NameError`（调用了不存在的 `do()`）。
- 超分扩散路线增加显存预算限幅：输出超过 16MP 时自动生成缩小副本。
- 无预览封面的视频不再整段解码，改为占位封面。

## 1.4.0

- 新增 ComfyUI 托管、作品墙与任务台账。
- 新增视频生成与超分放大页面。
- 监控指标持久化到 `metrics.jsonl`，支持永久/限时保留。

## 1.3.0

- 引入 KVMem 路线与保活代理，长上下文空闲后不再全量重算。
- 引入单实例命名管道与托盘常驻。
- 状态页改为四块大数字 + 滚动曲线。

## 1.0.0

- 首个可用版本：状态监控、LM Studio 路线、配置中心。
