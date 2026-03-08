# OpenCode 会话监控方案提案（V2）

## 这版修正了什么

相较上一版，这里明确修正两点：

1. **本地状态存储不是为了和 OpenCode 共享文件**  
   它只是 OpenClaw 侧 watcher 的本地缓存与状态机持久化。

2. **不假设 OpenCode 与 OpenClaw 共享节点或可互相直接访问文件**  
   远端状态以 **OpenCode API** 为真相源；OpenClaw 只在本地缓存“我已经看过什么、当前判断是什么”。

---

## 目标

实现一套低 token 成本、可审计、适合 OpenClaw 编排的 OpenCode 监控方案，满足：

- 实时发现会话结束
- 以一定频率检查任务是否符合预期
- 不依赖高频 agent 阅读 transcript
- 结束/异常/阻塞时及时通知用户

---

## 核心设计原则

1. **观察机械化，判断最小化 agent 化**
2. **优先事件驱动，轮询兜底**
3. **只读增量，不重复读全量 transcript**
4. **通知与监控解耦**
5. **不依赖远端文件系统共享**
6. **触发判定与上下文沉淀分离**

---

# 一、系统边界

## 1. OpenCode 是远端被观察对象
远端真相源来自 OpenCode API：

- `/event` / `/global/event`
- `/session/{id}/message?limit=1`
- `/session/{id}/todo`
- `/session/status`
- `/permission`
- `/question`
- 必要时 `/pty`

## 2. OpenClaw 是监控编排与通知方
OpenClaw 负责：

- 派发 OpenCode 任务
- 启动 watcher
- 保存本地监控状态
- 异常/结束时通知用户

## 3. 本地状态文件的真实用途
本地状态存储只服务于 **OpenClaw watcher 自己**：

- 记录上次看到的 message/todo/version
- 去重通知
- 在 watcher / gateway 重启后恢复
- 让 cron 或 agent 只读小状态，而不是重读 OpenCode 会话

它不是 OpenCode 的工作文件，也不需要 OpenCode 能访问。

---

# 二、推荐总体架构

## Phase 1（建议先做）
先不写 OpenCode plugin，不先写 OpenClaw plugin。

只做：

1. **OpenClaw skill：`opencode-watch`**
2. **本地 watcher 脚本**（运行在 OpenClaw 侧）
3. **OpenCode prompt contract**（要求 todo/checkpoint/final format）
4. **低频通知机制**（基于 watcher 本地状态，而不是远端 transcript）

## Phase 2（后续增强）
如果 Phase 1 跑顺，再考虑：

- OpenCode plugin：标准化 checkpoint/status 输出
- OpenClaw plugin/tool：watch start/stop/status/list 原生工具化

---

# 三、Phase 1 详细方案

## 1. OpenClaw skill：`opencode-watch`

### 作用
把下面这些动作封装成稳定工作流：

- 启动监控
- 停止监控
- 查看监控状态
- 生成适合被监控的 OpenCode prompt contract
- 在必要时触发最终总结

### 建议结构

```text
skills/opencode-watch/
├── SKILL.md
├── scripts/
│   ├── opencode_watch.py
│   ├── opencode_watch_check.py
│   └── opencode_watch_contract.txt
└── references/
    ├── opencode-api-notes.md
    └── watch-state-schema.md
```

---

## 2. Watcher 脚本（运行在 OpenClaw 侧）

### 建议文件

```text
skills/opencode-watch/scripts/opencode_watch.py
```

### 职责
- 连接 OpenCode API
- 优先订阅 event stream
- event 不可用时降级到轮询
- 维护本地状态文件
- 检测 completed / failed / blocked / stalled / deviated
- 输出结构化事件供 OpenClaw 使用

### 重要说明
这个 watcher **不需要运行在 OpenCode 节点上**。  
只要 OpenClaw 能访问 OpenCode API，它就能工作。

---

## 3. 本地状态存储

### 建议位置

```text
runtime/opencode-watch/
```

### 建议文件

```text
runtime/opencode-watch/<watch-id>.json
runtime/opencode-watch/<watch-id>.events.jsonl
```

