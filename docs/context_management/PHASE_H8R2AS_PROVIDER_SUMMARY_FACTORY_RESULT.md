# Phase H8-R2AS：Provider summary factory 结果

## 判定

**PASS（默认关闭的 runtime 接入与离线契约门禁）**。provider-neutral rolling summary
factory 已接入 `IntelligentAutopilot` 的 `MemoryContextBuilder`，但只有显式 typed
flag 开启时才会注入；默认路径仍为 deterministic observation compaction，未发生
provider summary 调用。

## 实施

- 新增 `build_llm_rolling_summary_request_factory`，只构造 `memory_compression`、
  `json_object`、无 tools、temperature `0`、typed `disabled` reasoning 的请求。
- 请求只携带可压缩 assistant candidates 的 evidence IDs/content；不携带 required
  constraints、用户对话、权限、写目标或验证命令。
- factory 绑定 source candidate IDs/fingerprint，并保留原始 provider usage、
  `usage_observed` 与 `finish_reason`；parsed JSON、空响应和 malformed JSON 不被猜测为
  合法 summary。
- `LLMSettings` 新增 `OPENPILOT_ROLLING_SUMMARY_ENABLED=false` 与
  `OPENPILOT_ROLLING_SUMMARY_TOKEN_LIMIT=256`（上限 4096）。flag 只控制非-required
  derived view，不授予写文件、执行命令或修改权限。
- `MemoryContextBuilder` 继续负责 adapter 验证、原子选择、artifact persistence 和
  deterministic fallback；动态 summary cap 沿用 H8-R2AR。

## 验证

- factory/settings/context focused suite：**69 passed**。
- context、session constraint、reasoning、provider、native transport、rolling
  compaction extended suite：**332 passed**。
- 完整实验目录离线 suite：**76 passed**。
- `git diff --check`：通过。

## 限制与下一步

本阶段没有开启默认 summary，也没有执行真实 provider shadow；因此不宣称生成摘要的
语义质量或 token 收益。下一阶段应在 `openpilot-air` 使用一次性 DeepSeek 凭据执行
read-only summary shadow，比较 deterministic 与 provider summary 的接受率、fallback
原因、summary token/total token 和 required/recent/context lineage。DeepSeek key 不写入
仓库、`.env`、shell profile、argv 或 receipts；OpenAI lane 仍需 OpenAI 专属 key，不能
复用 DeepSeek key。
