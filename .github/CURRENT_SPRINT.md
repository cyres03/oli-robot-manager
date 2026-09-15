# Sprint 5：Luna 动作库真机准入

- 周期：2026-09-14 至 2026-09-27
- Product Owner / Developer / Reviewer：cyres03
- 真机操作人员：cyres03
- 发布目标：Cross-platform，不创建正式 Tag

## Sprint Goal

在不开放 Luna 通用运动控制的前提下，完成舞蹈和原子动作的专项真机验证与最小权限准入。

## 承诺工作项

| Issue | 类型 | 故事点 | 平台 | 状态 |
|-------|------|--------|------|------|
| #66 | Story | 5 | Cross-platform | Done |

当前 WIP：0

## Story 拆分

- Luna 单次舞蹈和原子动作工具准入
- `Walk` 状态 UI + Service 双层门禁
- 每次真机动作二次安全确认
- 动作库自动进入、就绪检测、退出和恢复 Walk
- Luna 舞蹈终态 response 与 Oli notify 语义隔离
- 重复动作、序列器、行走和手动动作库入口保持锁定

## 验收重点

- `HU_L04_01_084`、固件 `robot-luna-r-1.2.11.20260819121912`
- `Nod`：response 与完成 notify 均 success，动作后恢复 Walk
- `wakawaka`：终态 response success，`total_duration=23.437s`、`walk_restored=1`，无 `notify_dance`
- 状态非 Walk 或目标切换时禁止下发
- Luna 动作前不插入未准入的零速度命令
- Oli 原有 response+notify 舞蹈语义不回归

## 本次不做

- Luna 自动站立、行走、坐下、躺下、阻尼和零力矩
- Luna 连续动作、序列器和手动动作库模式
- Luna 校零、Backlash、MoveJ/MoveP、UB/WB 和末端硬件
- 正式版本 Tag

## Sprint Review

- Sprint Goal：达成
- 自动化测试：255 passed
- 静态验证：`git diff --check`、`pip check`、`compileall`、编辑器诊断通过
- 真机原子动作：`Nod` 通过，response+notify success
- 真机舞蹈：`wakawaka` 通过，终态 response success，无 `notify_dance`
- 真机恢复：两次动作均确认 `Walk/Walk`
- Windows/Linux CI：通过（PR #67）

## Retrospective

- Keep：先做最短动作、逐步扩大，并以真实 response/notify 作为准入证据
- Stop：假设同源产品的动作完成通知语义完全相同
- Try：将产品差异收敛到薄适配器，继续保持工具最小权限