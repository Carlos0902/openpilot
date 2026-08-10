# Phase H8-R2AT：DeepSeek provider summary shadow 结果

## 判定

**PASS（安全与接入边界；无收益信号）**。`openpilot-air` 上 production
`MemoryContextBuilder` 触发了真实 DeepSeek summary factory，provider 返回完整 JSON，
adapter 接受了该摘要；但最终 builder sink 收到的是 deterministic record，摘要没有被
选入 Prompt，也没有写入 authority artifact。这证明当前 kill-switch/原子边界有效，
不证明生成摘要带来 token 或质量收益。

## 证据身份

- host：`openpilot-air` / `abaabadeMacBook-Air.local`
- selection：H8-R2AQ target-bound selection，hash
  `sha256:44337cccd493f06dc5df4d761e1817e1ec40a0bdbe3fd38d18b67613920bc514`
- 本地回收 receipts：
  `experiments/full_architecture_context_observation/runs/phase_h8r2at_provider_summary_shadow_20260809_v1/`
- R0 builder baseline receipt：`sha256:75d6ff510c9105a4d9422a43351fb4f8357fa63c5f872dbe19c0bc6db781d74d`
- S1 builder summary receipt：`sha256:33a62a2c79ad18d1062f4e69883c63257334b4f0d93fbbd6544912fc2484c119`

## 实验结果

| 项目 | R0 deterministic builder | S1 provider summary observation |
|---|---:|---:|
| builder prompt chars | 7,000 | 7,000 |
| provider calls | 0 | 1 |
| source fingerprint | `sha256:5eda3f…` | `sha256:5eda3f…` |
| source chars | 14,739 | 14,739 |
| deterministic compact chars | 237 | 237 |
| provider finish | — | `stop` |
| provider usage | — | prompt `2,522`, completion `114`, total `2,636` |
| generated summary | — | 504 chars / 114 tokens |
| adapter | — | accepted=true |
| final context compaction | none (sink returns no artifact) | deterministic record observed; generated summary not selected |

S1 request evidence：`json_object`、无 tools、temperature `0`、reasoning
`disabled`、`max_tokens=256`、`max_retries=1`、`transport_retries=0`。request/response
仅记录 hash、message chars、usage 和 finish reason，不保存 prompt、源正文或摘要正文。

## 安全门禁

- `assembly_status=ready`；required constraint 与最近 dialog 保持在最终 context；
  `used_in_prompt=false`、`authority_artifact_persisted=false`。
- project/memory/writer/command/verification/retry/fallback tool side effects 均为 0；
  provider transport 仅 S1 的 1 次 summary request。
- 本地实验目录 suite：**79 passed**；receipts hash、secret scan、body-field scan 和
  `git diff --check` 通过。
- key 仅通过 `openpilot-air` 单次 stdin 注入；结束后进程和 shadow receipt 均无
  secret-shaped 内容，未写 `.env`、shell profile、argv 或持久配置。

## 原因探查与下一步

这是一个有价值的负收益信号：摘要本身满足 schema/usage/finish/source/budget，但在
builder 的原子 fit/recent-suffix 选择边界前没有成为最终 record；当前 receipt 能确认
“accepted but not selected”，还不能从 builder 内部区分是 summary fit 失败、recent-suffix
位移还是其他 trial decision。下一阶段应先补 typed fallback/selection reason telemetry，
再调整 summary schema/动态 cap 或做多窗口实验；在此之前保持默认 flag 关闭，不进入真实
tool-task 或 mutation canary。OpenAI 仍需独立 OpenAI credential。
