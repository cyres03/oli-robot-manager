# Sprint 3：建立自动验收历史与复验闭环

- 周期：2026-08-26 至 2026-09-01
- Product Owner / Developer / Reviewer：cyres03
- 真机操作人员：cyres03
- 发布目标：Cross-platform，不创建正式 Tag

## Sprint Goal

验收人员能按当前产品查看历史会话和单项结果，并把旧会话的失败项组成新的复验会话，原结果保持不变。

## 承诺工作项

| Issue | 类型 | 故事点 | 平台 | 状态 |
|-------|------|--------|------|------|
| #49 | Task | 5 | Cross-platform | Done |
| #50 | Story | 3 | Cross-platform | Done |

当前 WIP：0

## Story 拆分

- #8：父 Story，自动验收会话与 JSON/CSV 报告
- #49：会话模型与 SQLite 存储（本 Sprint）
- #50：历史界面和失败项复验（Backlog）
- #51：JSON/CSV 导出（Backlog）

## 验收重点

- 按当前产品 Profile 显示最近会话
- 选择会话后显示全部单项结果和备注
- 只有存在 FAIL 时启用“复验失败项”
- 复验创建新会话，不覆盖旧结果
- Profile 切换后重新筛选历史

## 本次不做

- JSON/CSV 导出
- 飞书回写
- 运动、校零、断电或 Backlash 新逻辑

## Sprint Review

- Sprint Goal：本地实现完成，待 PR/CI
- 自动化测试：163 passed
- Windows：源码窗口响应正常，历史标签和真实数据库查询通过
- Windows/Linux CI：待 PR 验证
- 真机：仅验证现有自动验收只读项，不新增命令

## Retrospective

- Keep：#49 数据层稳定后再接历史 UI
- Stop：在历史 UI PR 中夹带导出功能
- Try：历史闭环完成后再启动 #51 导出