# Phase H8-R2BB：Shadow failure telemetry 与 artifact contract 结果

## 判定

**PASS FOR DEFAULT-OFF SHADOW TELEMETRY；不授权 prompt-use、default-on 或 provider canary。**

## 实施内容

- 在 `ContextSelectionMetadata` 下新增 owned nested
  `compaction_reuse_shadow_failures`，只记录 typed reason、bounded exception type、
  `strict_sources=false` 和 `fallback_applied=true`；不记录异常 message、prompt 或正文。
- `MemoryContextBuilder` 对 shadow provider 的 exception、`None`/空返回、malformed result
  分别记录 `provider_exception`、`provider_empty`、`invalid_provider_result`；non-strict
  保留原 assembly，strict 仍抛 `ContextSourceError("context_compaction_reuse", ...)`。
- checkpoint reusable artifact 的 malformed checksum 现在生成
  `artifact_contract_invalid` typed rejection，并以 zero checksum 替代非法值，避免 admission
  被静默吞掉。
- admitted reuse admission 现在必须使用 `context_compaction` artifact kind 且携带
  `generated_summary_fingerprint`；runtime candidate 的 algorithm 也必须属于现有 typed
  compaction algorithms。
- 同步 `API.md`、`CONTRACT_CATALOG.md` 和 metadata exports；未新增 `MetadataKind`。

## 证据

本地 H8-R2BB receipt（临时目录，未纳入仓库）hash：

`sha256:94234fd7ec6a4fc3bfb6384c66457d35c5be27269de00f6c102fd141e0e33c56`

receipt status：`passed`

覆盖：

- 四类 real-builder failure paths；
- strict/non-strict 行为；
- prompt、selected candidates、request hash 不变；
- malformed checkpoint artifact；
- admitted identity contract；
- receipt self-excluding hash 与 body/secret scan。

## Gates

- production/context/metadata focused：**151 passed**，1 个既有 pytest deprecation warning；
- H8-R2AZ/H8-R2BA/H8-R2BB stage suite：**17 passed**；
- 排除历史缺失测试文件后的 `Code/tests`：**1330 passed**，1 个既有 warning；
- compileall：passed；
- `git diff --check`：passed；
- 未排除的 full `Code/tests` collection：仍被历史缺失的
  `stage25_budget_profile_task_matrix.py` 阻断，且相关 stage28/30/31 文件也缺失；不能
  宣称 full pass。

## Independent acceptance

只读子代理独立验收通过：

- H8-R2BB review harness：5/5 gates passed；
- 四类 failure telemetry、strict/non-strict invariants、malformed artifact zero hash、
  admitted identity validator：passed；
- receipt validator、self-excluding hash、body/secret scan：passed；
- independent focused：**122 passed**；
- full-excluded：**1330 passed**，1 个既有 warning；
- 未修改文件、未调用真实 provider、未写仓库 runs/data、未 staging/commit/push。

## 独立验收边界

本阶段只允许 shadow diagnostics 和 fail-closed rejection：

- real provider calls：0；
- project/memory/writer/command/verification side effects：0；
- prompt-use：禁止；
- default-on：关闭；
- staging/commit/push：未执行；
- dirty tree：不是验收对象。

## 尚未解决的限制

- required/recent/session/ledger guard provenance 仍由 checkpoint caller 外部注入；
- artifact body loader 和实际 checksum re-read 尚未实现；
- non-strict fallback telemetry 只覆盖 shadow provider 边界，不代表真实 provider 质量或
  token 收益；
- 未完成真实任务、多 Provider 或 Compact 端到端收益验证。

## 下一阶段

下一阶段先写 H8-R2BC 计划，审查 guard provenance 与 artifact integrity loader 的 prompt-use
前置门禁；在该门禁通过前不进入 reusable artifact canary。
