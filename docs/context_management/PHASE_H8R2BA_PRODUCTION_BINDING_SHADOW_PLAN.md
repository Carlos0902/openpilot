# Phase H8-R2BA：Default-off production-facing reusable summary binding shadow 计划

## 背景

H8-R2AZ 证明了实验层 admission gate：同一 source binding 下复用 2 次可摊销 narrow summary
生成成本，并且 source/required/recent/session/artifact 漂移都会 fail closed。下一步不是直接启用
provider summary，而是把这些 admission 事实映射到生产可审计 surface 上，先 shadow-only。

## 目标

建立一个 default-off、production-facing、shadow-only 的 reusable summary binding 证据层：

1. 使用现有 `ContextCompactionRecord` / `ContextCompactionBinding` / `DurableArtifactReference`
   作为主要事实来源，避免创建第二套 summary authority。
2. 为 reusable summary admission 增加 body-free guard evidence：source binding hash、required candidate IDs、
   recent suffix IDs、session constraints hash、artifact integrity hash。
3. 在生产 builder 或其相邻边界中只做 shadow/admission 记录，不把 reused summary 放入 authority prompt。
4. 漂移时必须 typed rejected，且 fallback 到当前 deterministic/normal source view。

## 非目标

- 不开启默认复用。
- 不读取或持久化 summary 正文到 receipt。
- 不调用 provider。
- 不改变 mutation/tool-task 权限边界。
- 不声明 token/quality/default-on 收益。

## 元数据影响预检查

本阶段可能触碰 metadata surface，因此实施前必须完成：

1. 复核 `docs/metadata/CONTRACT_CATALOG.md`。
2. 复核 `docs/metadata/DEVELOPMENT_CONVENTIONS.md`。
3. 复核 `ContextCompactionRecord`、`ContextCompactionBinding`、`DurableArtifactReference` 的生产消费者：
   context builder、context projection、runtime checkpoint resume、相关测试。
4. 优先选择扩展现有 binding/attempt evidence；只有现有合同无法表达 admission guard 时才新增合同。

## 实施候选

优先方案：

- 新增一个 typed、body-free admission evidence value，挂在现有 context compaction selection/attempt 或 binding
  旁边，而不是创建新的 authority artifact。
- 该 evidence 只记录：
  - source_candidate_ids；
  - source_fingerprint；
  - source_binding_hash；
  - required_candidate_ids；
  - recent_suffix_ids；
  - session_constraints_hash；
  - artifact_id/kind/checksum；
  - admission status/reason；
  - `used_in_prompt=false`。

备选方案：

- 如果现有 metadata 不适合扩展，则先在 experiment harness 做 production-shaped shadow receipt，
  保持生产代码不变，等独立 review 后再进 metadata change。

## 阶段门禁

- Metadata impact note 明确“复用/扩展/新增”的选择。
- Focused metadata/context tests 覆盖：
  - default off；
  - admitted shadow 不进入 prompt；
  - stale source rejected；
  - required/recent/session drift rejected；
  - artifact kind/checksum rejected；
  - body-free serialization。
- `git diff --check`、compileall、context/rolling suite 通过。

## 通过标准

- H8-R2BA 只允许产生 shadow/admission evidence。
- 任何 reusable summary admission 结果都不能改变最终 prompt。
- 所有 rejected path 都保留 deterministic/current source view。
- 文档、证据索引和完成审计同步更新。
