# OpenCode Runtime Integration Plan

## 目标

把当前 skill 侧已经收敛好的链条，真正接进 OpenClaw runtime，使主路径变成：

`turn -> delivery-handoff -> inject origin-session systemEvent -> main-session agent decides visible reply`

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
   - 生成 origin-session `systemEvent` handoff
   - 保留 `originSession` / `originTarget`
   - 对冲突做 `hold`

也就是说，**skill 侧 handoff 语义基本已完成**。

缺口在于：

> runtime 如何真正把这个 handoff 注入 originating session，
> 并让 main-session agent 以系统事件的方式消费它。

---

## 设计原则

### 1. 主路径采用事件驱动，不采用 cron 轮询
runtime 应优先支持：

- handoff 就绪后，直接向 `originSession` 注入 `systemEvent`

而不是：

- 等待另一个 cron 去轮询消费

### 2. runtime 负责注入，不负责改写语义
runtime 只负责：

- 接受 handoff
- 做最小合法性校验
- 向 origin session 注入系统事件

runtime 不应：

- 改写 handoff 的事实语义
- 生成最终文案
- 把事件改投到当前 helper/debug session

### 3. main-session agent 负责最终解释
originating session 收到这个 system event 后，主 agent 才是：

- 是否发言
- 怎么表达
- 是否追问 / 纠偏 / 停止

的最终 owner。

---

## 推荐架构

### A. Skill side（已基本完成）
职责：
- `turn` 生成结构化结果
- `delivery-handoff` 生成 origin-session systemEvent handoff

### B. Runtime injector（下一步实现重点）
新增一个 runtime 侧能力：

#### 输入
- `openclawDelivery.systemEventTemplate`

#### 行为
- 校验 `deliveryAction == inject`
- 校验 `routeStatus == ready`
- 校验 `originSession` 存在
- 将 `payload.kind = systemEvent` 的内容注入到指定 session

#### 输出
- 成功 / 失败 / hold / dropped 的结构化结果

### C. Main session
origin session 收到系统事件后：
- 把其中 facts 作为系统输入交给 main-session agent
- main-session agent 决定最终可见回复

## 推荐事件格式

runtime 注入的对象应保持现有 handoff shape，不再新增 skill 层适配器。

建议继续使用：

- `OPENCODE_ORIGIN_SESSION_SYSTEM_EVENT_V1`
- 结构化 JSON envelope

runtime 只需要识别并注入，不需要 skill 侧再维护单独的 `origin-session-consume` 层。

也就是说：

- **消费逻辑属于 session runtime / main agent 处理路径**
- **不再单独抽一个 skill 层 consumer**

---

## 实现步骤

### Step 1. Runtime 侧增加 handoff injector
新增一个最小能力：

- 接收 `systemEventTemplate`
- 注入到 `originSession`

此步只做：
- 结构校验
- session 定位
- 注入调用
- 错误返回

不做 narrative。

### Step 2. 增加 guard
为了避免错误注入或重复注入，runtime 应至少处理：

- `routeStatus != ready` -> 不注入
- `deliveryAction != inject` -> 不注入
- `originSession` 缺失 -> hold
- 同一 handoff id / hash 的短时间重复注入 -> 去重

### Step 3. Main agent 消费实验
做一次真实 end-to-end 验证：

1. OpenCode 产生状态变化
2. `turn` 产生 handoff
3. runtime 注入 origin session
4. main-session agent 接收系统事件
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

1. **先设计并实现 runtime injector**
2. **再做一次真实 end-to-end 实验**
3. **在真实需求出现前，不额外引入 watchdog cron**

优先级非常明确：

> **先打通 origin-session systemEvent 注入，再做其它。**

---

## 成功标准

达到以下条件，说明这一步完成：

1. `delivery-handoff` 产出的 `systemEventTemplate` 能被 runtime 真实注入 origin session
2. main-session agent 能消费该事件并自然决定最终回复
3. 不需要新增 script-side narrative logic
4. 不需要 cron 才能看到事件
5. 不发生 silent reroute 到 helper/debug session
6. 有最小去重/guard，避免重复注入