### 这份状态缓存记录什么

```json
{
  "watchId": "fix-telegram-topic-routing",
  "baseUrl": "http://100.126.131.48:4096",
  "sessionId": "ses_xxx",
  "sessionUrl": "http://100.126.131.48:4096/.../session/ses_xxx",
  "status": "running",
  "phase": "testing",
  "lastSeenMessageId": "msg_xxx",
  "lastCompletedMessageId": "msg_yyy",
  "lastUpdatedMs": 1772903761885,
  "lastTodoDigest": "sha256:...",
  "pendingPermission": false,
  "pendingQuestion": false,
  "stalled": false,
  "result": null,
  "notify": {
    "channel": "telegram",
    "to": "-1003607560565:topic:3348"
  }
}
```

### 它的价值
- watcher 重启后继续盯
- 避免重复读取和重复通知
- 让 OpenClaw 只看小状态文件，不读远端全文

---

## 4. 远端状态如何获取

### 远端真相源只来自 OpenCode API
推荐优先级：

1. **event stream**
2. **todo**
3. **latest message / latest assistant finish**
4. **permission/question**
5. 必要时 `pty`

### 不应该依赖的东西
- 不依赖 OpenCode 与 OpenClaw 共享目录
- 不依赖直接读远端 repo 里的 `status.json`
- 除非该文件本身能通过 OpenCode API 被可靠读取

---

# 四、如何判断会话结束 / 异常 / 卡住

## 1. completed
满足任一：
- 最新 assistant message `completed != null` 且 `finish = stop`
- 或最终 checkpoint 明确表明任务结束

## 2. failed
- tool/bash 明确失败
- 最新结果明确包含失败结论

## 3. blocked_permission
- `/permission` 出现该 session 相关请求

## 4. blocked_question
- `/question` 出现该 session 相关问题

## 5. stalled
- 长时间没有新 message / todo / tool 输出变化
- 且 session `updated` 不推进
- 按任务类型设置阈值，例如：
  - 普通实现任务：10~15 分钟
  - build/test：20~40 分钟

## 6. deviated
- 任务没有按预设 phase 推进
- 或出现明显偏离预期的 checkpoint / 输出
- 尽量先规则化判断，必要时才调用 agent

---

# 五、如何检查“是否符合预期”

不要让 agent 高频做阅读理解，而是在 watch 配置中预先声明预期。

### 示例

```json
{
  "expectedPhases": ["investigate", "implement", "test", "commit", "push"],
  "maxSilenceSeconds": 900,
  "maxPhaseSeconds": {
    "investigate": 900,
    "implement": 1800,
    "test": 2400,
    "commit": 600,
    "push": 600
  }
}
```

### 优先用这些信号判断
1. todo 变化
2. checkpoint 变化
3. assistant finish/tool state
4. 必要时才调 agent 做语义判定

---

# 六、OpenCode 任务的 Prompt Contract

Phase 1 不先做 OpenCode plugin，但建议每次派任务时都带一个“监控友好约定”。

### 约定内容
1. 维护 todo
2. 每个关键阶段输出短 checkpoint
3. 最终输出固定格式结果

### 建议 checkpoint 形式

```text
CHECKPOINT: root cause confirmed
CHECKPOINT: patch applied
CHECKPOINT: tests passed
CHECKPOINT: pushed ba538f5d...
```

### 建议最终输出格式
- root cause
- files changed
- tests run
- commit hash
- pushed branch

### 为什么这样做
因为 watcher 只需要读：
- todo
- 最后几条短 checkpoint
- 最终固定格式结果

不需要重读整个 transcript。

---

# 七、定时巡查、事件巡查与 no-change 策略

这一部分是对 V2 的重要补充：

**定时巡查的目的不只是“发现变化”，而是给 agent 一个周期性的决策机会。**

因此，是否触发 agent、是否形成动作、是否沉淀上下文，应该拆开处理。

## 1. 定时巡查应始终触发决策层
到达定时点时，应始终触发一次 agent 判定，
**不以“是否检测到变化”为前置条件**。

也就是说：
- 有变化，要判定
- no-change，也要判定
- 信息不足，也要判定

