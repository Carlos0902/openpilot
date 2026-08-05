# Runtime Recovery Documents

本目录集中管理运行时断点恢复文档，避免 checkpoint、状态和兜底方案成为互相竞争的
计划。

## 文档职责

- `COMPREHENSIVE_RECOVERY_BOUNDARY_PLAN.md`：当前唯一的实施路线图。记录已完成基线、
  全部边界、优先级、切片依赖和总体验收标准。
- `RUNTIME_CHECKPOINT_RECOVERY_PLAN.md`：checkpoint、原子存储、文件副作用提交协议和
  早期 Phase 0–6 的设计与实施记录。
- `RECOVERY_STATUS_AND_FALLBACK_PLAN.md`：typed recovery status、recoverability、
  automation policy、blocker/fallback 和不可恢复兜底的详细设计。

根目录协议文件具有规范性：

- `AGENT_LOOP_SESSION_RESUME.md`：单次恢复的身份、预检、预算和副作用规则；
- `AGENT_LOOP_SUPERVISOR.md`：跨进程调度、重试和单写者规则；
- `AGENT_LOOP_PROTOCOL.md`：Agent loop 的总体协调协议；
- `AGENT_LOOP_GOAL.md`：验收标准和停止条件。

后续实施先更新总计划的切片状态，再同步受影响的详细设计和规范协议。历史计划中的
“建议阶段”不再单独决定实施顺序。
