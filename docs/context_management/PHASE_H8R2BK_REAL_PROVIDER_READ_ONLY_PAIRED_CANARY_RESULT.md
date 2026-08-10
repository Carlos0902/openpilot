# Phase H8-R2BK：Real-provider read-only paired canary 结果

## 判定

**PASS FOR REAL-PROVIDER READ-ONLY PAIRED CANARY；仍不授权 production prompt-use、mutation/tool-task、cross-provider rollout 或 default-on。**

本阶段在 H8-R2BJ 的 discovered persisted binding 链路之后，首次用真实 DeepSeek
provider 做 raw/reusable paired read-only 对比。Provider 请求只回答一个固定 JSON
质量问题，不调用工具、不写文件、不运行命令；receipt 只保存 hash、usage、finish
reason 和质量布尔量，不保存 prompt/source/summary/response 正文或 key。

## 实施内容

- 新增计划：
  `docs/context_management/PHASE_H8R2BK_REAL_PROVIDER_READ_ONLY_PAIRED_CANARY_PLAN.md`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bk_real_provider_read_only_paired_canary.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bk_real_provider_read_only_paired_canary.py`
- 新增正式 receipt：
  `experiments/full_architecture_context_observation/runs/phase_h8r2bk_real_provider_read_only_paired_canary_20260810_v1/aggregate/receipt.json`

## Metadata impact

本阶段没有新增或修改 public metadata contract。它复用：

- `ContextCompactionBinding.source_binding_hash`；
- checkpoint discovery shadow admission；
- reusable prompt-use preflight；
- reusable prompt-use simulation；
- `ContextAssembler` atomic compaction governance；
- `LLMRequest` / `LLMResponse` provider telemetry。

`ContextCompactionReuseAdmission.used_in_prompt` 仍为 `false`。Reusable projection 只在
BK harness 的显式 opt-in paired canary 中临时构造，没有改变
`MemoryContextBuilder` 默认 prompt 输出。

## 结果

Official receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bk_real_provider_read_only_paired_canary_20260810_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:023074faf47e1bf0ab54d0b2d5a78b1da507536c1ae63a487e3ac0085927dba8`

Provider usage：

| Metric | Raw | Reusable | Delta |
|---|---:|---:|---:|
| prompt tokens | 4,424 | 1,176 | 3,248 |
| total tokens | 4,474 | 1,233 | 3,241 |

Both arms:

- `finish_reason=stop`
- provider usage complete
- deterministic required facts covered
- zero tool/writer/command/project/memory mutation side effects

Invariants：

| Invariant | Result |
|---|---|
| raw candidates available | true |
| compact builder produced one binding | true |
| discovery admitted binding | true |
| discovery is shadow only | true |
| preflight passed | true |
| simulation passed | true |
| projection hashes match simulation | true |
| provider usage complete | true |
| provider finish reason present | true |
| provider prompt tokens reduced | true |
| quality facts covered | true |

## Gates

- BK focused：
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bk_real_provider_read_only_paired_canary.py`
  → **2 passed**
- Compaction reuse + BF/BG/BH/BI/BJ/BK focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bf_prompt_use_preflight.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py experiments/full_architecture_context_observation/test_stage_h8r2bj_discovered_binding_opt_in.py experiments/full_architecture_context_observation/test_stage_h8r2bk_real_provider_read_only_paired_canary.py`
  → **40 passed**
- compileall for BK harness/test：passed
- `git diff --check` for BK files：passed
- BK receipt/code/doc body/secret scan：passed

## 安全边界

- no production prompt mutation；
- no default-on；
- no tool calls；
- no project/memory mutation；
- no writer or command actions；
- no artifact body read；
- no prompt/source/summary/response body persisted；
- provider key is process-only and not serialized；
- `ContextCompactionReuseAdmission.used_in_prompt=false`；
- result is DeepSeek read-only scoped, not cross-provider。

## 限制

- 只覆盖一个 deterministic read-only quality prompt，不覆盖 mutation/tool-task。
- 质量判定是结构化事实覆盖，不是通用语义等价评审。
- 这是 DeepSeek lane 的真实 usage，不是 OpenAI 或其他 provider 结果。
- Reusable projection 仍是 harness-level explicit opt-in，不是生产 builder default。

## 下一步

进入更贴近真实任务的阶段前，建议先做 read-only quality/benefit confirmation：

1. 复用 BK 链路，扩大到 3 个 read-only fact-contract prompts。
2. 验证不同上下文长度下 provider usage delta 和事实覆盖是否稳定。
3. 只有 read-only confirmation 稳定后，再进入 mutation/tool-task shadow gate。
