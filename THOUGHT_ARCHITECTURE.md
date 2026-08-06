# OpenPilot Thought Architecture

## 1. 目标

本项目的目标不是构建单一强智能体，而是构建一个**小模型协作系统**：

- 先识别用户意图
- 再判断是否需要治理与路由
- 复杂任务进入多模型/多专家协作链路
- 简单任务直接执行
- 全链路保持可审计、可回放、可演进

核心原则：

- metadata 只负责通信桥梁
- governance 负责权限、风险、预算、确认
- router 负责任务分发与专家选择
- autonomous_iteration 负责自主迭代、拆解、推进和回收
- latent communication 只做局部提速，不替代主链路

---

## 2. 当前项目现状

当前仓库里已经有：

- `Code/src/core/command_approval.py`
- `Code/src/tools/command_tool.py`

这说明项目已有**命令级审批/执行雏形**，但还没有完整的：

- 意图识别层
- 全局治理层
- 任务路由层
- 专家池层
- metadata 契约实现

此外，`Code/src/metadata/` 当前为空，说明：

- 设计意图已经在 `API.md` 中表达
- 但还没有转成可执行的代码契约

---

## 3. 正确的系统链路

推荐的链路是：

```text
输入
 -> 意图识别
 -> 治理判断
 -> 任务路由 / 规划
 -> 执行
 -> 验证
 -> 反馈 / 记忆
```

### 各层职责

#### 3.1 意图识别
判断输入属于：

- 闲聊
- 简单执行
- 复杂任务
- 需要规划/拆解的目标

输出应包含：

- `intent_type`
- `complexity`
- `risk_hint`
- `needs_router`
- `needs_confirmation`

#### 3.2 治理层
治理层不做任务执行，只决定是否允许进入下一步。

职责包括：

- 权限控制
- 风险控制
- 预算控制
- 确认控制
- 停止条件判断

输出应包含：

- `allow_execute`
- `require_confirmation`
- `risk_level`
- `budget_limit`
- `stop_condition`

#### 3.3 路由层
路由层决定复杂任务交给谁。

职责包括：

- 选择 agent / tool / expert
- 生成 fallback
- 记录路由理由
- 记录置信度

输出应包含：

- `route_target`
- `fallback_target`
- `confidence`
- `required_capabilities`

#### 3.4 执行层
执行层真正完成任务。

职责包括：

- 调用工具
- 修改文件
- 运行命令
- 收集结果

#### 3.5 反馈层
反馈层负责把执行结果沉淀回系统：

- 写回 metadata
- 写回 memory
- 记录成功/失败
- 记录成本与效果

---

## 4. metadata 的定位

metadata 只做**通信桥梁**，不做治理。

### metadata 负责

- 描述任务
- 描述状态
- 描述决策结果
- 描述执行结果
- 描述状态流转

### metadata 不负责

- 权限判断
- 风险判断
- 路由决策
- 执行逻辑

### 建议的基础字段

所有核心 metadata 结构都应保留：

- `kind`
- `schema_version`
- `source`
- `correlation`

再根据类型增加领域字段。

---

## 5. 治理层

治理层应独立于 metadata，建议放在 `core/`。

### 职责

- 判断是否允许自动执行
- 判断是否需要用户确认
- 判断是否超出风险阈值
- 判断是否超出预算
- 判断是否触发停止条件

### 与当前实现的关系

当前的 `CommandApprovalGate` 可以视为治理层的一部分雏形，但它只覆盖：

- 命令级风险判断
- 命令级确认判断

它还不等于完整治理层，因为还缺少：

- 意图级治理
- 路由级治理
- 链路级治理

---

## 6. autonomous_iteration 的定位

`autonomous_iteration` 不只是 goal 模式的执行器，而是**自主迭代控制层**。

建议承担以下职责：

- 任务拆解
- 路由推进
- 状态流转
- 失败重试
- 结果回收

因此：

- `goal` 只是输入形式之一
- 不是唯一入口

---

## 7. 任务路由

任务路由不应只存在于大任务场景，而应该由意图识别决定是否进入。

### 路由策略

- 简单任务：直接执行
- 中等复杂任务：先治理，再路由
- 高复杂任务：进入完整拆解与专家协作链路

### 路由闭环

1. 输入任务
2. 识别意图
3. 治理判断
4. 选择专家
5. 执行
6. 验证
7. 回写结果与经验

---

## 8. 专家池 / BTX 思路

可以先实现“虚拟专家池”，再逐步替换为真实微调模型。

### 第一阶段：虚拟专家

同一个基础模型，通过不同配置形成不同角色：

- 规划专家
- 实现专家
- 审核专家
- 恢复专家

区分方式：

- system prompt
- 工具权限
- 记忆范围
- 风险阈值

### 第二阶段：真实专家

当路由数据足够后，再替换为真实微调模型。

---

## 9. 隐态通信

隐态通信适合做**局部加速**，不适合做主干治理。

### 使用原则

- metadata 负责主链路
- latent 负责短链路协作
- 最终结果必须回落到 metadata

### 适用场景

- 同模型子任务协作
- 同家族模型之间的内部交换
- 局部上下文压缩

### 不适用场景

- 审计
- 回放
- 权限
- 风险治理
- 状态主存储

---

## 10. 建议的模块划分

### `metadata/`
只放契约，不放业务逻辑。

建议对象：

- `IntentMetadata`
- `GovernanceDecision`
- `TaskCard`
- `RouteDecision`
- `ExecutionEvent`
- `TaskStateTransition`

### `core/`
放治理、风险、权限、策略。

### `autonomous_iteration/`
放路由、拆解、迭代、状态机。

### `tools/`
放具体执行工具。

### `memory/`
放路由样本、成功率、失败样本、经验回流。

---

## 11. MVP 实现顺序

### 第一步：补意图识别
输出：

- `intent_type`
- `complexity`
- `risk_hint`
- `needs_router`
- `needs_confirmation`

### 第二步：补治理层
输出：

- `allow_execute`
- `require_confirmation`
- `risk_level`
- `budget_limit`
- `stop_condition`

### 第三步：补路由层
输出：

- `route_target`
- `fallback_target`
- `confidence`
- `required_capabilities`

### 第四步：补专家池

- 先虚拟专家
- 后真实模型

---

## 12. 一句话总结

OpenPilot 的正确方向是：

**意图识别 -> 治理 -> 路由 -> 执行 -> 反馈；metadata 只做通信桥梁，autonomous_iteration 负责自主迭代，expert pool 负责分工协作，latent 只做局部提速。**

