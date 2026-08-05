# Active Iteration 文档入口

## 当前入口

按阅读目的选择文档，不需要顺序阅读整个目录。

| 目的 | 文档 | 定位 |
|---|---|---|
| 对外或管理层汇报 | [ACTIVE_ITERATION_PROGRESS_REPORT_CN.md](./ACTIVE_ITERATION_PROGRESS_REPORT_CN.md) | 中文进度、结论、边界和下一步 |
| 技术评审 | [ACTIVE_ITERATION_EXPERIMENT_REVIEW.md](./ACTIVE_ITERATION_EXPERIMENT_REVIEW.md) | 当前完整证据综述和复现入口 |
| 实验审计 | [ACTIVE_ITERATION_EXPERIMENT_LOG.md](./ACTIVE_ITERATION_EXPERIMENT_LOG.md) | 追加式协议、失败、结果和证据等级账本 |
| 架构设计 | [ACTIVE_ITERATION_EXPERT_ROUTING_ARCHITECTURE.md](./ACTIVE_ITERATION_EXPERT_ROUTING_ARCHITECTURE.md) | 当前主动迭代、信号和专家路由设计 |
| Project Improvement 模块研究 | [PROJECT_IMPROVEMENT_ARCHITECTURE_RESEARCH.md](./PROJECT_IMPROVEMENT_ARCHITECTURE_RESEARCH.md) | 模块边界、运行语义、历史问题、根因与重设计约束 |
| 下一阶段实验协议 | [MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md](./MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md) | mini-SWE 原生轨迹上的分阶段净增益、消融与迁移计划 |
| 最短收益决策路线 | [MINI_SWE_CORE_BENEFIT_SCREEN_PROTOCOL.md](./MINI_SWE_CORE_BENEFIT_SCREEN_PROTOCOL.md) | 12 个配对任务的强信号筛查：先判断核心 E2--E3 闭环是否值得继续投入 |
| 核心收益筛查就绪性 | [MINI_SWE_CORE_BENEFIT_SCREEN_READINESS_AUDIT_V1.md](./MINI_SWE_CORE_BENEFIT_SCREEN_READINESS_AUDIT_V1.md) | 当前 receipt、独立审核与 Air 串行资源门的审计状态 |

## 冻结依赖

[ACTIVE_ITERATION_SIGNAL_DECISION_LOG.md](./ACTIVE_ITERATION_SIGNAL_DECISION_LOG.md)
是历史决策账本，并被已冻结实验 artifact 按原路径引用。它必须保留，但不是当前汇报或架构入口。
新结论不再同时写入该文件和实验日志。

## 归档

`archive/` 保存已被当前入口吸收的旧矩阵和比较稿。归档文件用于追溯，不代表当前项目状态。

## 更新规则

- 当前进度和对外表述更新中文汇报；
- 新实验事实先写冻结 artifact、实验日志和实验综述；
- 架构文档只保留当前设计，不记录日期式实施流水；
- 历史失败和无效实验不得删除或重新解释；
- 同一结论只保留一个权威入口，其他文档用链接引用。
