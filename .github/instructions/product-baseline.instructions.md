---
name: "Robot Product Baseline Gate"
description: "Use when adding or changing a robot model, product Profile, identity/ACCID/SN parsing, Wi-Fi SSID rules, network topology, Workspace routes, acceptance checks, service endpoints, capability states, or tool allowlists."
applyTo:
  - "models/robot_profile.py"
  - "models/workspace.py"
  - "config.py"
  - "network/wifi_manager.py"
  - "services/robot_monitor.py"
  - "ui/main_window.py"
  - "ui/widgets/sidebar.py"
  - "ui/panels/acceptance_test_panel.py"
  - "ui/panels/settings_panel.py"
  - "tests/test_robot_profile*.py"
  - "tests/test_wifi_manager.py"
  - "tests/test_workspace_resources.py"
---
# 机器人产品基线编码前门禁

## 先确认事实，再改代码

新增或修正机器人型号前，必须先获得完整“产品事实包”。以下任一项缺失时停止编码，不得修改 Profile、身份正则、Workspace、验收项或工具白名单：

1. 官方产品名称、文档 URL、版本、修订日期
2. 正式型号字符串与完整 ACCID/SN 精确格式
3. 至少一个真实 SN、SSID、8080 返回 SN 正例
4. 相似非法格式、旧格式、其他产品负例
5. 旧身份格式兼容或拒绝的明确决定
6. Wi-Fi 频段后缀规则，以及 SSID 与 8080 SN 的归一化规则
7. 节点 role、标签、IP、用户、SSH 是否开放
8. 8080、8090、5000、MCP 等服务的支持矩阵与证据
9. 能力状态和允许工具白名单
10. 工作区默认路由、可见/隐藏页面、SSH 快捷入口
11. 只读验收项及每项明确期望值
12. 真机安全边界、停止方式和未完成验证

## 提问规则

- 缺少事实时，一次性列出所有缺失项向用户确认，避免逐项往返。
- 不从产品名称、示例 ACCID、热点名或旧代码推断正式前缀。
- 不把“文档存在接口”写成“应用已验证支持”。
- 官网不可访问时，明确记录当前证据来源和待复核项，不自行补全。

## 保守默认值

只有在事实包完整后才实现，并采用以下默认策略：

- `allowed_tools = frozenset()`
- 未真机验证的控制能力为 `pending_validation` 或 `unsupported`
- 未确认服务端点设为 `None`
- 未确认 SSH 节点使用 `ssh_enabled=False`
- 未知电机数、CPU 核心数、IMU 频率不得硬编码为验收期望
- 独立 Workspace 只显示已验证页面
- Profile 管理的拓扑字段在设置页锁定
- 共享异步连接绑定 generation、Profile 和 ACCID，并校验通知中的 SN

## 首轮最小测试

第一次编辑后立即运行最窄测试，至少覆盖：

- 正式 SN 正例解析为目标 Profile
- 频段 SSID 正例与 portal SN 一致时 READY
- 伪前缀、非数字 SN、多余后缀和明确拒绝的旧格式不匹配
- 多机器人和 SSID/portal 不一致时阻断
- 未开放工具不能入队或执行
- Workspace 隐藏未验证页面
- 未支持服务不探测，控件不可操作
- 旧 Profile 行为不回归

## 完成条件

- 使用 `.github/ISSUE_TEMPLATE/product-baseline.yml` 的事实表作为验收依据
- 增加日期化产品基线文档
- 更新用户说明、维护手册和当前 Sprint
- 本地 `pip check`、`compileall`、全仓测试通过
- Linux/Windows CI 通过后才标记 Done
- 真机未验证项必须继续保持锁定
