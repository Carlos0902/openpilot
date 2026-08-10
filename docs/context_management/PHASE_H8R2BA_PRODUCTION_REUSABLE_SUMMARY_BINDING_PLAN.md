# Phase H8-R2BA：Production-facing reusable summary binding contract 计划

## 背景

H8-R2AZ 证明了 narrow summary artifact 在 source binding、required/recent/session drift
fail-closed 的前提下，复用两次可以摊销生成成本；但当前证据仍是 experiment-only，
`ContextCompactionReuseAdmission` 明确 shadow-only，不能直接进入 authority prompt。

## 本阶段目标

先审查并验证一个最小、默认关闭的 production-facing binding contract，回答：

1. 现有 `ContextCompactionBinding`、`ContextCompactionReuseAdmission` 和
   `MemoryContextBuilder` 是否已经能表达 artifact identity、source lineage、required/recent
   protection、session constraint drift、integrity 和 invalidation；
2. 如果缺少字段，选择复用/扩展现有 typed contract，而不是创建第二份事实或通用关系层；
3. 在不让 summary 进入 authority prompt、不持久化正文、不调用 provider 的情况下，验证
   builder-level default-off binding shadow 能记录 admission/fallback reason；
4. 为后续真实可复用 artifact canary 定义明确的 acceptance 与 rollback gate。
5. 在 shadow-only 范围内先关闭已确认的 admission fail-open：当前 body-free
   source fingerprint、生产可接受的 artifact kind、source-binding/fixture-ledger
   drift 必须有可复现的 typed rejection；不能用实验 fixture 的 admitted 结果替代
   production-admissible candidate。

## 非目标

- 不开启默认 summary 或 prompt-use transition；
- 不改变 `ContextCompactionReuseAdmission.used_in_prompt=False` 的 shadow-only 约束；
- 不新增 provider、Compact、Reasoning、budget、权限或 mutation 行为；
- 不持久化 summary 正文、不运行真实 provider、不修改 project/memory；
- 不把 H8-R2AZ 的单 fixture token 摊销结果扩展为生产收益。

## 实施步骤

1. metadata inventory：检查 CONTRACT_CATALOG、具体 models、exports、producer/consumer，
   记录复用/扩展/新 contract 决策和 impact note；
2. contract review：以 source fingerprint、ordered source IDs/binding hash、required/recent
   IDs、session constraint hash、artifact kind/integrity、ledger identity 为 fail-closed 门禁；
   其中 source fingerprint 与 fixture-turn ledger 必须进入真实 admission payload，且
   `context_compaction` kind 必须与 `ContextCompactionBinding` 保持一致。
3. 离线 focused tests：覆盖 same-binding admission、每类 drift、builder default-off、
   body-free fallback/selection telemetry；
4. 若现有 contract 足够，只新增 review harness/receipt，不新增 metadata 字段；若确有
   缺口，先停在 typed contract proposal，不直接修改 production authority path；本阶段
   只允许修复 shadow admission 的 lineage 检查，不授权 prompt-use transition。
5. 记录 receipt、限制和下一阶段（仅在 contract review PASS 后才设计 default-off
   production builder shadow）。

## 通过标准

- inventory/impact note 明确无重复 authority fact；
- 所有 source/required/recent/session/ledger/artifact drift 都 typed fail-closed；
- default-off builder 不产生 provider、writer、command、verification 或 memory mutation；
- receipt body/secret-free，保留 selection/fallback reason 和 source lineage hash；
- receipt 必须断言 exact rejection vector、receipt 自洽 hash 和 H8-R2AZ nested receipt hash；
- 不把 shadow admission 或 trial selection 称为 prompt-use 或 accepted production cache。
