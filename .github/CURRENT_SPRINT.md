# Sprint 4：建立 TRON2 产品基线

- 周期：2026-09-07 至 2026-09-13
- Product Owner / Developer / Reviewer：cyres03
- 真机操作人员：cyres03
- 发布目标：Cross-platform，不创建正式 Tag

## Sprint Goal

Robot Manager 能安全识别 TRON2、进入独立工作区并执行官方文档支持的只读验收；未验证控制能力保持锁定。

## 承诺工作项

| Issue | 类型 | 故事点 | 平台 | 状态 |
|-------|------|--------|------|------|
| #58 | Task | 5 | Cross-platform | Done |
| #60 | Bug | 2 | Cross-platform | Done |

当前 WIP：0

## Story 拆分

- TRON2 Profile 与 `TRON2A_*` 身份识别
- TRON2 独立工作区与导航隔离
- 官方拓扑端点与 `.4` 开发电脑 SSH 基线
- 只读验收/诊断项和未支持服务 UI 门控
- 产品基线文档、使用说明和维护手册更新
- #60：纠正初版误写的 `WF_TRON2*` 身份，并收紧 Wi-Fi 自动连接格式

## 验收重点

- `TRON2A_*` SN 前缀解析到 `tron2` Profile，旧 `WF_TRON2*` 不再冒充正式身份
- 默认进入 TRON2 验收页
- 不显示舞蹈、控制、测试用例、健康检查或校零入口
- 工具白名单为空，不加载隐藏动作资源
- 仅检查 Wi-Fi、8080、`.4` SSH 和时间
- 8090/MCP/`.2` SSH 明确不支持且 UI 不可操作
- 官方文档事实、风险和后续准入条件形成日期化基线

## 本次不做

- TRON2 运动、灯效、紧急停止和实时控制
- TRON2 `.2` SSH、8090、MCP
- 固定电机数量或文档示例维数
- 校零、Backlash、动作库和硬件疲劳测试
- 正式版本 Tag

## Sprint Review

- Sprint Goal：达成
- 自动化测试：238 passed
- 静态验证：`pip check`、`compileall`、编辑器诊断通过
- Windows/Linux CI：通过（PR #59）
- #60 验证：238 passed；Windows/Linux CI 通过（PR #61）
- 真机：未执行运动或写操作
- 官方文档：V0.2 已核验内容建立基线；2026-09-07 官网正文受第三方跳转影响，待恢复后复核

## Retrospective

- Keep：先建立只读能力矩阵，再决定控制接口准入
- Stop：把“文档存在接口”等同于“应用已验证支持”
- Try：后续每个 TRON2 控制能力单独建 Issue 和真机验收记录