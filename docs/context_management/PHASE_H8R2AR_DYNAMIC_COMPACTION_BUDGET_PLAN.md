# Phase H8-R2AR：Compact 动态 summary budget 计划

## 背景与问题

当前 `RollingSummaryAdapter` 已能验证真实 DeepSeek 的 bounded structured summary，
但 `MemoryContextBuilder` 传给 summary factory 的 `max_summary_tokens` 仍是固定静态
cap。这样无法根据本轮已经使用的 prompt、required context、最近对话 suffix 和输出
schema 保留动态缩小 summary 槽，也无法在剩余槽为零时明确跳过 Provider summary。

## 目标

保留静态上限作为硬 cap，同时引入动态上限：

```text
dynamic_summary_cap = min(
    static_cap,
    requested_prompt - used_prompt - required_reserve
      - recent_suffix_reserve - response_schema_reserve,
)
```

动态槽只控制非 required 的 derived summary；required constraint、用户原话、系统
指令和最近 suffix 不得被 summary 挤出。动态槽为零或 tokenizer 不可用时，不调用
summary factory，保留现有 deterministic source view。

## 实施边界

- 复用已有 `calculate_summary_budget`，不新建预算事实副本。
- 不改变默认 `rolling_summary_enabled=False`。
- 不改变 `ContextCompactionRecord`、`ContextCompactionBinding` 或 source authority。
- provider/factory 仍是 untrusted derived view；invalid/overlong/stale/unknown usage
  继续 fallback。
- `response_schema_reserve` 是内部固定保留槽，不代表 Provider completion budget 已
  被消费；真实 usage 仍由 summary attempt evidence 记录。

## 测试与门禁

1. 有 exact tokenizer 时，factory 收到的 cap 必须随剩余 prompt 下降且不超过静态 cap。
2. required/recent reserve 吃满剩余槽时，factory 不被调用，deterministic record 保留。
3. 无 token budget 时保持现有静态 cap兼容；flag off 时 factory 永不调用。
4. 动态 cap 变化不得改变 source IDs、lineage、required constraint 或 artifact 原子性。
5. 运行 compaction summary、rolling integration、context governance 和完整实验目录
   offline suite；失败不得进入真实 Provider shadow。

## 退出条件

只有离线门禁全通过才进入下一阶段的真实 DeepSeek summary shadow。该阶段本身不改变
默认 Compact，不声明语义质量收益或全局 token 最优。
