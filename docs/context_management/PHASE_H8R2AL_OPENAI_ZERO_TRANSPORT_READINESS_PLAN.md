# Phase H8-R2AL：OpenAI zero-transport readiness recheck 计划

## 目标

在进入任何 OpenAI paired canary 前，重新验证 provider lane 身份、显式
`openai-chat-known` reasoning profile、`tiktoken:o200k_base` tokenizer 和
credential 状态；缺少 credential 时必须 typed-blocked，且不得发起网络请求。

## 范围

- 使用 `OPENAI_GPT4O_MINI_LANE` 的 typed settings。
- 只读取进程环境中的 `OPENPILOT_OPENAI_API_KEY` 或 `OPENAI_API_KEY`。
- 使用项目 `.venv` 的 provider tokenizer 检查。
- 生成 body-free readiness 结果，记录 blocker、lane identity、tokenizer identity
  和 zero-side-effect counters。

## 非目标

- 不调用 OpenAI provider。
- 不运行工具、命令或 mutation task。
- 不把 DeepSeek credential 代用到 OpenAI lane。
- 不声明跨 provider 收益或 production prompt-use。

## 通过标准

- lane/settings/profile identity 一致；
- tokenizer 可用且 identity 精确匹配；
- 有 credential 时才允许后续 paired canary；
- 无 credential 时 status 必须为 `typed_blocked`，blocker 为
  `missing_credentials`，provider/network counters 全为 0；
- receipt 不含 secret 或正文。

## 后续决策

只有 `passed` readiness 才能进入 OpenAI raw/reusable paired canary；否则保留
DeepSeek-only 结论，并等待明确的 OpenAI credential 注入。
