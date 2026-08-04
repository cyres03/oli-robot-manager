# SDK 按钮逻辑与 joystick 映射说明

本文只覆盖当前软件里会发送 WebSocket SDK 指令的按钮/动作。健康检查、回差检测、SSH 诊断类功能主要走 SSH 或本地工具，不属于 WebSocket SDK 控制链路。

## 全局控制链路

1. UI 按钮触发 `ControlPanel.action_requested` 或 `DanceService` 方法。
2. `MainWindow`/服务调用 `McpWorker.call_tool(tool_name, arguments)`。
3. `McpWorker` 串行执行队列，最终进入 `RobotClient.call_tool()`。
4. `RobotClient` 发送 SDK JSON：`accid/title/timestamp/guid/data`。
5. 普通 request 等同 guid 的 `response_*`；舞蹈/动作还要等对应 `notify_*`。

本地审计日志：`%LOCALAPPDATA%\OliRobotManager\audit\sdk_requests.jsonl`。每条会记录时间、accid、title、guid、data、response/notify/error。之后如果机器人日志再次出现 `joystick_manager key combination changed`，先按时间和 guid/title 对齐这份文件。

## 基础控制按钮

| 按钮 | SDK title / data | 继续执行或判定成功的 response 内容 | 备注 |
| --- | --- | --- | --- |
| 准备站立 | `request_prepare {}` | `response_prepare.data.result == "success"` | worker 会先停止正在运行的直线行走；无 notify。 |
| 站立 | `request_standup {"mode":"hanging/sitting/lying"}` | `response_standup.data.result == "success"`；如果 `enter_walk_after=true`，还必须 `request_set_walk_mode` 返回 success | 吊装保护开启时用 `hanging`，且不自动进 Walk。 |
| 坐下 | `request_from_stand_to_sit {}` | `response_from_stand_to_sit.data.result == "success"` | 吊装保护开启时按钮禁用。 |
| 躺下 | `request_lie_down {}` | `response_lie_down.data.result == "success"` | SDK 允许 Walk 状态调用；吊装保护开启时按钮禁用。 |
| 停止行走 | `request_set_walk_vel_sync {"x":0,"y":0,"yaw":0}` | SDK 成功时可能无 response；软件短时间只监听失败 response，没收到失败则记为 `sent` | 已改：不再发送 `request_damping`。 |
| 行走模式 | `request_set_walk_mode {}` | `response_set_walk_mode.data.result == "success"` | UI 只在当前状态允许时启用。 |
| 动作库模式 | `request_set_motion_engine {"mode":1}` | `response_set_motion_engine.data.result == "success"` | 进入动作库引擎。 |
| 阻尼模式 | `request_damping {}` | `response_damping.data.result == "success"` | 高风险：默认软件锁定，必须勾选“允许阻尼/零力矩按钮”并二次确认。 |
| 零力矩 | `request_zero_torque {}` | `response_zero_torque.data.result == "success"` | 高风险：默认软件锁定，必须勾选并二次确认。 |
| 直线行走测试 | 每 100ms `request_set_walk_vel_sync {"x":0.7,"y":0,"yaw":0}` | 成功通常无 response；若收到失败 response 则记录失败 | 10 秒后只在测试确实运行时发送一次零速度停止。 |
| 动作库状态 | `request_get_action_library_status {}` | `response_get_action_library_status.data.result == "success"`，并读取 `action_library_mode/state` | 只查询状态。 |
| 查询唤醒词 | `request_audio_get_wakeup_word {}` | response 中读取 `word/pinyin/thresh/greeting/subsets/backend` | 只查询。 |
| 开启/关闭唤醒 | `request_audio_wakeup_control {"enable":1/0}` | `response_audio_wakeup_control.data.result == "success"` | 只改语音唤醒。 |
| 绿灯常亮/蓝灯呼吸/关闭灯效 | 先 `request_enable_led_control {"enable":1}`，再 `request_led_control {"led_index":0,"led_state":...,"led_color":...}` | 两个 response 的 `data.result == "success"` | 不触发运动模式。 |

## 姿态循环的下一步条件

现在姿态循环不再靠固定 timer 直接推进，而是 response-driven：

