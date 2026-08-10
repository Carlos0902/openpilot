# Phase H8-R2AS：Feature-flagged provider summary factory 计划

## 目标

把已通过 Phase33B 的 `RollingSummaryAdapter` 接入完整 autonomous runtime 的可选
provider summary 路径。factory 只负责构造 provider-neutral JSON 请求、记录 attempt
evidence 和返回不可信 payload；adapter 继续负责 schema/source/budget/finish 验证，
`MemoryContextBuilder` 继续负责 authority、原子选择和 artifact persistence。

## 设计边界

- 新增复用型 factory，不新建 summary authority 或 metadata kind。
- 请求 purpose 固定为 `memory_compression`，response format 固定 `json_object`，无 tools，
  temperature=0，reasoning policy 固定 typed `disabled`。
- prompt 只包含当前可压缩 assistant candidates 的 IDs/content；required constraint、
  user dialog、权限、写目标和验证命令不进入可替换 summary。
- provider usage、finish reason 和 parsed JSON 缺失时由 adapter fail-closed；factory
  不猜 usage=0，不执行 retry/fallback tool action。
- `OPENPILOT_ROLLING_SUMMARY_ENABLED` 默认 false；开启只改变非-required derived view，
  deterministic summary 仍是 kill switch。动态 summary cap由 H8-R2AR 提供。

## 实施顺序

1. 提取稳定 source fingerprint helper，避免 factory 与 deterministic record 漂移。
2. 实现 `build_llm_rolling_summary_request_factory`：构造严格 JSON schema prompt，调用
   `build_context_llm_request`，解析 `LLMResponse.parsed_json` 和完整 usage/finish evidence。
3. 在 `LLMSettings` 增加默认关闭、正整数 cap 的 typed flag；在 `IntelligentAutopilot`
   只按该 flag注入 factory+adapter，其他构造路径保持行为不变。
4. 补 offline/mock tests：flag off zero calls、valid summary、malformed/empty/length/
   unknown usage fallback、unsupported reasoning profile fallback、source fingerprint drift。
5. 先跑 focused/full offline suite；只有全通过才考虑一次 DeepSeek shadow，shadow 不改变
   default Compact，也不执行 mutation。

## 退出条件

factory 可被完整 runtime 使用但默认关闭；所有失败保持 deterministic source view；
receipt/request 不泄露 secret 或权限字段；focused context、reasoning、provider 和
experiment suite 全部通过。真实 shadow 仅在本计划离线门禁通过后单独记录。
