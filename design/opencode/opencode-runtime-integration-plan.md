# OpenCode Runtime Integration Plan

## 目标

把当前 skill 侧已经收敛好的链条，真正接进 OpenClaw runtime，使主路径变成：

`turn -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent(sessionKey=originSession) -> main-session agent decides visible reply`

并保持以下原则：

- 主 session 是决策中心与叙事中心
- 脚本层只输出事实骨架 / cadence / routing
- 不在脚本层生成最终用户文案
- 投递目标始终回到原始任务发起 session

---

## 当前状态

skill 侧已经具备：

1. `turn`
   - 产出结构化结果：`factSkeleton`, `shouldSend`, `delivery`, `cadence`

2. `delivery-handoff`
   - 可直接消费 `turn` 结果
   - 生成 origin-session handoff
   - 保留 `originSession` / `originTarget`
   - 对冲突做 `hold`

3. `openclaw-agent-call`
   - 可直接消费 `delivery-handoff` 结果
   - 生成/执行 `openclaw gateway call agent --params { sessionKey, message, deliver }`
   - 用 `sessionKey` 回到 originating session
   - 默认 dry-run，避免误投递

也就是说，**skill 侧 practical handoff 语义与桥接路径都已落位**。

当前不再需要额外 Node/plugin 注入层；
缺口已经从“runtime 另写 injector”收敛为“如何在 watcher/orchestrator 中调用既有 OpenClaw CLI bridge”。

---

## 设计原则

### 1. 主路径采用事件驱动，不采用 cron 轮询
watcher / orchestrator 应优先支持：

- handoff 就绪后，立即调用 `openclaw-agent-call`
- 由该桥接层执行 `openclaw gateway call agent` 回到 `originSession`

而不是：

- 等待另一个 cron 去轮询消费

### 2. 桥接层负责投递，不负责改写语义
桥接层只负责：

- 接受 handoff
- 做最小合法性校验
- 用既有 OpenClaw CLI 把 handoff 投递回 origin session

桥接层不应：

- 改写 handoff 的事实语义
- 生成最终文案
- 把事件改投到当前 helper/debug session

### 3. main-session agent 负责最终解释
originating session 收到这个 handoff 后，主 agent 才是：

- 是否发言
- 怎么表达
- 是否追问 / 纠偏 / 停止

的最终 owner。

---

## 推荐架构

### A. Skill side（已完成）
职责：
- `turn` 生成结构化结果
- `delivery-handoff` 生成 origin-session handoff
- `openclaw-agent-call` 生成/执行 OpenClaw CLI bridge

### B. Watcher / orchestrator（下一步接线重点）
使用既有 OpenClaw CLI，而不是另写 Node/plugin 注入器。

#### 输入
- `delivery-handoff` 的完整结果

#### 行为
- 校验 `deliveryAction == inject`
- 校验 `routeStatus == ready`
- 校验 `originSession` 存在且未被重写
- 执行 `openclaw gateway call agent --params { sessionKey, message, deliver }`

#### 输出
- dry-run plan / accepted / failed / hold 的结构化结果

### C. Main session
origin session 收到该 agent-turn handoff 后：
- 把其中 facts 作为内部运行输入理解
- main-session agent 决定最终可见回复

## 推荐 handoff 格式

桥接调用应保持现有 handoff shape，不再新增 skill 层适配器。

建议继续使用：

- `OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1`
- 结构化 JSON envelope

但**运输方式**改为：

- `openclaw gateway call agent`
- `sessionKey = originSession`
- `message = 包含上述结构化 envelope 的机械输入`
- `deliver = true`

也就是说：

- **语义格式继续复用现有 envelope**
- **运输层复用现有 OpenClaw agent/sessionKey 路径**
- **消费逻辑属于 main session / main agent 处理路径**
- **不再单独抽一个 skill 层 consumer，也不需要新增 Node/plugin injector**

---

## 实现步骤

### Step 1. Watcher 侧调用现有 bridge
新增的最小接线不是 runtime 新模块，而是调用现有 skill 脚本：

- `delivery-handoff`
- `openclaw-agent-call`

此步只做：
- 结构校验
- session 定位
- OpenClaw CLI 调用
- 错误返回

不做 narrative。

### Step 2. 增加 guard
为了避免错误注入或重复注入，bridge / orchestrator 应至少处理：

- `routeStatus != ready` -> 不执行
- `deliveryAction != inject` -> 不执行
- `originSession` 缺失 -> hold
- `systemEventTemplate.sessionKey != routing.originSession` -> refuse
- 同一 handoff id / hash 的短时间重复执行 -> 去重

### Step 3. Main agent 消费实验
做一次真实 end-to-end 验证：

1. OpenCode 产生状态变化
2. `turn` 产生 handoff
3. `openclaw-agent-call` 执行 `gateway call agent` 回到 origin session
4. main-session agent 接收该机械 handoff
5. main-session agent 决定是否发出自然语言回复

验证重点：
- 会不会自然
- 会不会误发
- 会不会漏发
- 是否仍正确保留原始投递目标

## 不建议做的事情

### 1. 不要再新增 skill 层 consumer adapter
例如不再重新引入：
- `origin-session-consume`

因为它已经被证明更像 runtime glue，而不是 skill 主概念。

### 2. 不要让脚本直接生成最终聊天文案
这会重新破坏边界。

### 4. 不要引入当前 helper/debug session 作为默认 fallback 目标
如果 origin 不明确，应 hold，不应 silent reroute。

---

## 我建议的下一步落地顺序

1. **先让 watcher/orchestrator 调用现有 `openclaw-agent-call` bridge**
2. **再做一次真实 end-to-end 实验**
3. **在真实需求出现前，不额外引入 watchdog cron**

优先级非常明确：

> **先打通 origin-session 的 `sessionKey` 注入闭环（经 `gateway call agent`），再做其它。**

---

## 成功标准

达到以下条件，说明这一步完成：

1. `delivery-handoff` 产出的 handoff 能被 `openclaw-agent-call` 真实转成/执行 `gateway call agent`
2. main-session agent 能消费该 handoff 并自然决定最终回复
3. 不需要新增 script-side narrative logic
4. 不需要 cron 才能看到事件
5. 不发生 silent reroute 到 helper/debug session
6. 有最小去重/guard，避免重复执行