这里的 agent 不需要重读完整 transcript，
而应只读取 watcher 提供的**紧凑状态快照 + 增量摘要**。

## 2. no-change 也是有效输入
no-change 只表示“本轮巡查没有检测到显式状态变化”，
不等于“什么都不用做”。

agent 仍可能基于当前整体上下文决定：
- 是否打断或停止当前会话
- 是否发起新的询问
- 是否做纠正或补救动作
- 是否继续静默观察

所以：
**no-change 巡查不应在触发层被直接跳过。**

## 3. 连续性应由外部状态承载，而不是靠聊天上下文硬扛
低频定时巡查和事件触发巡查，应共享同一份 watcher state：

- `lastSeenMessageId`
- `lastCompletedMessageId`
- `lastTodoDigest`
- `lastUpdatedMs`
- `phase`
- `status`
- `lastDecision`
- `lastNotifiedState`

这样无论是 event trigger 还是 timed trigger，
都可以基于同一份连续状态只看增量，而不是各自重新理解世界。

## 4. 上下文沉淀应按结果决定
需要明确区分三件事：

1. **定时点到了没有**
2. **agent 要不要动作**
3. **这次巡查要不要写入上下文/记忆**

建议策略：

- **定时触发**：始终发生
- **agent 判定**：始终发生
- **上下文沉淀**：按价值决定是否发生

具体来说：
- **无动作 / no-op**：通常不保留完整上下文，最多记录轻量巡查痕迹
- **形成动作**：写入上下文 / 状态 / 事件日志
- **有重要结论但暂不动作**：可只沉淀摘要，不保留完整推理链路

## 5. 对成本的影响
这并不意味着要高频烧 token。

因为定时触发时给 agent 的输入应当是：
- watcher 本地状态
- 自上次判定以来的增量
- 必要时的最后一条/几条 checkpoint

而不是整段 OpenCode transcript。

所以正确的实现不是：
- 每次定时都让 agent 重读全文

而是：
- 每次定时都让 agent 基于**压缩状态**做决策
- 只有在形成动作或发现异常时，才进一步扩大阅读范围

## 6. 推荐分层
为了避免实现上把“是否触发”“是否行动”“是否记忆”耦在一起，建议明确拆为三层：

### Scheduler
负责按时间计划触发定时巡查。

### Decision Agent
负责基于 watcher state + delta 判断：
- no-op
- stop / interrupt
- ask / correct
- notify
- escalate

### Memory / Context Policy
负责决定本轮巡查是否沉淀：
- 完整记录
- 轻量摘要
- 只留时间戳/痕迹
- 完全不写主会话

---

# 八、当用户要求“用 OpenCode 开发功能”时，watcher 应该以什么形态触发？

这是一个单独的重要设计点。

## 结论先说
**watcher 本身最适合是“本地独立进程/脚本”**，而不是 agent。  
但“谁来启动它、谁来兜底通知”，可以有几种选择。

---

## 方案 A：main cron

### 适合做什么
- 低频读取 watcher 本地状态
- 在状态变化时通知用户
- 做轻量兜底检查

### 优点
- 天然有主会话上下文
- 通知容易接回当前对话
- 实现快

### 缺点
- 不适合承担持续监控本身
- 可审计性一般
- 之前已经验证过：`main + systemEvent` 容易出现“调度 ok，但实际执行痕迹不清”
- 在 Telegram topic 场景还可能受路由历史/heartbeat行为影响

### 结论
**适合作为通知层和轻量兜底层，不适合作为核心 watcher。**

---

## 方案 B：isolated cron

### 适合做什么
- 周期性、独立地检查 watcher 本地状态
- 或低频直接读取 OpenCode API 的轻状态

### 优点
- 每次运行有独立 run 痕迹
- 审计比 main cron 清楚
- 不污染主会话上下文
- 可以更稳定地做定时检查

### 缺点
- 如果每次都让 isolated agent 去读 OpenCode transcript，仍然会烧 token
- 要显式配置 delivery 目标
- 如果只靠 isolated cron 而没有本地 watcher，本质还是“间歇性巡检”，不是实时监控

