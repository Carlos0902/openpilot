# Phase H8-R2BA：Production-facing reusable summary binding review 结果

## 判定

**PASS FOR DEFAULT-OFF SHADOW REVIEW；不授权 prompt-use、default-on 或 provider canary。**

本阶段关闭了一个真实 admission fail-open，并把 review harness 从手工 metadata 构造改为真实
`MemoryContextBuilder` wiring。当前结果仍是 shadow evidence，不是 accepted commit。

## 观察到的问题与修复

### 1. source fingerprint 未参与 production admission

`admit_reusable_compaction_candidate` 原先只比较 source IDs 和 binding hash；篡改 candidate 的
`source_fingerprint` 仍可能得到 `admitted`。

修复：

- builder 在 body boundary 前计算受限的 assistant-dialog source fingerprint index；
- payload 只传 `source_fingerprint_by_candidate_ids`（ID key + hash，不传正文）；
- admission 对 exact source-ID key 做 fail-closed 比较；缺少当前 fingerprint 也拒绝；
- 当前 session 有约束但 candidate 缺少对应 `session_constraints_hash` 时拒绝。

### 2. 实验 artifact kind 与 production kind 不一致

旧 BA shadow fixture 使用实验 kind `context_compaction_summary_artifact_v1`，而 production
candidate admission 只接受 `context_compaction`。fixture 现在通过显式 production adapter
归一化，避免把实验 kind 误报为 production-admissible。

### 3. drift matrix 覆盖不足

H8-R2AZ/BA 原先未覆盖 fixture-turn ledger 与 source-binding hash drift。现已加入两类负向
case，并由 BA gate 断言 exact rejection vector 和 nested AZ receipt hash。

## 真实 builder wiring 证据

H8-R2BA harness 现在实际调用 `MemoryContextBuilder`：

- shadow admission 由 production `build_compaction_reuse_shadow_provider` 生成；
- `prompt_text`、selected candidate IDs、request hash、`context_compactions` 保持不变；
- admission 始终 `used_in_prompt=false`；
- payload 只含 hashes/IDs/digests，不含 prompt 或 dialog body；
- real provider、project/memory、writer、command、verification side effects 均为 0。

本次本地 receipt（临时目录，未纳入仓库）hash：

`sha256:741a1e59721500902a8cd20cd27fd4afe996585c77bbf689c6843ac5b7f2ebc2`

claim boundary：`production_reusable_summary_binding_contract_review_no_prompt_use`

## Gates

- production/context/metadata focused：**144 passed**，1 个既有 pytest deprecation warning；
- H8-R2AZ + H8-R2BA stage suite：**15 passed**；
- compileall：passed；
- `git diff --check`：passed；
- full `Code/tests` collection：仍被既有缺失的
  `experiments/full_architecture_context_observation/stage25_budget_profile_task_matrix.py`
  阻断，不能宣称 full pass。

## Independent acceptance

只读子代理独立复跑确认：

- receipt validator、self-excluding receipt hash、body/secret scan：passed；
- 7 个 H8-R2BA gates：全部 passed；
- AZ exact rejection vector 与 top-level zero side effects：passed；
- real builder wrong source fingerprint：`rejected/source_fingerprint_mismatch`；
- real builder 当前有 session constraint、candidate guard 缺失：
  `rejected/session_constraints_hash_mismatch`；
- independent focused suite：**64 passed**；
- 未修改文件、未调用真实 provider、未 staging/commit/push。

## 尚未解决的限制

- `ContextCompactionBinding` 尚未持久化 required/recent/session/ledger guard provenance；
  checkpoint provider 仍依赖外部注入；
- `ContextCompactionReuseAdmission` 的 artifact kind、generated summary fingerprint 和
  artifact integrity 仍需在未来 prompt-use gate 做更严格的 typed/integrity review；
- candidate/runtime view 尚未持久化并比较 session-turn ledger hash；本阶段只补了实验 drift
  evidence，不把它宣称为 production prompt-use protection；
- non-strict shadow provider 异常仍缺少独立 typed fallback telemetry；
- 未调用真实 provider，未验证 token/质量/调用收益；
- 未 stage、commit 或 push，dirty tree 不是验收对象。

## 下一阶段

进入独立的 H8-R2BB：补齐 builder shadow 的 typed fallback telemetry、malformed artifact
contract rejection 和真实 lineage receipt；通过后才讨论 reusable artifact canary，仍不直接
开启 prompt-use。
