# Stage 7C 计划：provider-attempt 输出与 reasoning 遥测闭环

## 边界

本阶段先把现有 `llm_requested`/`llm_responded`/`llm_failed` 事件投影成严格的
离线 attempt receipt；不增加 Provider 流量，不把诊断投影升级为执行权限，也不改变
reasoning 路由策略。

## 分阶段计划

1. 复用现有 provider-bound request hash、call/ordinal、usage、finish reason 和
   response artifact；补齐 producer 在 request/response/failure 事件中的 attempt
   identity 与 hash 链接。
2. 定义实验侧 `ProviderAttemptReceipt`：完整 usage 才可结算，partial/unknown 保留
   raw usage 且 token 字段保持 `null`；reasoning usage 可空；finish reason 保留原值；
   failure、repair、transport retry、fallback、replay 都有显式关系。
3. 用事件对离线 receipt 做 deterministic replay，覆盖成功、length 截断、空/未知
   usage、transport failure、一次受限恢复、pre-transport block 和 replay。
4. 在真实 canary 前把 receipt 与 campaign ledger、ContextLoader request hash、
   `session_turn_source_hash` 和下游 verification evidence 对齐；没有完整 usage、
   finish reason 或 action evidence 时 stop/fallback，不能宣称成功。

## 不变量

- `usage_observed=false` 绝不意味着 Token 为 0；unknown usage 不进入 ledger reconcile。
- `total=input+output`，reasoning tokens 已知时不得超过 output；length 只允许一次
  窄 schema recovery，不能重做完整文件生成。
- replay 保留 request hash 与原始 attempt 链接，`transport_attempted=false`；
  fallback 与原始 compact/current receipt 使用不同 execution ID。
- provider/model/endpoint/reasoning profile 变化必须改变 request hash，旧 receipt 不可
  静默复用。

通过离线 receipt 回放后，才制定下一阶段的 bounded real Provider canary。
