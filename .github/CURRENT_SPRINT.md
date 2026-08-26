# Sprint 3：建立自动验收会话存储

- 周期：2026-08-26 至 2026-09-01
- Product Owner / Developer / Reviewer：cyres03
- 真机操作人员：cyres03
- 发布目标：Cross-platform，不创建正式 Tag

## Sprint Goal

每次自动验收都形成可追溯的本地会话，检查结果即时写入 SQLite，异常退出、用户停止或换机不会留下伪完成记录。

## 承诺工作项

| Issue | 类型 | 故事点 | 平台 | 状态 |
|-------|------|--------|------|------|
| #49 | Task | 5 | Cross-platform | In Progress |

当前 WIP：#49（1 项）

## Story 拆分

- #8：父 Story，自动验收会话与 JSON/CSV 报告
- #49：会话模型与 SQLite 存储（本 Sprint）
- #50：历史界面和失败项复验（Backlog）
- #51：JSON/CSV 导出（Backlog）

## 验收重点

- 运行全部或单项时创建会话
- 每项 PASS/FAIL/N/A 即时写库并更新统计
- 正常完成写入 completed
- 用户停止、Profile 切换、应用退出或异常中断写入 cancelled
- 详情进入数据库前脱敏
- 重启后 Repository 可读取历史

## 本次不做

- 历史记录 UI
- JSON/CSV 导出
- 飞书回写
- 运动、校零、断电或 Backlash 新逻辑

## Sprint Review

- Sprint Goal：本地实现完成，待 PR/CI
- 自动化测试：160 passed
- Windows：源码窗口启动，数据库迁移成功，无新崩溃日志
- Linux：待 PR CI 验证
- 真机：仅验证现有自动验收只读项，不新增命令

## Retrospective

- Keep：先拆分 8 点 Story，再启动一个 5 点 Task
- Stop：把历史、导出和存储混在同一个 PR
- Try：先确保数据可靠，再设计历史 UI