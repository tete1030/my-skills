# OpenCode 会话监控方案提案

## 目标

实现一套低 token 成本、可审计、适合 OpenClaw 编排的 OpenCode 监控方案，满足：

- 实时发现会话结束
- 低频/低成本检查任务是否符合预期
- 异常、卡住、权限阻塞时及时通知
- 避免高频让 agent 重读 OpenCode transcript

---

## 设计原则

1. **观察与判断分离**
   - 观察尽量机械化（API / event / state file）
   - 只有少数情况才调用 agent 做语义判断

2. **优先增量，不重复阅读**
   - 只追踪新 message / 新 todo / 新状态
   - 不定时重复读取整段 session 历史

3. **事件驱动优先，轮询兜底**
   - 优先使用 OpenCode event stream
   - event 断线时降级到低频轮询

4. **通知与监控解耦**
   - watcher 只产出状态变化
   - OpenClaw 只在必要时发消息

---

## 推荐实施路径

分成两个阶段：

### Phase 1（推荐先做）
不改 OpenCode 本体，不先写 plugin，先做：

- 一个 **OpenClaw skill**：`opencode-watch`
- 一个 **watcher 脚本**：负责 event/poll/state machine
- 一个 **prompt contract**：要求 OpenCode 任务维护 todo/checkpoint/final output
- 一个 **低频 cron**：只读 watcher 状态文件，必要时通知

### Phase 2（后续增强）
在 Phase 1 跑顺后，再考虑：

- OpenCode plugin：标准化 phase/checkpoint/status 输出
- OpenClaw plugin/tool：原生 watch start/stop/status/list

---

# Phase 1 方案

## 1. OpenClaw Skill：`opencode-watch`

### 作用
负责把“启动监控 / 停止监控 / 查看监控状态 / 提示 OpenCode 按监控友好方式执行任务”封装成稳定工作流。

### 建议目录结构

```text
skills/opencode-watch/
├── SKILL.md
├── scripts/
│   ├── opencode_watch.py
│   ├── opencode_watch_state.py
│   ├── opencode_watch_check.py
│   └── opencode_watch_contract.txt
└── references/
    ├── opencode-api-notes.md
    └── watch-state-schema.md
```

### Skill 主要能力
- 启动某个 OpenCode session 的监控
- 停止监控
- 查询当前监控状态
- 生成适合被监控的 OpenCode prompt contract
- 决定是否需要调 agent 做最终总结

---

## 2. Watcher 脚本

### 建议文件

```text
skills/opencode-watch/scripts/opencode_watch.py
```

### 职责
- 连接 OpenCode API
- 优先订阅 `/event` 或 `/global/event`
- 若 event 不可用/断线，则低频轮询
- 维护本地状态文件
- 检测完成、失败、阻塞、卡住、偏离预期
- 把状态变化写成结构化事件

### 建议输入参数

```bash
python3 opencode_watch.py \
  --base-url http://100.126.131.48:4096 \
  --session-id ses_xxx \
  --workspace-b64 L21udC92YXVsdC9vcGVuY2xhdw \
  --watch-id tg-topic-3348-fix1 \
  --mode event+poll \
  --poll-seconds 120
```

### 监控时优先读取的 OpenCode API
- `/event` 或 `/global/event`
- `/session/{id}/message?limit=1`
- `/session/{id}/todo`
- `/session/status`
- `/permission`
- `/question`
- 必要时 `/pty`

### 不应该做的事
- 不要高频读取整段 `/message`
- 不要每几分钟让 agent 重做一次“阅读理解”
- 不要把 transcript 当作主状态源

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

### 状态文件示例

