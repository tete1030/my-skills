# OpenCode watcher / inspect 输出瘦身方案（草案）

> 日期：2026-03-18  
> 状态：提案（**不改代码**，先评审）

## 1. 背景问题

当前 `inspect` / watcher 相关 JSON 在以下场景会过重：

- tool output 字段过长（stdout、tail、多层 detail）
- 长 ID（messageId / uuid / callId）在默认视图里占据大量上下文
- agent 经常只需要“当前进展 + 最终结论”，但被迫读取大量结构化细节
- `inspect-history` 虽可钻取，但实际使用时容易变成默认依赖路径

你提出的方向非常明确：

1. 默认不再输出大 JSON（或至少不是 JSON-first）
2. 默认只保留“序号 + 简短文本”
3. 细节按序号单条展开
4. 不鼓励把 `inspect-history` 作为常规路径

---

## 2. 目标与非目标

## 2.1 目标

- **默认轻量**：单次 `inspect` 结果应适合直接给 agent 读，不挤爆上下文。
- **顺序可追踪**：每条事件有短序号（如 `#01 #02`），可点查。
- **细节按需**：只在显式请求时展开某一条。
- **结论优先**：完成态优先给出“最终完整结论段落”。
- **运行态优先**：运行中优先给“当前阶段 + 最近有效进展”。

## 2.2 非目标

- 不在本阶段重做 watcher 状态机。
- 不引入 SSE 依赖。
- 不引入 auto-approval。
- 不移除底层 JSON 能力（可保留 debug / 兼容通道）。

---

## 3. 设计总览（默认轻量 + 按序号展开）

## 3.1 inspect 默认输出改为“轻量文本视图”（human/agent-first）

建议 `inspect` 默认输出由“JSON 大对象”改为“紧凑文本块”（Markdown 友好）：

- 顶部摘要：`status / phase / blocker(如有) / progress`
- 时间顺序清单（仅最近 N 条，默认 6~10 条）：
  - `[#01] user: ...`
  - `[#02] tool[read]: 读取 xxx`
  - `[#03] tool[bash]: 运行测试（完成）`
  - `[#04] assistant: 已完成 xxx`
- 底部提示：`如需细节，执行 --expand-index <n>`

**默认不显示**：

- 长 uuid / messageId / callId
- tool 输入/输出全文
- patch/full stdout

> 说明：这不是“丢信息”，而是默认隐藏，改为按需展开。

## 3.2 细节展开：`inspect --expand-index <n>`

新增“单条展开”模式（优先替代常规 `inspect-history`）：

- 输入：一个序号（来自默认 inspect 列表）
- 输出：该条的详细内容（接近当前 inspect-history 粒度）
  - tool command / 目标文件 / 输出 tail / patch 摘要
  - 必要时显示原始 id（建议默认仍短化，`--show-ids` 才全量）

这样 agent 的交互会变成：

1. 先看轻量列表定位问题（几百 token 级别）
2. 仅对 1 条事件做展开（局部高密度）

## 3.3 JSON 退到显式模式（兼容/调试）

为了兼容已有脚本与测试，建议保留：

- `inspect --format json` 或 `inspect --json`

但默认走轻量文本，避免“每次 inspect 都注入大 JSON”。

---

## 4. 输出内容策略（围绕你最关心的两件事）

## 4.1 运行时（最重要：当前大概进展）

默认 inspect 顶部固定给：

- 当前状态（running/blocked/...）
- 当前阶段（phase）
- 最近有效进展一句话（latest meaningful progress）
- 若 blocked：阻塞摘要（permission/question 简述）

事件列表只保留“动作语义”，例如：

- `tool[read]: 读取 scripts/opencode_snapshot.py`
- `tool[edit]: 修改 scripts/opencode_remote_cycle.py`
- `tool[bash]: 执行 unittest（完成）`

不默认带 output 内容。

## 4.2 完成后（最重要：最终完整结论段落）

