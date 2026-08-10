# Phase H8-R2AR：Compact 动态 summary budget 结果

## 判定

**PASS（离线预算与 fallback 门禁）**。`MemoryContextBuilder` 现在同时保留静态
summary cap 和基于当前 prompt 剩余空间的动态 cap；动态槽为零时不会调用 summary
factory，仍使用 deterministic source view。默认 `rolling_summary_enabled=False` 未变。

## 实施

- 接入已有 `calculate_summary_budget`，扣除 used prompt、required reserve、最近两条
  dialog suffix 和 64-token response-schema reserve，再与静态 cap 取最小值。
- tokenizer 不可用、预算元数据异常或动态槽为零时，不扩大 summary 权限；保持静态兼容
  或 deterministic fallback。
- 未改变 `ContextCompactionRecord`、`ContextCompactionBinding`、source fingerprint、
  required candidate 或原子选择语义。

## 验证

- compaction/rolling/context governance focused suite：**135 passed**。
- 完整实验目录离线 suite：**76 passed**。
- 动态 cap 测试观察到静态 cap `80` 在剩余空间下收缩到正值 `11`；预算耗尽时
  factory 调用次数为 `0`，持久化算法为 `deterministic_observation_mask_v1`。
- `git diff --check` 通过。

## 限制与下一步

本阶段只证明预算与 fallback 语义，不改变默认 Compact，也不证明生成式 summary 的
语义质量。已有 Phase33B 证明 DeepSeek summary adapter 可在固定 80-token cap 下
通过，但尚未把新的动态 cap 接入真实 runtime 的 provider factory。下一阶段应先写
计划，再实现 feature-flagged provider factory/shadow，继续保持 deterministic fallback。
