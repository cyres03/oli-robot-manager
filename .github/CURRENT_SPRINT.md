# Sprint 2：完成 Oli/Luna 双工作区基础

- 周期：2026-08-23 至 2026-08-29
- Product Owner / Developer / Reviewer：cyres03
- 真机操作人员：cyres03
- 发布目标：Cross-platform，不创建正式 Tag

## Sprint Goal

软件能自动识别 D04/L04 并进入 Oli/Luna 独立工作区，安全加载对应节点、资源和
已验证能力；两个产品不共享运行时资源状态，Oli 现有功能不回归。

## 承诺工作项

| Issue | 类型 | 故事点 | 平台 | 状态 |
|-------|------|--------|------|------|
| #17 | Bug | 3 | Cross-platform | Done |
| #20 | Bug | 1 | Cross-platform | Done |
| #22 | Bug | 1 | Cross-platform | Done |
| #18 | Story | 5 | Cross-platform | In Progress |

当前 WIP：#18（1 项）

## 旧功能基线

- 未连接时可主动发现机器人 WiFi：#12 Done。
- 身份失败不使用默认 Oli 目标：#17 Done。
- 三类 WebSocket 请求均执行无目标门禁：#20 Done。
- 顶部状态栏能够显示现有 Oli SN：#22 Done。

## 本次不做

- 不执行运动、模式切换、动作、校零、Backlash、灯效或语音设置。
- 不开放 claw、UB/WB 等返回 `fail_no_data` 的能力。
- 不把现有 PyQt6 应用重写为 Electron/React。
- 不创建正式发布 Tag。

## 后续顺序

1. #24：Luna `.2/.4` 测试用例执行器。
2. #25：自动读取日志并诊断错误，结果关联测试会话。
3. #26：参考 LimX Studio 的 Electron/React 渐进迁移 Spike。

## 前端参考

参考本机 LimX Studio 0.1.35 的工作台信息架构和组件语义：Electron 38、React、
shadcn/Radix、Tailwind CSS 4、Zustand、React Router 和 Lucide。本 Sprint 保留
PyQt6，只借鉴紧凑工作台、清晰状态和能力驱动交互；技术栈迁移另立 Spike。

## 真机计划

| Issue | 机器人型号 / ACCID | 固件 | 安全工况 | 允许操作 |
|-------|-------------------|------|----------|----------|
| #18 | HU_L04_01_091 | robot-luna-r-1.2.12.20260821201520 | 机器人静止 | 被动状态、HTTP、SSH 只读、4 项查询 |

## Sprint Review

- Sprint Goal：待 Review
- 自动化测试：待 PR 最终记录
- Linux 真机：启动与四项只读查询已完成，结果待写入 PR
- Windows：由 CI 验证，目标机器冒烟测试后续安排

## Retrospective

- Keep：新需求前先修复受影响旧功能。
- Stop：以接口标题存在代替行为和安全验收。
- Try：下一 Sprint 为 L04 控制能力逐类建立独立真机 Story。