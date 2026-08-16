# Phase H8-R2AT：DeepSeek provider summary shadow 计划

## 目标

在确认属于 `openpilot-air` 的 DeepSeek lane 上，对刚接入的 production
`build_llm_rolling_summary_request_factory` + `RollingSummaryAdapter` 做一次严格只读
shadow：比较 deterministic source view 与显式 provider summary 的接受率、fallback 原因
和 token 开销，确认生成摘要不会越过 required/recent/context lineage。现有 H8-R2AQ
tool-task runner 不经过 `MemoryContextBuilder` 的 rolling factory，因此本阶段不复跑
R1/K1，也不把 shadow 结果注入任务 Prompt、MemoryStore 或 artifact authority；这只验证
provider summary boundary，不外推为完整 tool-task 或跨 provider 收益。

## 实验臂与固定条件

- R0：仅构造 deterministic source fingerprint/size baseline，不发 summary Provider request。
- S1：同一 source snapshot 调用 production factory + adapter，允许最多一个
  `memory_compression` summary request；结果 `used_in_prompt=false`，失败保持 deterministic
  source view。
- provider：DeepSeek v4 flash，`deepseek-chat-known:v1`，reasoning disabled，zero
  transport retries，`real_read_only` scope；summary request 无 tools、temperature 0、
  `json_object`。
- source：复用 H8-R2AQ selection 的 50-turn dialog candidate contract；required constraint
  与最近两条 dialog 不进入可替换 summary；项目 target 只读，不执行 writer/command/
  verification。
- 认证：key 只通过一次性 stdin 注入 `openpilot-air` 的单个进程环境；不写 `.env`、shell
  profile、argv、日志、prompt、receipt 或文档，只允许不可逆 credential fingerprint。

## 必须记录的无正文证据

- source/selection、adapter、provider/model/profile、request/response hash；
- R0/S1 provider call count、prompt/completion/total usage、finish reason、usage completeness；
- S1 summary accepted 或 typed fallback reason；summary source IDs/fingerprint、dynamic cap、
  summary token count、`used_in_prompt=false`（不持久化为 authority artifact）；
- required candidate IDs、recent suffix IDs、lineage、assembly status、omitted required IDs；
- zero project/memory mutation outside disposable run root, zero writer/command/verification/
  retry/fallback tool actions；secret scan。

## 停止条件

任何 provider identity/selection drift、unknown usage/finish、malformed or over-budget summary、
source fingerprint mismatch、required/recent candidate omission、secret serialization、或
非只读副作用均停止，不重试、不扩大预算、不执行工具 task。R0 仅用于 paired baseline；S1
失败仍保留 deterministic source receipt，不能宣称 provider summary 成功。

## 实施顺序

1. 在 source 副本补齐 shadow harness、offline tests 和 secret-free receipt verifier；先跑
   本地 focused + experiments suite。
2. 将最小源文件同步到 `openpilot-air` 全新 disposable run root，运行 zero-transport
   readiness，确认 target/venv/tokenizer/DeepSeek identity。
3. 注入 key 运行 R0，再运行 S1；S1 只能使用同一 source snapshot，`max_retries=1`，禁止
   JSON repair 重复 Provider call 和任何 mutation。
4. 拉回 receipt，独立验证 hashes、lineage、usage、fallback 与 side effects；通过后更新
   result/index/implementation log。默认 flag 和已有 raw/Compact runner 不改变。

## 结论边界

通过只证明当前 DeepSeek lane 的 provider-summary context projection 可安全 shadow；不
证明完整 autonomous tool-task 质量、不改变默认 Compact、不证明调用次数下降，也不替代
OpenAI 专属 credential 实验。