### 结论
**适合作为 watcher 的低频兜底/通知编排，但不应代替实时 watcher。**

---

## 方案 C：subagent

### 适合做什么
- 在任务结束时做一次总结
- 在发生 `deviated` / `stalled` 时做较复杂分析
- 处理需要语义判断的异常情况

### 优点
- 适合复杂总结与诊断
- 和主会话分离
- 能承接较长上下文

### 缺点
- 不适合高频监控
- 持续轮询会非常浪费 token
- 仍然是 agent，不是机械 watcher

### 结论
**适合作为“异常/完成后的分析器”，不适合作为持续 watcher。**

---

## 方案 D：本地独立 watcher 进程（推荐）

### 适合做什么
- 持续监听 OpenCode event
- 断线后轮询兜底
- 维护本地状态机
- 只在状态变化时产出事件

### 优点
- 最低 token 成本
- 真正接近实时
- 可审计
- 不依赖 agent 持续工作
- 能跨节点工作，只要 OpenClaw 能访问 OpenCode API

### 缺点
- 需要写脚本/小服务
- 需要管理生命周期

### 结论
**这是核心 watcher 的最佳形态。**

---

## 方案 E：OpenCode 插件主动上报（后续增强）

### 适合做什么
- 任务内部主动发送 checkpoint/status/result
- 标准化 phase 更新

### 优点
- 状态最结构化
- watcher 最轻松

### 缺点
- 需要改 OpenCode 本体/生态
- 不适合作为第一版起步条件

### 结论
**适合作为 Phase 2，而不是 MVP。**

---

## 推荐组合

### 最佳第一版组合
- **核心监控**：本地独立 watcher 进程
- **低频定时决策 / 兜底 / 通知**：isolated cron 或 main cron（更建议 isolated cron 做定时 decision turn 与检查，主会话做最终对话通知）
- **复杂分析**：subagent（仅异常/完成时触发）

### 不推荐
- 让 main cron 或 subagent 本身承担持续高频监控

---

# 九、推荐的完整流程（修正版）

## 1. 用户要求“用 OpenCode 开发功能，并帮我盯着”

## 2. OpenClaw 做两件事
- 派发 OpenCode 任务（带 prompt contract）
- 启动本地 watcher 进程

## 3. watcher 持续监听 OpenCode API
- event 优先
- poll 兜底
- 更新本地状态文件

## 4. 低频 cron / 定时巡查读取 watcher 本地状态
- 每到定时点，都触发一次基于压缩状态的 decision turn
- 即使 no-change，也进入判定层
- 若本轮结论为 no-op，则通常不打扰用户，也不沉淀完整上下文
- 若出现 completed/failed/stalled/blocked/deviated，则形成动作并通知用户

## 5. 如有需要，异常、完成或定时巡查判定需要升级时，再触发 subagent
- 做一次最终总结或复杂分析
- 不参与高频监控

---

# 十、为什么这版比“定时让 agent 去看 OpenCode”更合理

因为它把系统职责拆开了：

- **Watcher**：机械观察与状态机
- **Cron**：调度与低频通知
- **Subagent**：只在真正需要智能判断时出手
- **Main session**：只保留用户沟通

这样可以同时得到：
- 更低 token 消耗
- 更强可审计性
- 更少重复阅读
- 更清晰的错误边界

---

# 十一、建议的第一版交付物

## OpenClaw side
- `skills/opencode-watch/SKILL.md`
- `skills/opencode-watch/scripts/opencode_watch.py`
- `skills/opencode-watch/scripts/opencode_watch_check.py`
- `skills/opencode-watch/scripts/opencode_watch_contract.txt`
- `runtime/opencode-watch/*.json`
- `runtime/opencode-watch/*.jsonl`

## OpenCode side
- Phase 1：不改本体
- 采用标准监控友好 prompt contract

---

# 一句话结论

最合理的第一版不是“靠 main cron / subagent 盯 OpenCode”，而是：

> **用本地独立 watcher 进程做核心监控；用 cron 做低频兜底和通知；只在异常或完成时让 agent/subagent 出手。**