```json
{
  "watchId": "tg-topic-3348-fix1",
  "baseUrl": "http://100.126.131.48:4096",
  "sessionId": "ses_336b97e52ffeDrlsn48j4p95fA",
  "workspacePath": "/mnt/vault/openclaw",
  "workspaceB64": "L21udC92YXVsdC9vcGVuY2xhdw",
  "sessionUrl": "http://100.126.131.48:4096/L21udC92YXVsdC9vcGVuY2xhdw/session/ses_336b97e52ffeDrlsn48j4p95fA",
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

### 事件日志示例

```jsonl
{"ts":"2026-03-08T01:10:00Z","type":"phase_changed","from":"implement","to":"testing"}
{"ts":"2026-03-08T01:15:00Z","type":"completed","commit":"ba538f5dfc17ea430d7867291ff38dbfb786ee4b"}
```

---

## 4. 状态机设计

### 核心状态
- `running`
- `completed`
- `failed`
- `blocked_permission`
- `blocked_question`
- `stalled`
- `deviated`

### 状态切换依据

#### completed
满足任一：
- 最新 assistant message `completed != null` 且 `finish = stop`
- 或最终 checkpoint / status 文件明确标记完成

#### failed
- tool/bash 返回失败
- 最新结果明确 error/failure

#### blocked_permission
- `/permission` 中出现该 session 相关请求

#### blocked_question
- `/question` 中出现该 session 相关问题

#### stalled
- 长时间没有新 message / 新 tool 输出 / 新 todo 变化
- 且 `updated` 时间不再推进
- 按任务类型设置阈值，例如：
  - 普通实现任务：10~15 分钟
  - 构建/测试任务：20~40 分钟

#### deviated
- 任务没有按预设 phase 推进
- 或出现明显不符合预期的 checkpoint / 输出模式
- 这个状态最好尽量基于结构化规则判断；必要时才调 agent

---

## 5. 如何判断“符合预期”

不要靠 agent 每次重读全文，而是把预期前置为结构化约束。

### 建议 watch 配置里记录

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

### 判断优先级
1. `todo` 变化
2. `status.json` / checkpoint
3. 工具状态变化
4. 必要时才让 agent 做语义判断

---

## 6. OpenCode Prompt Contract

Phase 1 不做 OpenCode plugin，但强烈建议在派任务时加一段“监控友好约定”。

### 建议要求 OpenCode 任务：

1. 维护 todo
2. 每个关键阶段输出短 checkpoint
3. 最终输出固定格式结果
4. 可选：写入 `status.json`

### 推荐 checkpoint 形式

```text
CHECKPOINT: root cause confirmed
CHECKPOINT: patch applied
CHECKPOINT: tests passed
CHECKPOINT: pushed ba538f5dfc17ea430d7867291ff38dbfb786ee4b
```

### 推荐最终输出格式
- root cause
- files changed
- tests run
- commit hash
- pushed branch

### 可选状态文件

```text
.openclaw-status/opencode-watch.json
```

示例：

```json
{
  "task": "telegram-topic-heartbeat-fix",
  "phase": "testing",
  "updatedAt": "2026-03-08T01:12:00Z",
  "blocker": null,
  "result": null
}
```

这样 watcher 基本不需要读 transcript，只要读状态文件即可。

---

## 7. OpenClaw 这边的通知策略

### 不建议
- 每 10 分钟让 agent 读 OpenCode session 再总结
- 高频 main-session cron + 大段 transcript 阅读

### 建议
用 cron 低频读取 watcher 的本地状态文件：

- 若状态仍 `running` 且无异常 → `NO_REPLY`
- 若状态变成 `completed` → 通知用户
- 若状态变成 `failed / stalled / blocked_* / deviated` → 通知用户

这样 agent 读的是本地状态，而不是远端 transcript。

---

## 8. 完整流程（Phase 1）

### 1. OpenClaw 创建/接管 OpenCode session
获得：
- `baseUrl`
- `workspace`
- `sessionId`
- `sessionUrl`

### 2. OpenClaw 向 OpenCode 派任务
同时附带 prompt contract：
- 维护 todo
- 输出 checkpoint
- 最终固定格式结果

### 3. OpenClaw 启动 watcher
写入 watch config：
- session 信息
- notify 目标
- expected phases
- stall threshold

### 4. watcher 开始监听
- 优先 event
- event 断线则 poll
- 维护本地状态机

### 5. watcher 只产出状态变化
例如：
- phase changed
- completed
- stalled
- blocked

### 6. OpenClaw cron 低频检查 watcher state
- 正常不打扰
- 异常或结束才通知

### 7. 如有需要，再让 agent 做一次“最终摘要”
只在任务结束时做一次，而不是周期性做。

---

# Phase 2（增强版）

当 Phase 1 跑顺后，再考虑插件化。

## A. OpenCode Plugin（可选）

### 适合做的事
- 提供标准化 phase/checkpoint/result 上报能力
- 自动写 `status.json`
- 或提供 webhook 风格事件推送

### 可能的 plugin 形态
- `watch-contract`
- `checkpoint-emitter`
- `task-status`

### 价值
把“靠 prompt 自觉维护状态”升级成“程序化保证状态输出”。

---

## B. OpenClaw Plugin / Tool（可选）

### 适合提供的原生工具
- `opencode_watch_start`
- `opencode_watch_stop`
- `opencode_watch_status`
- `opencode_watch_list`

### 价值
让“盯一个 OpenCode 会话”变成原生能力，而不再依赖 skill + 脚本拼装参数。

---

# 为什么这个方案省 token

## 传统方式
每隔几分钟让 agent：
- 读取 session
- 理解当前状态
- 判断要不要通知

问题：
- 重复读
- 高 token 消耗
- 审计痕迹不清楚
- 容易混入主会话上下文

## 新方式
绝大部分时间：
- event stream / 轻轮询
- 读状态文件
- 不用 agent

只有在：
- 结束
- 异常
- 卡住
- 偏离预期

才调用 agent 或发通知。

---

# 推荐优先级

## 先做
1. `opencode-watch` skill
2. watcher 脚本
3. prompt contract
4. 基于状态文件的低频通知

## 后做
1. OpenCode plugin
2. OpenClaw plugin/tool

---

# 建议的第一版交付物

## OpenClaw side
- `skills/opencode-watch/SKILL.md`
- `skills/opencode-watch/scripts/opencode_watch.py`
- `skills/opencode-watch/scripts/opencode_watch_check.py`
- `skills/opencode-watch/scripts/opencode_watch_contract.txt`
- `runtime/opencode-watch/*.json`
- `runtime/opencode-watch/*.jsonl`

## OpenCode side
- Phase 1：无需改本体
- 仅采用监控友好 prompt contract

---

# 一句话总结

最合理的第一版不是“做很多插件”，而是：

> **OpenClaw skill + watcher 脚本 + OpenCode prompt contract + 低频状态通知**

这样最省 token、最容易落地、最适合先验证工作流。
