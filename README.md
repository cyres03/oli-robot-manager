# Oli Robot Manager

Oli Robot Manager 是面向机器人售后、入库和交付验收的 PyQt6 桌面工具，集成机器人识别、动作控制、自动验收、健康检查、日志分析、断电恢复和校零流程。

当前仓库为公司内部协作项目，正在现有 Oli 机器人基础上扩展第二款机器人适配。

## 仓库信息

| 项目 | 内容 |
|------|------|
| 私有仓库 | [cyres03/oli-robot-manager](https://github.com/cyres03/oli-robot-manager) |
| 仓库负责人 | `cyres03` |
| 默认分支 | `main` |
| 当前版本 | `1.0.1` |
| 最新迭代 | [跨平台迭代说明 2026-08-20](更新说明-跨平台-20260820.md) |

首次获取源码：

```powershell
git clone https://github.com/cyres03/oli-robot-manager.git
cd oli-robot-manager
```

仓库为私有仓库，必须先由负责人添加为 Collaborator 并接受邀请。

## 快速开始

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

默认机器人地址已经内置，不需要创建 `config.local.json`。连接机器人 WiFi 后，直接在“验收测试”运行自动检查；首次 SSH 检查会提示输入当前机器人账号密码，并在授权成功后自动继续。

可在密码弹窗勾选“记住到系统凭据管理器”。Windows 使用
Credential Manager，Linux 使用 Secret Service；凭据按机器人隔离，不写入仓库。
只有需要覆盖默认地址或兼容旧部署时，才使用 `config.example.json` 创建本地配置。

如需开发 Backlash 回差检测功能，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_backlash_resource.ps1
```

资源来源：[Backlash Runtime Resource v1](https://github.com/cyres03/oli-robot-manager/releases/tag/backlash-resource-v1)，附件约 113.7 MB。

## 主要文档

- [项目源码交接与二次开发说明](项目源码交接与二次开发说明.md)
- [GitHub 协作说明](GitHub协作说明.md)
- [SDK 按钮逻辑与 joystick 映射说明](SDK按钮逻辑与joystick映射说明.md)
- [发布说明](发布说明.md)
- [Linux 更新说明](更新说明-Linux-20260819.md)
- [跨平台迭代说明](更新说明-跨平台-20260820.md)
- [敏捷开发流程](敏捷开发流程.md)

## 协作原则

1. `main` 始终保持可运行，不直接在 `main` 上开发。
2. 每项工作先创建 GitHub Issue，进入 Sprint 后创建独立分支，通过 Pull Request 合并。
3. 不提交 `config.local.json`、日志、数据库、构建产物和安装包。
4. 机器人动作改动必须说明测试工况、停止方式和真机验证结果。
5. 新机器人差异优先放入配置或适配器，不在各页面散布型号判断。