当状态为 completed/failed/blocked-terminal：

- 优先抽取最后 assistant 的“结论文本段落”（完整展示，不截成碎片）
- 若最后是 tool-only，无结论文本，则退回“最终摘要行 + 关键动作清单”

这样 agent 在完成态无需读历史细节，也能直接拿到可回复用户的结论。

---

## 5. inspect-history 的定位调整（不鼓励默认使用）

建议策略：

- 文档与提示中把 `inspect-history` 标记为 **debug/legacy** 通道
- 常规细节查看改为 `inspect --expand-index`
- 仅在“跨窗口追溯老消息”或“需要 message-id 精确定位”时才建议 `inspect-history`

也就是把“细节钻取入口”统一到 inspect 自身，减少心智分叉。

---

## 6. 结构草图（示例）

## 6.1 默认 inspect（轻量）

```text
Session: ses_demo
Status: running
Phase: Implement watcher blocker summary
Progress: 已完成 snapshot 归一化，正在补 manager inspect 字段

Recent timeline:
[#01] user: 继续按这个方向推进
[#02] tool[read]: 读取 opencode_snapshot.py
[#03] tool[edit]: 修改 opencode_remote_cycle.py
[#04] tool[bash]: 运行 tests（通过）
[#05] assistant: 已完成 Phase 1 结构化 blocker 字段

Tip: 查看 #03 细节 -> inspect --expand-index 3
```

## 6.2 单条展开

```text
Expand #03
Type: tool[edit]
Target: scripts/opencode_remote_cycle.py
Action: 新增 blockedPromptKey / blockedSummary 观测字段
Patch summary:
- derive_status 改为优先看 normalized pendingPrompts
- noChange 判定新增 blocker identity 变化
Output tail:
- Successfully replaced text in ...
```

---

## 7. 实施分阶段建议

## Phase A（最小可用，优先）

- inspect 默认输出改轻量文本
- 增加序号映射
- 增加 `--expand-index`
- 完成态优先提取“最终结论段落”
- 保留 `--format json` 兼容

## Phase B（质量提升）

- tool 行为摘要优化（read/edit/bash 的更友好中文短句）
- blocker 行文模板统一（permission/question）
- 长文本折叠策略优化（不丢语义）

## Phase C（文档与行为收口）

- SKILL/README 调整：默认 inspect + expand；inspect-history 降级为 debug
- watcher 注入提示词中减少对 inspect-history 的主路径暗示

---

## 8. 验收标准（建议）

1. 默认 inspect 输出体积相较当前下降显著（目标：**至少 60%+**）
2. 默认 inspect 不出现长 uuid/id（除非显式 `--show-ids`）
3. agent 在运行态可仅凭默认 inspect 回答“现在进展到哪了”
4. agent 在完成态可仅凭默认 inspect 拿到“最终完整结论段落”
5. 细节查看可通过单条 `--expand-index` 完成，不需要先跳 inspect-history

---

## 9. 兼容性与风险

- 风险：已有依赖 JSON 默认输出的脚本会受影响。  
  - 规避：保留 `--format json`，并在迁移期给明确提示。
- 风险：轻量摘要可能丢失少量上下文。  
  - 规避：单条展开必须可达当前 inspect-history 级别细节。
- 风险：序号在不同时间窗口会变化。  
  - 规避：序号只作为“当前快照内定位”，必要时支持 `--show-ids`。

---

## 10. 你需要拍板的点

1. 默认格式是否接受“文本视图优先，JSON 显式开启”？
2. `inspect-history` 是否保留但降级为 debug（文档弱化）？
3. 完成态“最终完整结论段落”是否作为固定必出块？
4. 是否需要 `--show-ids`（默认隐藏，调试时显示）？

---

如果你确认这个方向，我再按 Phase A 先做最小改动并给你一个前后对比样例（同一 session：旧 inspect vs 新 inspect）。
