# OpenCode observer 边界说明（2026-03-09）

这份笔记只记录当前 observer / event-model 的边界与验证结论，避免后续实现继续漂移。

## 当前模型

- observer 先把最近消息规整成结构化 `eventLedger`；当前可见事件类型以 `user_input`、`text`、`read`、`tool[...]`、`prune` 为主，并保留 `ignored` 元数据。
- `user_input` 现在是可见事实源：最新的非 `ignored` 用户输入会进入 `latestUserInputSummary`，也会被优先保留进 `accumulatedEventSummary`。
- `ignored=true` 的 plugin input 仍可作为原始事件留在 ledger 里做调试，但不会参与 `latestMessage` 选择，也不会进入 `latestUserInputSummary` 或 `accumulatedEventSummary`。
- `accumulatedEventSummary` 的职责，是把最近一小段“有意义事件轨迹”压成机械摘要，供 turn 决策和主 agent 消费；它不是用户文案，也不应退化成 raw stdout dump。
- `turn.factSkeleton.latestMeaningfulPreview` 应视为 `accumulatedEventSummary` 的短指针（必要时再回退到 assistant/text preview），目的是把 observer 侧事实带过边界，而不是再造一层 narrative summarizer。

## 非目标 / 边界

- observer / summarizer 不负责 narrative layer；最终可见表达仍由 main-session agent 决定。
- bridge / delivery path 不变：`turn -> delivery-handoff -> openclaw-agent-call -> openclaw gateway call agent(sessionKey=originSession) -> main agent`。
- 不是每个 tool call 都要触发 visible update；是否可见仍由 cadence + main agent 决定。

## 已验证

- 目前验证仅在 dedicated test directory/session（`/mnt/vault/test-opencode-skill` + topic `4029`）完成。
- live injection 到 topic `4029` 已打通。
- 仅 cadence / no-change 变化时，dedupe / idempotency 稳定，不会重复可见投递。
- 出现新的真实事实变化时，会生成新的 update / action key，并触发新的投递。
- `accumulatedEventSummary` 产生的事实现在能穿过 turn 边界并到达 main agent（当前主要经 `latestMeaningfulPreview`）。

## 已知 caveats / 下一步

- 还没在更广泛真实 session 上验证；暂不把当前行为当作“任意会话都生产可用”。
- 混合型 preview（`user | read | tool | text`）虽然更完整，但可读性和截断策略后面可能还要再微调。
- 后续如果补测试，优先补 permission / question / blocked 和更多 plugin 变体；不要借这个口子扩大 observer 的 narrative 职责。