| 循环步骤 | 发出的 SDK | 允许进入下一步的条件 |
| --- | --- | --- |
| 坐下起身循环：坐下 | `request_from_stand_to_sit {}` | `response_from_stand_to_sit.data.result == "success"` 后，再等待 UI 设置的安全间隔。 |
| 坐下起身循环：起身 | `request_standup {"mode":"sitting"}`，随后自动 `request_set_walk_mode {}` | `response_standup.data.result == "success"` 且 `response_set_walk_mode.data.result == "success"` 后，再等待安全间隔。 |
| 躺下起身循环：躺下 | `request_lie_down {}` | `response_lie_down.data.result == "success"` 后，再等待安全间隔。 |
| 躺下起身循环：起身 | `request_standup {"mode":"lying"}`，随后自动 `request_set_walk_mode {}` | `response_standup.data.result == "success"` 且 `response_set_walk_mode.data.result == "success"` 后，再等待安全间隔。 |

任一步返回失败、超时或 worker 报错，循环会停止，不再下发后续姿态动作。

## 舞蹈、动作库、序列器

| UI 操作 | SDK title / data | 判定完成的内容 | 备注 |
| --- | --- | --- | --- |
| 刷新舞蹈 | `request_get_dance_list {}` | `response_get_dance_list.data.result == "success"`，读取 `dances` | 只查询。 |
| 刷新动作 | `request_get_atomic_motion_list {}` | `response_get_atomic_motion_list.data.result == "success"`，读取 `motion_list` | 只查询。 |
| 执行舞蹈卡片 | 先确保 `request_set_motion_engine {"mode":1}`，再 `request_dance {"name":rc_mapping}` | `response_dance.data.result == "success"` 且 `notify_dance.data.result == "success"` | 完成后自动 `request_set_motion_engine {"mode":0}` + `request_set_walk_mode {}`。 |
| 执行动作卡片 | 先确保 `request_set_motion_engine {"mode":1}`，再 `request_execute_atomic_motion {"motion_name":name}` | `response_execute_atomic_motion.data.result == "success"` 且 `notify_execute_atomic_motion.data.result == "success"` | 完成后自动退出动作库并回 Walk。 |
| 序列器：舞蹈/动作步骤 | 同上 | 当前动作 response+notify 都 success 后才推进下一步 | 失败则停止序列。 |
| 序列器：行走步骤 | `request_set_walk_vel_sync {x,y,yaw}` | 成功通常无 response；没收到失败则记为 `sent`，再按步骤 delay 推进 | 行走速度类 SDK 成功无返回，不能把它当成“机器人已完成动作”。 |

## joystick 映射从哪里来

当前软件代码里没有 `A+L2+DOWN`、`L2`、`BACK` 之类按键组合映射，也不会发送 `notify_joy_data`。软件只发送 WebSocket SDK 的 `request_*`。

机器人日志里的 `joystick_manager.cpp key combination changed: A+L2+DOWN` 只能说明机器人内部走到了名为 joystick_manager 的快捷命令处理路径，不能单独证明有人操作了物理遥控器。可能来源有三类：

1. 真实遥控器输入：需要同时看到 `notify_joy_data` 或机器人侧遥控器 axes/buttons 证据才能坐实。
2. 机器人固件/任务层把某些 SDK 或内部状态切换复用了 joystick_manager 的快捷命令路径，因此日志名字显示 joystick。
3. 同一时间另一个客户端、测试脚本或机器人内部服务向机器人发送了控制请求。

2026-06-03 19:04:54 那次旧日志里没有本软件审计，所以无法反推本软件是否在同一毫秒发过某个 request。新版本以后要这样判断：

1. 先查机器人日志里 `key combination changed` 和 `Switch mode to Damped` 的时间。
2. 再查 `sdk_requests.jsonl` 同一时间窗口是否有 `request_damping`、`request_zero_torque`、`request_set_walk_vel_sync`、`request_set_motion_engine` 或姿态类 request。
3. 如果本地审计没有对应 request，就优先排查机器人内部服务、其他控制端、遥控器输入或 SDK 层映射。
4. 如果本地审计有对应 request，再用 guid/title/data 精确定位是哪个软件动作触发。

后续如果要进一步排除多端控制，可以基于 SDK 4.7 的 `request_lock_robot_control` 做独占控制：软件连接后先锁定控制权，退出或断开时释放。这样能减少“另一个端同时发指令”的不确定性。