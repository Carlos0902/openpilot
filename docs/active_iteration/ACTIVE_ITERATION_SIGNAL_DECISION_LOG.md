# 主动迭代信号体系决策日志

## 文档定位

这份文档记录**主动迭代信号体系**(残差、眼图、采样密度、评判信号)的设计决策过程。

它不是架构文档。架构文档
(`./ACTIVE_ITERATION_EXPERT_ROUTING_ARCHITECTURE.md`)
描述已经确定的架构方向；这份文档记录尚在讨论中的信号体系,以及它与架构文档之间
已经出现的冲突。

只记录三类内容:

1. **已定决策** —— 附理由。理由比结论重要,因为后续判断边界情况要靠理由。
2. **待标定量** —— 附标定方法。不在这里发明具体数值。
3. **开放问题** —— 附关闭判据。说清楚"什么证据出现时这个问题算解决"。

**本文档不授权任何实现改动。** 决策进入实现需要单独的实现计划与验证证据。

---

## 更新规则

- 按日期分组追加,不重写历史条目。
- 一条决策被推翻时,保留原条目并追加"已推翻"标记与推翻理由,不删除。
- 待标定量在实验给出数值后,连同实验标识一起补记,不允许只写数值。
- 本文档中的任何数值若没有标注来源实验,一律视为占位符。

---

## 2026-07-28

### 背景

本轮讨论的输入是两份外部文档(与外部模型讨论产出,位于仓库外):

- 信号中继器框架(理论);
- 针对架构文档的九条改动建议。

讨论方式是逐条判定每个跨学科类比是否引入了**有效约束**。判定标准:一个类比必须至少
引入一项约束、一项可测量的程序、或一项定量关系,否则只是换名。这条标准本身是本轮
确立的方法论,后续新增类比一律按它判定。

### 项目目标框架(本轮明确)

目标不是"让模型更聪明",而是**用多轮主动迭代逼近更大模型的能力**。

主流 agent 的分工是:决策交给模型,一部分固定流程交给 harness。本项目要做的是把
**更多的判断责任移到 harness 侧**,并要求这部分收益具有**一定的迁移性**——即架构增益
不能只在某一个模型尺度上成立。

这条框架对信号体系有两个直接后果,后面的决策都受它约束:

- **信号体系是 harness 侧能力,不是模型能力。** 残差、眼图、采样、终止判据都必须
  由确定性代码计算,不能靠模型自评得出。模型自评只能进入建议层。
- **凡是按模型参数量取值的设计,与迁移性目标直接矛盾。** 架构文档 §3.3 已经把
  "架构增益能否迁移到不同模型尺度?"列为正式评测问题,假设 H4 也指向同一件事。
  一个按参数量分档的采样密度表,等于把待验证的迁移性预先写成了配置。

## 已定决策

### D1 残差只统计规定性字段

metadata 分两类:

- **描述性(事实中继)**:"函数 foo 有 3 个参数"。没有"满足/不满足"这个状态。
- **规定性(约束中继)**:"所有合约测试必须通过"。是二值的。

残差只统计规定性字段。

**理由。** 若不加这条限制,残差随 metadata 总量增长,度量的是"项目有多大",而不是
"距离目标有多远"。加了限制之后,残差才是距离量。

**现有承载。** `Code/src/metadata/project.py` 的 `SuccessMetricMetadata` 已经有
`required: bool` 与 `satisfied: bool | None`,这就是规定性字段的雏形,不需要新概念,
需要的是把"哪些类是规定性的"显式登记下来。

### D2 残差是三元组 `(unmet, unknown, uncontrollable)`

`satisfied: bool | None` 不是二值的,是三态。若残差定义为
`|{status == "unmet"}|`,则 `None`(尚未测量)被静默计为"不在残差里",即等价于已满足。
这是**误停止的主要来源**。

进一步,有一类残差项 agent 用任何动作都无法改变:缺失的外部依赖、需要人做的产品决策、
真实环境限制。它们永远是 unmet。若判据是 `critical > 0 → 必须继续`,系统将永不终止,
并且测量优先级会被永久钉在这些项上。

因此残差先按**能控性**划分,再按严重级分类:

- `unmet` —— 已测量、不满足、且可控。
- `unknown` —— 尚未测量。不给预算,必须归零。
- `uncontrollable` —— 已测量、不满足、不可控。只进报告,退出继续/停止判据。

**终止条件:`unmet == 0 且 unknown == 0`。**
`unmet == 0 且 unknown > 0` 不是收敛,是"看起来收敛",必须触发测量。

**理由。** unknown 与 uncontrollable 是两个方向相反的非二值状态:unknown 是
"还没测,必须去测";uncontrollable 是"测了也是不满足,但不能作为继续的理由"。压成
同一个计数就会各自造成一种失效:误停止,或永不停止。

**与架构文档的一致性。** 架构文档 §3.2 已经写明"未知:缺少证据,不能默认为通过或
失败"。原则已经在文档里,这条决策只是把它变成可计算的量。

### D3 分类残差不压扁

残差按 `{critical, major, minor}` 分级,严重级在字段设计时由人指定,不由模型运行时判定。

判据是**字典序**的,不是加权和:

- `critical > 0` → 必须继续。
- `critical == 0 且 major > 0` → 继续,建议层可以给顺序建议。
- 只剩 minor → 预算决定,可以停。

**理由。** 加权求和是可补偿的:大量 minor 可以在数值上盖过一个 critical,反过来
一个 critical 也会被算成"总体还行"。继续/停止是不可补偿判断,必须用字典序。

**密度自适应只读当前最高非空级别的趋势**,不读总量趋势。分母为零时不定义趋势——
零残差是最常见的终局状态,`residual_t / residual_{t-1}` 在那里除零。零残差用
"连续 k 轮为零"作为终止判据,不用比值。

### D4 双层结构:确定层绑定,评判层只建议

- **第一层(确定层,绑定)**:未加权的分类残差 + 眼图。驱动继续/停止/转向。不可补偿。
- **第二层(评判层,建议)**:加权严重度、模型评判。只能给优先级顺序,永远不上决策路径。

**第一层只读 `satisfied` 与 `required`,不读 `score` 与 `confidence`。**

**理由与代码证据。** `project.py` 的 `ProjectDimensionAssessmentMetadata` 在同一个
对象上同时带离散 `status: Literal[...]` 和连续 `score: float = 0.5`;
`confidence: float = 0.5` 也出现在多处。数字信号与模拟信号在类型层面没有隔离,而
默认值 `0.5` 使"没人填"与"评估为一半"在数值上不可区分。第一层若读这些字段,一个
未填字段会被当成中等确信的结论。

结构性事实可以无损再生(读一次、再读一次,值相同),像数字信号;语义判断会累积噪声,
像模拟信号。所以进入第一层的字段必须是二值的。给残差加权,等于把它从数字降级成模拟。

模型降低残差的唯一途径,是真的把约束修成满足。

### D5 事件触发采样,密度退出第一层

采样的对象是**规定性 metadata 的状态向量**;采样动作是**提取**(注入不是采样);
采样率的单位是"每 N 次改变状态的动作",不是墙钟时间,也不是 token 数。

**决策:每次改变状态的动作之后提取一次(事件触发)。**

**理由(奈奎斯特迁移的有效部分)。** 欠采样会产生别名:一个 `required=True` 的指标在
一个采样间隔内经历 unmet → met → unmet,两个端点都读到 met。这与 `satisfied is None`
不同——后者是"没测量",这里是"测量了且自洽",因此更危险:得到的是一个自信的错值,
而不是一个缺失值。

由此**眼图与采样密度定义上绑定**:欠采样下的眼图不是噪声大的眼图,是**假张开**的眼图。
一旦密度降到低档,眼图就不能作为证据使用。

**类比在哪里失效。** 奈奎斯特定理预设带限信号与均匀采样,两者在这里都不成立,所以
"两倍"这个具体倍数**不能当约束用**。能迁移的是:存在一个由被观测信号本身(不是模型
规格)决定的失效下界,且失效模式是自信误读。

事件触发使别名在构造上不可能发生,代价是提取次数变多。**密度因此只保留第二层的成本
含义,不再是第一层的参数。** 这一条与外部框架"把密度做成自适应的智能参数"的期望
明确相反,理由见 D6。

### D6 自适应下限只允许单向放松,且必须带标定窗口

若一定要引入自适应采样下限,约束如下:

- 初始值取最密(每次状态改变都提取);
- 只允许**单向放松**,不允许自适应收紧后再放松;
- 每次放松必须保留一段**高频标定窗口**;
- 标定窗口中发现任何漏掉的状态跳变,立即回滚到上一档密度。

**理由。** 一个从"它自己所治理的数据"中估计出来的下限是自指的:欠采样时会系统性
少数到状态变化 → 估出更低的变化率 → 允许更低的采样频率 → 估得更低。这个下降螺旋的
每一步看上去都在收敛,而**错误状态是稳定的**,不会自己暴露。

另外,状态变化率是**任务/项目耦合度的属性,不是模型的属性**。用多个体量的模型做实验
去求这个下限,会得到一族被任务性质打散的数字,组内离散大于组间差异,容易错误地
得出"没规律"的结论。要测这个量,应该按任务类型分层,不按模型尺度分层。

### D7 眼图是跨轮信号,不是残差的另一种写法

残差是 O(n) 的距离量;眼图是 O(n×k) 的,跟踪每个规定性字段最近 k 次取值。

它唯一能检出而残差检不出的失效:残差恒为 5,而失败集合在轮次之间轮换
`{A,B,C,D,E} → {B,C,D,F,G} → {C,D,F,G,H}`。这是振荡,不是进展。

简化度量:`signal_stability_t = 1 - (changed_fields_t / total_prescriptive_fields)`。

**二维诊断空间(残差 × 眼图)**:

| 残差趋势 | 眼图 | 判断 | 动作 |
| --- | --- | --- | --- |
| 下降 | 张开 | 正常收敛 | 继续,可降低中继密度 |
| 下降 | 闭合 | 有进展但不稳 | 谨慎继续,缩小改动面 |
| 走平 | 张开 | 卡住 / 假稳定 | 转换策略 |
| 走平 | 闭合 | 抖动 | 停止 |
| 上升 | 张开 | 系统性回归 | 回滚 |
| 上升 | 闭合 | 灾难性 | 立即停止 |

**残差决定继续/停止;眼图决定"继续的方式"。** 两者不融合成一个分数。

### D8 建议层永不修改状态(Allocator 挂载点)

metadata 注册表对外暴露一个稳定的 **Allocator 挂载点**:外部层可以读取全部 metadata,
可以产出优先级建议,**不可以修改任何 status**。

**理由(生物侧约束)。** 代谢通量不决定形态,但形态给通量设上限(血管直径、表面积
体积比)。对应过来:资源分配无法塑造骨架,但骨架决定了可分配的上限。所以建议层有一份
非空的职责——在骨架给定的约束内做分配——同时被禁止改动骨架。

这一条替代外部框架里"能量模型暂时挂起"的说法。挂起是悬而未决,挂载点是可执行的接口
约定:openpilot 侧只承诺接口稳定,biofield 侧可以自由探索能量模型。

**骨架的判据是可再生性**:骨架量必须能从代码无损地重新提取(文件结构、合约结果、
测试状态),读一次、再读一次,值相同。能量流不是这样的量,所以它天然属于建议层。
脱离结构谈流动,得到的是热力学而不是生理学。

### D9 新增规定性字段必须先通过可观测性检验

metadata 是从真实项目状态到读数向量的一个映射。若两个实质不同的真实状态映射到同一个
读数向量,这个差异就是**不可观测的**,此时 `residual == 0` 只意味着
"在我能看见的维度上达成了"。

**规则:新增一个规定性字段之前,必须说出它区分了此前无法区分的哪两个状态。**
答不出来的字段只增加量纲,不增加信息。

这条比外部框架的"提取/注入两问"更严格,后者只保证字段可填可用,不保证字段带来区分度。

**推论:出现系统性误通过时,先找不可观测维度,不要先调阈值。** 调阈值在不可观测的
维度上没有作用,只会把问题挪到别处。

### D10 三个信号共用一份追加式提取记录

**现状(已核对代码)。** `Code/src/autonomous_iteration` 里存在重试历史
(`models.py` 的 `retry_history`)、执行历史(`task_models.py` 的 `execution_history`、
`tool_planning_executor.py` 的历史摘要函数)、以及 `logs/autopilot.jsonl`。但:

- `iteration_history`、`previous_iteration`、`last_iteration` 在 src 下**零命中**;
- `snapshot` 一词已经被 git 安全提交占用(`task_executor.py` 的
  `_create_git_snapshot`、`GitSnapshotMetadata`)。

也就是说,**跨轮的 metadata 状态序列不存在**,现有历史都是任务内的重试历史和被序列化
进 prompt 的执行历史。

**决策:增加一份追加式记录,每次提取时追加 `(轮次, 事件序号, 字段 id, 取值)`。**
命名要避开 `snapshot`,以免与 git 语义混淆。

有了这一份记录之后:

- 残差 = 对最新一条记录的过滤计数;
- 眼图 = 相邻记录的逐字段比较;
- 状态变化率 = 对变化间隔求的统计量。

三个信号共用一个底层事实源,不各自维护状态。这是把信号体系落地的最小前置工作,
也是唯一一处必须先动数据结构的地方。

---

## 待标定量

规则:这一节只写标定方法,不发明数值。任何未标注来源实验的数值都是占位符。

### C1 抖动分位数(眼图闭合判据的阈值)

- **定义方向**:滞回宽度取"残差走平轮次上 `changed_fields` 分布"的高分位数。
- **标定方法**:先按 D5 的事件触发采样跑一批任务,收集残差走平轮次的
  `changed_fields` 序列,取分布的高分位数作为阈值。
- **不预设** p90 还是 p95。分位数是标定量,实验之后再定。

### C2 终止所需的连续轮数 k

- **定义方向**:连续 k 轮满足 `unmet == 0 且 unknown == 0` 才终止。
- **标定方法**:统计"首次达到零残差"到"最终稳定在零残差"之间的轮次分布,k 取能
  覆盖绝大多数回弹的最小值。
- **不预设** k=2 还是 k=3。

### C3 密度放松步长(仅在采用 D6 时需要)

- **标定方法**:标定窗口中漏检跳变的比例作为唯一验收指标,步长取"漏检为零"的最大
  放松幅度。
- 若事件触发的成本可以接受,这一项不需要标定——按 D5,密度不在第一层。

### C4 眼图闭合的度量维度

这一项**连维度都还没定**,不只是数值没定。可选:变化字段数、变化字段集合的
Jaccard 距离、单字段翻转次数。

- **标定方法**:用同一批任务轨迹离线计算三种度量,看哪一种能把"轮换型抖动"与
  "正常收敛"分开得最干净。
- 在定下来之前,可以先用变化字段数作保守占位,但不得据此下结论。

---

## 开放问题

### Q1 主动迭代的驱动力是主动评测,还是信号保真度管理?(未采纳外部改动)

**当前架构文档的立场。** `./ACTIVE_ITERATION_EXPERT_ROUTING_ARCHITECTURE.md` 第 69 行:

> 这是主动迭代的主要驱动力。

指的是 §3.2 的迭代时主动诊断评测。同一节把它定位为"系统的**诊断与测量中枢**",
并明确它不应垄断行动权、路由权和最终停止权。

**外部改动建议(第 2 条)提出的立场。** 把主动评测从"每轮驱动"降级为
"停滞时触发的诊断工具",把驱动力重新定义为"信号保真度管理"。

**判定:不采纳,记为冲突。**

理由有两层。第一,这不是补充,是换主语——它改的是架构文档的中心命题,不是往上加一节。
第二,"驱动力来自主动评测"是本项目自己得出的立场,不是外部文档带进来的,推翻它需要
的是证据,不是一条改动指令。

**我提的拆分(供后续判断,尚未定稿)。** 两边说的其实是不同层级的东西:

- **主动评测决定"下一步测什么、往哪走"** —— 内容层。这是驱动力,保持 §3.2 不变。
- **信号体系决定"什么时候还能相信当前的读数"** —— 有效性层。它不产生方向,它给
  主动评测的输出加有效性前提:欠采样时眼图不可用(D5),`unknown > 0` 时零残差不算
  收敛(D2),不可观测维度上调阈值无效(D9)。

按这个拆分,信号保真度不是驱动力的替代者,是驱动力的**前置校验**。主动评测仍然每轮
运行,但它的结论在信号无效时不能用于停止。

**关闭判据。** 需要一次对照实验:同一批任务,一组每轮运行主动评测,一组只在残差走平
时触发。看误停止率与平均轮次。若"停滞触发"组的误停止率不高于每轮组,则外部改动的
降级有依据;否则维持现状,并把信号体系写成前置校验层。在这个证据出现之前,架构文档
§3.2 不动。

### Q2 "字段发生变化"的定义(必须在实验之前定,不能靠实验标定)

> **已关闭 → 2026-07-28 第三轮 D16。** 本条目原文保留,不改写。定稿采用的分层
> (记录如实、"变化"为派生函数)比下面的分歧点列举更有约束力,倾向部分被采纳并扩充。

眼图、状态变化率、以及 D10 的记录,全都依赖这个定义。具体的分歧点:

- `satisfied` 从 `None` → `True` 算不算一次变化?
- 一个字段被重新提取但取值相同,算不算一次采样事件?
- 描述性字段的变化进不进眼图?(按 D1 不进残差,但眼图统计的是不是同一个集合?)

**为什么不能推迟。** C1–C4 是标定量,给个保守初值不影响信号的含义;而"什么算变化"
是**语义定义**,它决定信号有没有含义。如果它没定,实验采集到的读数本身就没有固定
所指,标定 C1 用的数据也不成立。

**倾向(尚未定为决策)。** `None → True` 应当计为一次变化,因为它是一次真实的状态
跃迁,不计入会让"刚开始测量"这一段看起来异常稳定。取值相同的重复提取应当计为采样
事件但不计为变化。眼图只统计规定性字段,与残差同一集合。

**关闭判据。** 写成一段可执行的判定函数,并用一条真实轨迹跑出三种定义下的眼图曲线
做对比,确认所选定义不会把"刚开始测量"误判为稳定。

### Q3 "上下文压力点"如何判定

外部框架把提取时机定为"工具调用边界 / 迭代边界 / 上下文压力点 / 决策点"。前三项里
只有"上下文压力点"没有可执行判据,目前只有散文描述。

按 D5 采用事件触发之后,这一项的紧迫性下降——提取由状态改变触发,不由上下文状态触发。
但若将来引入注入侧的压力管理,仍需要判据。

**关闭判据。** 能用可读取的量(剩余上下文比例、连续无进展轮数)写出判定式,且该判定式
不依赖模型自评。

### Q4 v0–v5 消融是否全量做

外部框架提出六档消融(v0 加权标量基线 → v5 加密度自适应),指标为任务成功率、平均
迭代轮次、误停止率、死循环率、抖动率。

**成本估计:约为 Phase 0 的 6 倍。**

**倾向:先只做 v0 / v2 / v3。** v0 是基线,v2 验证"分类不压扁"是否有效,v3 验证眼图
是否带来 v2 之外的增益。v1 的信息量被 v2 覆盖;v4 只影响建议层,不上决策路径,不需要
早期证据;v5 在 D5 之下暂时无对象。

**关闭判据。** v0/v2/v3 三档跑完之后,若 v3 对 v2 的增益落在噪声内,则眼图不进第一层,
后续档位取消;若有增益,再决定是否补 v4/v5。

---

## 尚未写入本文档的讨论内容

以下两条在讨论中提出但还没展开到可以写成决策的程度,记在这里以免丢失:

- **负反馈环稳定性**:高增益加延迟会引起振荡。延迟对应评测滞后,增益对应每轮改动
  幅度。推论方向是:**评测越滞后,每轮允许的改动面必须越小。** 需要定义"改动面"的
  度量之后才能成为约束。
  *(已展开 → 2026-07-28 第二轮 D15 / C5)*
- **积分饱和 / anti-windup**:一个可控但长期不收敛的残差项会持续累积进测量优先级,
  把系统钉在它上面。处理方向是:对连续 N 轮未收敛的项冻结或降权。与 D2 的
  uncontrollable 不同——那是不可控项,这里是可控但不收敛项。
  *(已展开 → 2026-07-28 第二轮 D12)*
- **信噪比随观测时间以 √N 改善**:因此建议层的评判信号应当**沿时间平均**,而不是
  **跨维度加权**。这一条与 D4 方向一致,但还没写成建议层的计算规则。
  *(仍未展开)*

---

## 2026-07-28(第二轮追加)

### 背景

本轮讨论的输入是项目自己的原始架构草图(三个新增迭代点、"三驱动力"提法、对架构文档
§9 与 §6 的两条反驳),以及上一节末尾"尚未写入"中的负反馈环与积分饱和两条。

沿用同一条判定标准:一个想法必须至少引入一条约束、一个可测量的过程或一个定量关系,
否则只是换名。本轮的主要工作是把三个"能力"改写成"约束"。

## 已定决策(第二轮追加)

### D11 每条读数必须携带产生它的验证深度

**来源。** 原始草图中的"验证深度阶梯"(Level 0 快速/静态 → Level 1 标准 →
Level 2 深度)。它原本是成本策略,这一形式与 D2、D5 有硬冲突。

**冲突。** 若 Level 0 的快速检查可以把一个规定性字段置为满足,那它产生的正是 D5 所说的
**自信的错值**——不是缺失值,而是"已测量且自洽"的错值,比 `None` 更危险,因为 `None`
会被 D2 计入 `unknown` 并强制去测量,而错值会直接触发终止。

**决策:字段登记时声明它要求的最低验证深度;低于该深度的检查只能产生 `unmet` 或
`unknown`,不能产生满足。**

这一条把验证深度从成本策略改写成保真度约束。代价只是字段登记表多一列,不需要任何
新数值,也不需要新的数据结构(深度作为取值的一部分进入 D10 的记录)。

**附带解决的问题。** 架构文档 §9 分档规则第 2 条("若硬失败已足以决定停止,不调用昂贵
Judge")本身与 D2 不冲突——它停在失败上。但若被扩展成"便宜检查过了就不做贵的",就会
让 `unknown` 被当成满足。加上深度标注后,跳过昂贵检查是允许的,代价明确:当轮不能声称
收敛。

### D12 anti-windup 三态,且冻结只作用于建议层

**N 的口径:取该项的修复尝试次数,不取轮次。** 理由与 D6 论证"状态变化率是任务属性
不是模型属性"同构:轮次口径会让冻结时机依赖任务的忙碌程度——同一个卡住的项在密集任务
里很快被冻结、在稀疏任务里永不冻结,这是任务耦合量,不是该项自身的属性。尝试次数是
唯一与该项对齐的口径。

**代价与其处理。** 尝试次数需要归因:动作必须声明它意图修复哪些字段,而这个声明只能
由模型给出。因此按 D4 划界:

- **冻结只影响 MeasurementPriority(第二层)。**
- **残差计数完全不受影响(第一层)。**

这样即使声明不准,后果只是优先级排序变差,不会造成误停止。这是"模型自评可以进入哪些
路径"的第一个具体案例,故在此明确记录。

**三态,不是二态。** `active` / `降权`,以及——**不存在"移出残差"这个状态**。降权项
仍然计入 `unmet`,仍然可以被探索配额抽到。

**从 `unmet` 转 `uncontrollable` 必须有确定性证据或人的判断,绝不允许因为"试了很多次
没成"自动转。** 否则 anti-windup 本身就变成一条误停止通道,把 D2 要堵的洞重新打开。
这是本条中最重要的一句。

**解冻用事件驱动,不用时间。** 触发条件:该项依赖的字段状态发生变化,或出现新的失败
模式。"过 M 轮自动解冻"会把系统拉回振荡,正是本条要避免的东西。

### D13 上下文投影是确定性函数,并保留探索配额

**来源。** 原始草图中的"诊断焦点驱动的上下文收集"。它落在 Q3 未解决的那一半:按 D5
采用事件触发后,**提取侧**不再需要"上下文压力点",但**注入侧**仍然需要判据。

**决策:投影的输入是当前 `unmet ∪ unknown` 的字段集合;字段登记时携带它的证据来源
(文件、测试、契约),这些来源的并集即本轮注入的上下文。模型不参与选择,只消费结果。**

**它带来的可标定量:投影覆盖率** —— 本轮实际改动的文件中落在投影内的比例。

- 覆盖率低 → 投影漏了;
- 覆盖率高而残差不降 → 投影对但策略错。

这两种失败此前无法区分,这是本条通过 D9 可观测性检验的依据。

**内生风险与处理。** 投影会自我强化盲区:不可观测维度上的问题永远不会进入投影,因为
没有字段指向它。处理方式是每轮保留一小份**与当前残差无关的探索配额**。

**共性原则(本轮识别,值得单独记住)。** D6 的标定窗口、D13 的探索配额、D12 的事件驱动
解冻,是同一条设计原则的三个实例:

> 任何从自身读数估计出来的收紧动作,都必须留一条不受该估计支配的通道。

这三处是独立提出的,却指向同一结构,这比三条各自成立更有说服力。

### D14 失败模式只承认确定性来源,升级规则要求单调

**来源。** 原始草图中的"跨迭代失败模式升级"。

**它通过可观测性检验(D9)。** 两轮的残差集合相同、眼图也相同,但失败原因不同(一轮是
导入失败,一轮是断言不成立),这个差异在字段级完全不可见。所以它不是眼图的换名。

**决策(信号侧):只承认有确定性来源的失败模式** —— 测试框架的 error type、退出码、
契约违规类别、超时。凡是只能靠模型描述的失败原因,一律留在建议层。理由:残差与眼图都
由确定性代码算出,失败模式若由模型归类,就把模型自评带进了第一层,与"信号体系是 harness
侧能力"的目标框架冲突。这使本条的落地范围显著变小,但小的那部分是可信的。

**决策(控制侧):"升级"是控制动作,不是信号。** D7 表格中"走平 + 张开 → 转换策略"
已为它留了位置;本条真正新增的是升级规则:

> 同一失败模式在同一字段上重复 N 次后升级到更深的手段,**且不允许回退到已经失败过的
> 手段**。

单调性比阈值重要:没有它,Router 会在两三种同样无效的手段之间循环,而残差恒定、字段
未变、眼图张开——三个信号全都看不出来。

> **补记 → 2026-07-28 第三轮 D17。** 本条写下时"更深"没有偏序来源,单调性因此不可
> 执行。D17 把偏序统一到 D11 的验证深度阶梯,并规定了手段耗尽时的终止态。

### D15 改动面的主度量与盲区指示器

**主度量:本轮触及的规定性字段数。**

- 文件数不行:一次重命名能扫过几十个文件而语义改动为零;
- 依赖闭包太贵,且需要静态分析。

两个量都能从 D10 的记录里算出,不需要新数据结构。

**已知盲区与其指示器。** 若改了代码而没有字段覆盖它,改动面读数为零。因此必须配一个
盲区指示器,用 diff 规模作代理量。它不精确,唯一用途是判断"本轮大部分改动落在观测
之外",此时**眼图不可用**。这与 D5 的欠采样假张开是同一种失效,只是原因从时间维度换到
覆盖维度,可直接沿用 D5 的处置。

**延迟侧的度量:一次改动到其对应字段被重新提取之间的事件数。** 由 D10 的
`(轮次, 事件序号, 字段 id, 取值)` 直接可算。

**约束成形:本轮允许的改动面,是当前最大未结算延迟的单调不增函数。** 具体函数形式不在
此发明,列为待标定量 C5。

**边界说明(不违反 D4)。** 这条约束的执行点在 Controller 的预算,不在信号层。第一层
仍然只读二值字段;改动面只影响"继续的方式"。D7 表格中"下降 + 闭合 → 谨慎继续,缩小
改动面"这一格,至此有了可执行的量。

## 待标定量(第二轮追加)

### C5 改动面与延迟的函数形式

**待定内容。** D15 只确定了单调不增的方向,没有确定形式(线性衰减、分档、还是硬上限)。

**标定方法。** 在同一批任务上记录每轮的 (最大未结算延迟, 改动面, 残差是否下降),
观察在哪个延迟区间上"改动面越大、残差下降概率越低"这一关系开始显现;函数形式取能分开
这个区间的最简形式。

**不预设分档数,也不预设衰减系数。** 本节按本文档规则只写标定方法:任何数值在实验给出
之前一律视为占位符。

**先决条件。** 依赖 Q2(字段发生变化的定义)先行确定,否则延迟的起止点无法计算。

## 对既有条目的补记

### RTS-X1 首个真实 provider shadow 样本

该轮只验证 read-only harness 的边界与记录能力，不是 H1-H4 或 E2/E3
效果实验。冻结协议 `771dbe99...1505`，3 个真实仓库任务 × 4 种模式共 12
格，provider 为 `deepseek-v4-flash`。12/12 记录均无工具、命令、写文件或
项目 mutation，仓库 before/after fingerprint 相同；但 objective success
仅 1/12，shadow gate 为 false。

失败主要来自 provider 超时/截断和 evidence 字段契约不匹配（模型返回
`verbatim_quote` 而冻结 schema 要求 `quote`）。因此该样本证明了失败可落盘、
EvidenceBundle 可 replay 和权限边界可审计，但不支持真实任务可靠性、架构
平均效应或进入真实 executor。下一步先修订并重新冻结 provider-compatible
输出契约、独立 quote-to-answer evaluator、超时与 partial-response 规则，
再预注册正式 real-task gate；不得下载本地 8B 作为绕过条件。

权限表述修正为：允许且仅允许 model-provider transport network；tool-originated
network、命令、写文件和 project mutation 均禁止。

### RTS-X2/X3 provider evidence contract 收敛

X2 把 provider/schema failure 从 `9/12` 降为 `0/12`，12/12 答案正确，
但自由文本 quote 仅 `4/12` 命中预注册证据，因此 gate 保持 false，未做
事后放宽。X3 改用公开 evidence candidates、干扰候选和隐藏 answer-to-ID
映射，并在三项未见任务上得到 `12/12` answers、evidence 和 objective
success，repository fingerprint 稳定且无副作用。

决策：探索性 real-provider harness gate 已关闭，可以设计正式 real-task
shadow 样本；E2/E3 真实任务效果、真实 executor 和生产 mutation 仍未授权。

### Q1 补记:区分"停滞时切换策略"与"停滞时才去测"

本轮讨论中提出的"检测到停滞就升级手段/切换策略"(见 D14 控制侧)与 Q1 中已判定不采纳的
外部建议**不是同一件事**,这里明确区分,以免本文档后续被读成对该建议的自我背书:

- **不采纳的是观测侧的降级**:把主动评估降为停滞后才触发的诊断手段,即平时不测。这会
  让 `unknown` 长期存在而不被计入,与 D2 冲突。
- **本轮采纳的是控制侧的响应**:每轮照常提取,残差走平时改变的是"怎么继续",不是"是否
  观测"。

**结论:Q1 的判定不变,架构文档 §3.2 不动**,仍然等待那一次对比误停止率与平均轮次的
受控实验。

## 对架构文档的两条建议(不授权改动)

以下两条来自本轮对原始草图中两处反驳的评估,**仅作为建议记录,不构成对架构文档的修改
授权**,与本文档开头的定位一致。

**建议一:§9 的 MeasurementPriority 乘式降为意图说明,分档规则 1–6 保留为规范。**
理由:该式的七个因子中只有 Criticality 与 ExpectedMeasurementCost 有确定性来源,其余
需要模型估计;而乘式的性质是任何一个因子取默认 0 就把整项归零。这与 D4 批评
`score: float = 0.5` 默认值的是同一类问题——默认值让"没人填"与"评估结果"数值上无法
区分,只是这里的后果从"看起来中立"变成"静默消失"。分档规则本身是确定性可执行的,不受
此影响。

**建议二:§6.1 的七个专业化因子若要进入实现,需要一张强制填写的表。**
理由:`Expert Role != Model Identity` 这一条只有在因子被逐项写明时才有约束力;否则
"专家"退化为提示词换名,而本文档全篇的判定标准是一个想法必须引入约束、可测量过程或
定量关系。

**另记一条策略性观察。** 本轮的信号体系约束(D11–D15)针对的是观测行为,而 §6 的专家
分工收益针对的是能力分配。前者在更大模型上依然成立(更大的模型不会因为更聪明就看得见
未提取的字段),后者可能被模型能力提升抹平。因此 §3.3 / H4 的跨规模可迁移性假设,应当
优先在信号体系上验证,而不是在专家分工上验证。

---

## 2026-07-28(第三轮追加)

### 背景

本轮只做一件事:关掉两个阻塞下一版架构文档的**语义定义**问题。两者都不是标定量——
给保守初值不能让它们成立,只能靠定义关掉。

- Q2("字段发生变化"的定义)阻塞 D15 的延迟计算与 C5 的横轴;
- D14 的"更深"缺一个偏序来源,单调性没有尺子就形同虚设。

本轮结论均由讨论达成,不含任何数值,不引入新数据结构。

## 已定决策(第三轮追加)

### D16 记录如实,"变化"是记录之上的派生函数(Q2 定稿)

**Q2 由开放问题升为决策。** 原条目保留在上一节,不删除。

**分层是本条的核心。** 先前 Q2 被问成"什么算一次变化",这个问法迫使记录层替眼图做取舍。
正确的分层是:

- **记录层如实记录每一次采样事件**,包括取值与上一次相同的采样。两次采样得到相同的值,
  是一个真实事实,必须留下;它是"测过且没动"与"没测过"的唯一区分依据,而这两者的混淆
  正是 D2 要堵的洞。记录层不做任何判断。
- **"变化"是记录之上的派生函数。** 眼图、状态变化率、改动面各自按自己的定义从同一份
  D10 记录派生,互不迁就。

这样 D10 的追加式记录不需要为任何下游信号做妥协,新增下游信号也不需要改记录格式。

**变化分型。** `None → 有值` 与 `有值 → 不同值` 是两类不同的事件,都进眼图,但用途不同:

- **首现变化(`None` → 有值)**:一次真实的状态跃迁,是"信号出现"。必须计入,否则
  "刚开始测量"这一段会显得异常稳定(这正是 Q2 原条目担心的误判)。
- **翻转变化(有值 → 不同值)**:抖动的唯一来源。

**D15 与抖动判据只看翻转。** 理由是结构性的:按首现也计入,每个字段的首次提取都是一次
变化,第一轮必然触及全部字段,改动面读数结构性地达到最大值。若 D15 的预算把首现算进去,
延迟预算在第一轮要么锁死系统,要么被迫形同虚设。分型之后,"信号出现"被完整记录,又不会
被读成系统在抖。C1(抖动分位数)取的是残差走平轮次,属后期轮次,不受此影响。

**置信度变化不算变化。** `True@Level1 → True@Level2`:记录层两条都留(按上述如实原则),
但派生成"变化"时不计入——字段状态没动,动的是我们对它的置信度。置信度应走另一条派生量,
它正是 D11 要暴露的东西,与抖动不是一回事。

反方向不需要单独规定:换用更便宜的检查会让读数按 D11 退回 `unknown`,那按取值本身
已经是一次翻转。

**描述性字段。** 与残差同一集合:眼图只统计规定性字段,与 Q2 原条目的倾向一致。

**关闭判据(继承 Q2)。** 上述定义需写成一段可执行的判定函数,并用一条真实轨迹跑出
首现/翻转分开与不分开两种情形下的眼图曲线做对比。**在该函数写出并对照之前,本条是
定义而非已验证结论。**

### D17 D14 的偏序来源统一到 D11 的验证深度阶梯

**决策:手段的"深"即它能达到的验证深度。** D14 的升级单调性直接使用 D11 的阶梯作为
偏序,不再发明第二套等级。

**理由。** 两条决策共用一把尺子,顺带解释了升级为什么有成本——更深的手段就是更贵的
检查,更高的置信度。若各自定义等级,"更深"会由 Router 自行解释,单调性形同虚设。

**代价(明确记录)。** 这把 D11 与 D14 绑在一起:此后修改 D11 的阶梯会牵动 D14 的升级
空间。接受这个耦合,是因为替代方案(两套等级)的代价是单调性不可执行。

**推论:最低验证深度是下界,不是上界。** 字段登记的最低深度是"能声称满足"的下界,
D14 的升级空间在这个下界之上。因此**登记深度定得太高会同时抹掉该字段的升级手段**——
若某字段的最低深度已在阶梯顶端,它天生没有升级空间,失败模式重复只能直接走人。这一
推论应写入字段登记表的填写说明。

**终止态:手段耗尽升级到人,不改残差分类。** 阶梯顶端的手段也失败时,**不允许**把
`unmet` 转为 `uncontrollable`。

理由与 D12 中最重要的那一句同源:手段耗尽证明的是"我们的检查手段用完了",不是"这个
字段不可控"。它与 D12 明令禁止的"试了很多次没成自动转"是同一类推理,只是形式更体面。
按 D12,转 `uncontrollable` 仍然只能凭确定性证据或人的判断。

## 对既有条目的补记

### D15 补记:改动面口径按 D16 收窄为翻转变化

D15 的"本轮触及的规定性字段数",口径明确为**发生翻转变化的规定性字段数**,首现变化
不计入预算。延迟的起止点同样按 D16 的派生定义计算。C5 的先决条件(依赖 Q2)至此满足。

### 字段登记表:已累积六列(结构性观察)

D1(规定性/描述性)、D2/D3(severity 档)、D9(可观测性检验说明)、D11(最低验证深度)、
D13(证据来源)、D17(升级空间说明)全部落在同一张字段登记表上。这张表实际上已成为整套
体系的重心,但它在架构文档中尚不存在。

**建议(不构成修改授权):下一版架构文档把它立为一节强制表格,其余各节引用它**,
而不是各节各自散述。这是下一版最主要的结构性改动。

### 下一版架构文档的导入范围(建议,不构成修改授权)

- **可导入**:D11–D17 及字段登记表一节。这些是约束,不改架构文档的中心命题。
- **不可导入**:§3.2 原样保留。Q1 只能由那次对照实验关闭,讨论关不掉。
- **仍欠着**:上一节"尚未写入"中的 √N 时间平均一条仍未展开;只影响建议层,不上决策
  路径。C4 连维度都未定,但属标定量,保守占位不影响含义。

---

## 2026-07-28(第四轮追加)

### 背景

第三轮之后架构文档已按导入范围改写完毕。本轮只处理当时明确列为"仍需靠讨论定、不靠实验定"
的两件事:D14 升级计数的量纲,以及 D16 关闭判据里那个尚不存在的变化判定函数。

两件都不是标定量。它们定不下来,实验就无从设计;而它们又都不需要数据就能定。这是本轮的
全部范围。**本文档仍不授权任何实现改动。**

## 已定决策(第四轮追加)

### D18 升级计数的键是 (字段 id, 失败模式, 手段档位),升级时归零

**来源。** D14 的"同一失败模式在同一字段上重复 N 次后升级",N 的量纲从未写明。

**决策:计数键是三元组 `(字段 id, 失败模式, 当前手段档位)`。** 前两项 D14 原文已经限定,
本条新增的是第三项。

**为什么必须带档位。** 设字段 F 在失败模式 M 上失败 3 次触发升级,新档位又失败 2 次。若
计数键不含档位,计数器读 5,N 一旦耗尽便永不复位,越深的档位分到的尝试次数越少。这与 D17
"更深意味着更多检查开销与更高置信度"的方向正好相反:代价更高的手段反而得到更少机会。
带上档位后,每一档各自拿满 N 次。

**归零不会让旧档位复活。** D14 的"不允许回退到已经失败过的手段"由 D17 的偏序独立保证:
耗尽的档位在偏序上被永久标记,归零只发生在新档位的计数器上。两条规则不冲突。

**计数单位是 D10 记录层的一条采样事件,不是底层调用次数。** 排掉"同一手段的调用次数"
这个量纲:一次测量尝试内部可能因网络或框架原因重试多次,把重试计进 N 会让升级时机随环境
波动。计数事件的定义是"一次完整的测量尝试产出了一个确定性失败模式(D14 口径)"。

**本条只定量纲,不定数值。** N 取几仍是待标定量,按本文档规则视为占位符。

### D19 变化判定函数第一版取严格相等,相等性按字段类型可替换

**来源。** D16 关闭判据要求的"一段可执行的判定函数",此前不存在。

**决策:第一版故意取最朴素的形式,不追求一次定死。**

- 先剥掉 D11 的验证深度标注,再比较取值。这是 D16"置信度变化不计为变化"的直接落地:
  `True@Level1` 与 `True@Level2` 剥掉标注后取值相同,判定为无变化。
- `None` → 有值判为首现变化。
- 有值 → 不同值判为翻转变化。
- 取值比较用严格相等,第一版不做容差、不做归一化、不做排序。

**为什么可以先简单再改:因为它不写入任何东西。** D16 已规定记录层如实记录每一次采样,
变化是记录之上的派生函数。判定函数每次都从 D10 记录重算,改函数的代价只是重算,不是丢
数据。这一条是本决策成立的全部依据,**因此 D16 的"记录层不做任何判断"必须严格守住**:
一旦让判定结果回写进记录层,换函数就会让历史眼图读数失效,那不是重构而是数据作废。

**唯一预留的接口:取值的相等性判断按字段类型可替换。** 会先出问题的地方一定在这里——
浮点字段的相等、集合类字段的元素顺序、结构化取值的字段序。现在没有真实字段可以判断哪种
处理是对的,所以只把相等性做成可替换的一小块,其余部分写死。

**验收沿用 D16 的关闭判据,不新增。** 严格相等版本已足以跑出"首现/翻转分开"与"不分开"
两条眼图曲线的对照,也足以暴露"第一轮改动面读数结构性最大"这一现象是否存在。对照显示分型
有意义,再谈相等性怎么细化;显示无意义,这个函数就不需要变复杂。

## 对既有条目的补记

### C4 编号错配(架构文档侧)

第三轮之后写入的架构文档 §24 表格把 C4 写成"升级触发的重复次数 N",而本文档 C4 是
"眼图闭合的度量维度"。这是导入时的编号错配,不是新增标定量。架构文档侧按本文档编号更正,
本文档 C4 的含义不变。升级重复次数 N 在本文档中此前没有独立编号,现补为 C6。

### C6 升级触发的重复次数 N(补编号)

- **量纲已由 D18 定死**:计数键 `(字段 id, 失败模式, 手段档位)`,单位为 D10 的采样事件,
  升级时按档位归零。
- **标定方法**:统计同一档位上"第 n 次失败之后仍能在本档位内转为满足"的比例,N 取该比例
  降到可忽略的最小 n。
- **不预设** N=2 还是 N=3。保守占位允许,但必须标注为占位。

---

## 2026-07-28(第五轮追加)

### 背景

本轮将信号架构与现有 task trajectory、`EvidenceBundle` 和 fixed Router 实现重新对照。结论是：
轨迹层已经提供同 run 的事件顺序、稳定 ID 和 append-only 存储，证据层已经提供 event/artifact 内容
冻结、SHA-256 和离线重放。因此 D10 不应另建一套证据存储；缺口在信号观察如何引用现有证据、
观察何时失效，以及停止、测量升级和干预策略的状态语义。

本轮同时决定推翻“正式实验必须沿用 A/B/C/D”的隐含前提。现有四模式 runner 继续作为可比性
harness，不再充当正式实验设计。**本文档仍不授权任何实现改动。**

## 已定决策(第五轮追加)

### D20 字段状态拆为满足性与可控性两个正交维度

规定性字段状态定为：

```text
satisfaction    = met | unmet | unknown
controllability = controllable | uncontrollable | unknown
```

D2 的 `(unmet, unknown, uncontrollable)` 保留，但明确为从这两个维度派生的残差分类，不再
解释成字段本身的三态。`met` 保留为长处和非回归基线。`uncontrollable` 只表示当前授权
主体有确定性证据或人的判断证明无法控制，不等于满足，也不自动等于失败。

**对 D2/D12 的关系。** 本条细化而不删除历史条目。D12 标题中的“三态”描述的是 anti-windup
优先级状态，不再作为规定性字段状态模型。

### D21 循环处置与任务结果分离,连续 k 改为独立有效验证点

Controller 的处置至少区分 `continue / stop_success / stop_budget / stop_regression /
handoff_required / blocked_external`；任务结果至少区分 `success / partial / failed /
inconclusive`。停止循环不能直接推出成功，特别是预算耗尽、不可控项和交还给人的情况。

D2 的零残差条件仍是成功停止的必要输入，但“连续 k 轮”改为连续 k 个独立有效验证点。空转轮次
没有新证据，不计数。最小独立性要求不同 `attempt_id`；复用同一 provider/cache 输出或同一
未变化证据只能计一个。证据必须覆盖自上次验证后的相关 mutation，具体 k 仍按 C2 标定。

### D22 证据新鲜度由依赖失效定义,不复用自由文本 freshness

现有证据层能证明“这条证据当时是什么且未被静默替换”，但不能证明“它对当前项目状态仍然
有效”。`ToolInputMetadata.freshness` 只是自由字符串，也不能进入判定层。

每个规定性字段必须登记 `dependency_refs`、`invalidation_triggers` 和
`freshness_scope`。相关 mutation 发生后，旧观察仍保留为历史事实，但立即失去当前判定资格，
直到新观察覆盖该 mutation。这样新鲜度由项目/环境版本和因果依赖决定，不由墙钟年龄或模型自评
决定。

### D23 D10 复用轨迹层和证据层,四元组降为派生视图

D10 的 `(轮次, 事件序号, 字段 id, 取值)` 只保留为残差/眼图计算时的派生视图。持久化事实源
复用现有 task trajectory：信号观察写 event，大结果写 artifact，判定层观察通过 `EvidenceBundle`
绑定到同一 `run_id` 下的内容和 SHA-256。

信号观察事件还必须绑定 `field_schema_version`、`extractor_id/version`、
`attempt_id`、验证深度、证明方向、`evidence_refs` 和字段登记表 fingerprint。项目/环境
版本与 mutation 边界从引用的 trajectory event 取得，不在信号层复制。这样既不重复证据基础设施，
也不会让 extractor 或登记表升级后用新语义冒充原实验回放。

### D24 测量证明能力与干预策略分离

D11/D17 只约束测量侧：检查声明能否证明 `met`、能否证明 `unmet`，两个方向各自有最低
验证深度。低于对应深度时只能得到 `unknown`，除非该检查登记了该方向的单侧可靠性。证明
能力允许偏序，不强制所有检查组成一条 Level 0/1/2 全序。

D14 的“升级手段”拆成两条：测量可以升级到证明能力更强的检查；修复由独立的
`strategy_id/version` 表示。Router 不得把更深测试当作新修复策略，也不得在依赖和失败模式未变
时重新选择已经耗尽的干预策略。测量或策略耗尽只触发交还给人/预算/阻塞处置，不自动产生
`uncontrollable`。

**对 D18/C6 的影响。** D18 原计数键中的“手段档位”分拆为 measurement method 和 intervention
strategy 两类计数，各自在切换到新 method/strategy 时归零，旧项仍保持耗尽。C6 不再预设两类使用
同一个 N；二者应分别标定，避免把测量失败频率当成修复策略失败频率。

### D25 Router 决定必须绑定历史输入、绑定和授权快照

可回放路由决定必须绑定 registry、role Spec、model binding、路由输入、上下文投影和候选集合的
fingerprint，并为每个候选保留命中依据或拒绝原因的 trajectory 引用。重放时不得查询当前 binding
代替历史 binding。

active 实例的授权快照必须冻结工具名、目标作用域、参数约束、网络边界、权限档和预算；执行器在
每次工具调用前 fail-closed 校验。shadow 决定不产生执行授权。历史决定缺关键 fingerprint 或授权
快照时只能用于观察性分析，不能重新执行或充当正式 route-outcome 证据。

### D26 正式实验协议重做,四模式只保留为 harness

正式实验不再被 A/B/C/D 形式绑定。每个实验先冻结单一研究问题、唯一主要处理变量、主要
estimand、最小充分对照、预算政策、任务分层与随机化、分析计划和机制中介量。H1-H4 分拆验证，
不通过一个总实验同时宣称。

第一批正式问题拆为虚拟特化、主动测量、主动控制、Router 和跨尺度五类。探索性实验可以修改
阈值和对照，但必须与 confirmatory 结果分开。旧四模式 runner、manifest、receipt、hidden evaluator
继续复用，因为它们是实验基础设施，不是实验假说。

### D27 E2 V1 在执行前否决,V2 先冻结命题与合法省略机制

E2 V1 虽已绑定协议字节和候选来源，但独立标注审查发现它不足以唯一确定实验：字段描述没有定义
“被检查的冻结 dossier 命题”，C1-C8 没有 coverage/前置条件和冲突规则，C5-C8 缺 claims、evidence
packet、mutation manifests 与 deliverable/rubric 输入；若所有字段都是 unknown 且各自由唯一检查解析，
fixed 与 active 还会结构性执行同一组检查。此时直接填写 outcome 等于看着期望效应补设计。

因此 V1 保留但不执行，也不改写原文件。V2 必须在 outcome 揭示前冻结：字段操作性语义、完整 case
envelope、measurement registry、确定性 checker、冲突/unknown/nuisance 规则，以及双方共享且有独立
证据的零成本 initial values。active 允许省略的来源只能是预先冻结的有效 seed 或预先登记的重叠证明
能力，不能来自历史结果或事后修改 coverage。V2 当前仅为 draft；机器协议、suite 和所有 fingerprint
完成前 `hypothesis_evidence_eligible=false`。

### D28 E2 synthetic feasibility 通过,只开放 E2+E3 pilot

V2 在 outcome 揭示前冻结机器协议、candidate-specific seed IDs、C1-C8 checker identity、五个内容寻址
fixture 和 policy 语义。suite loader 重算字段真值与完整 measurement matrix；bound runner 绑定 protocol、
suite、case、policy、runner 和 result fingerprint 后才比较。结果为 fixed `62`、active `53`，两组均
`5/5` exact diagnosis、`0` critical error，预注册 cost delta `-9`。

本结果只证明 synthetic same-domain feasibility：主动顺序能利用已冻结 seed 与 C4/C5 重叠 coverage，
在不损失该 fixture 集诊断正确性的情况下少做检查。它不估计真实任务分布效应，不能标为通用 E2 假说
证据。阶段门只开放到 E2+E3 closed-loop pilot；必须让两组共享同一诊断读数、任务、预算和执行器，
之后才可做 isolated E3 Controller ablation。

### D29 joint pilot 先离线接线,不直接改生产 Controller

当前 `AgentRuntimeController` 主要包装 session、吸收终态并发出 runtime diagnostics，iteration agent 的
停止依据仍是 hard gates 与 gain verdict；两者尚未消费 E2 bound diagnosis。joint pilot 先使用确定性
action transition harness，冻结 action registry/effects、三次预算、mutation invalidation、post-action
validation 与终态处置。这样先回答“E2 读数能否安全驱动 E3 控制”，不把 provider、权限、工具执行和
生产状态机变化一起混入处理变量。

control 固定为 fixed measurement + fixed applicable-action order；treatment 固定为 unknown-first
measurement + residual coverage/severity/cost action policy。两组共享 action catalog、effects、executor、
预算和隐藏真值。joint 结果不能归因 E3；只有 joint gate 通过后，才冻结相同 diagnosis trace 做 isolated
E3 ablation。

### D30 joint V1 否决,V2 通过后只开放 isolated E3 ablation

V1 首次试运行暴露出 action effect 语义错误：A1 等动作可以直接把 `synthesis_ready` 或 deliverable
相关派生结果置为 `met`，因此即使数字有利也不保存为正式结果。V1 保留为被否决的审计工件。V2 规定
action 只修改原子字段，`synthesis_ready` 必须在每次 action 后由六个前置字段确定性重算；只有 A5
可以修复 `deliverable_rubric_met`。发生变化的原子与派生字段都必须在同一 trace step 中完成
invalidation 和 post-action validation，成功停止才算新鲜。

V2 五案例新鲜回放中，control 与 treatment 均恢复 `5/5`、严重失败 `0`、fresh success `5/5`，恢复、
安全、新鲜度三项 gate 全部通过；control 总逻辑成本 `82`，treatment `68`，可比较差值 `-14`。该结果
只支持 `synthetic_same_domain_joint_feasibility`，不支持 E3 单独归因或真实任务分布外推，继续固定
`hypothesis_evidence_eligible=false`。阶段门因此只开放到固定同一 diagnosis trace、仅改变 action
policy 的 isolated E3 ablation；生产 Controller 仍不改动。

### D31 isolated E3 只作 post-joint mechanism attribution

E3 V1 将 `unknown-first-v2` diagnosis 每 case 只执行一次并冻结 fingerprint，再把完全相同的 diagnosis、
measurement trace 和 `53` 总测量成本交给两臂；唯一变化是 fixed-action-v1 与
residual-controller-v1。两臂均恢复 `5/5`、严重失败 `0`、fresh success `5/5`，fixed/residual 动作成本
为 `20/15`，相同测量成本下总成本为 `73/68`，delta `-5`。因此在当前 synthetic suite 内，joint delta
`-14` 可机械分解为 E2 测量差 `-9` 与 E3 动作选择差 `-5`。

该分解发生在 joint outcome 已揭示之后，同一案例上的 policy 行为不再是未见结果，故只标为
`synthetic_same_domain_isolated_control_feasibility` 与 `post_joint_exploratory=true`，不能升级为独立确认性
E3 假说证据。下一证据阶段必须使用在策略结果揭示前冻结的 holdout，并继续保持 matched diagnosis；
生产 Controller 仍不接线。

### D32 exhaustive lattice 否决 residual-controller-v1 的安全外推

在任何 lattice outcome 生成前，V1 冻结 action 可覆盖七原子字段的全部 128 个二值赋值、完整 severity、
派生 `synthesis_ready`、共享 diagnosis、相同三动作预算及 gate-first estimand。结果为 fixed/residual
recovery `92/95`、severe failure `14/26`、fresh success `92/95`。虽然 residual recovery 较高且原始成本
为 `1079/971`，safety gate 失败，因此成本比较被禁止，`pilot_claim_supported=false`。

根因不是 measurement 漂移，而是 residual-controller-v1 只把 direct `covers_fields` 纳入排序。A2 修复
major relationship 后可经派生规则同时恢复 critical `synthesis_ready`，但 V1 看不到这个 closure effect；
在三动作预算下，它可能优先 A5 而留下 relationship + critical synthesis。后续修复必须新建
closure-aware policy 版本，先在该 lattice 上作开发回归，再用未揭示 holdout 确认；不得覆盖 V1 协议或
把失败后的策略改动记为原预注册实验通过。生产 Controller 保持不变。

### D33 closure-aware V2 通过开发回归,但不升级证据等级

V2 协议先绑定 V1 lattice protocol 与失败 result summary，再冻结单步投影语义：应用候选 action 的原子
effect、重算 derived fields、按投影后实际消除残差的最高 severity、消除数量、cost、action ID 排序。
这使 A2 恢复 relationship 时产生的 critical `synthesis_ready` closure effect 能进入决策。

在已揭示 128-state lattice 上，fixed/V2 recovery `92/95`、severe failure `14/12`、fresh success
`92/95`；V1 的 14 个 residual-only severe 全部消失，且没有新的 V2-only severe。开发 recovery/safety/
freshness gate 全部通过。原始成本 `1079/1029` 因 recovery 不相等仍不可比较，delta 为 null。

该结果固定为 `post_failure_development_replay_on_revealed_lattice`，只说明已知失败被修复且全 lattice 未见
新 safety regression；它不是确认性 E3 证据，`hypothesis_evidence_eligible=false`。下一步需在 outcome
揭示前冻结新的 holdout diagnosis；在 holdout 完成前不再修改 V2 ranking，生产 Controller 不接线。

### D34 transition holdout 聚合门通过,但暴露 case-level safety gate 缺口

V2 ranking 冻结后，holdout V1 预先生成 128 diagnosis × `none/A1-A5` 单动作 validated no-op 的 768
案例。no-op 消耗 cost/budget、action 标记 used、完成重新验证，但不改变字段。fixed/V2 recovery
`343/352`、severe `320/314`、fresh success `343/352`；预注册 aggregate recovery/safety/freshness gate
全部通过，`pilot_claim_supported=true`。recovery 不相等，原始成本 `6578/6202` 不可比较，delta 为 null。

事后机制诊断发现两个 V2-only severe：`holdout-mask-044-fault-A2` 和
`holdout-mask-108-fault-A2`。A2 no-op 消耗一步后，V2 的排序路径用完预算并留下 critical read-only
残差，而 fixed 在对应路径没有触发 A2。V1 协议只比较 severe 总数，因此聚合改善掩盖个案回归。

决策是同时保留两点：原预注册 aggregate holdout 按原规则记为通过；生产 promotion 仍被拒绝。下一版
必须在执行前加入 zero treatment-only severe gate，且不能用这两个已揭示 case 充当确认集。V2 ranking
若为修复而改变必须升新版本，并用新的 fault timing/partial-effect holdout 验证。

### D35 fault-aware minimax V3 修复 case-level 回归,仍只算开发证据

V3 在协议中绑定 transition holdout protocol/result 和两个已揭示 A2 no-op severe case，并预先增加
zero treatment-only severe gate。策略维护剩余 fault hypotheses：成功验证排除当前 action fault，validated
no-op 消耗唯一 fault；随后在剩余动作预算内递归规划。纯 worst-case minimax 在安全指标打平时会因低成本
继续选择 A2，因此冻结目标顺序在 critical/加权/总残差之后加入 fixed-policy safety precedence，再使用
nominal cost 和 action ID。这是通用平局规则，不读取执行 case 的真实 fault target。

同一 revealed 768-case 开发回放得到 fixed/V3 recovery `343/349`、severe `320/314`、fresh success
`343/349`，V3-only severe 为 `0`；mask 044/108 + A2 两例均走 `A1,A3,A4` 且不再 severe。recovery、
aggregate safety、case-level safety、freshness 门均通过。recovery 不等，所以原始成本 `6578/6462` 不可
比较，delta 为 null。

该结果不重新解释 D34 的原 holdout，也不提升假说证据等级；`hypothesis_evidence_eligible=false`，生产
promotion 继续拒绝。下一实验只能在 V3 ranking/fingerprint 不再变化后，预先冻结未揭示的 fault timing、
partial effect 或其他新 transition 维度。

### D36 冻结 V3 通过独立 partial-effect holdout

在 D35 的 V3 policy、protocol、development result 和 policy fingerprint 全部冻结后，holdout V1 预先固定
128 diagnosis × 六个 fault variant：`none`，以及 A1/A2 各自精确省略一个原子 effect 的五种 partial
success。非省略 effect 正常应用并重算 derived field；动作仍消耗预算/cost、标记 used、执行后验证。
该维度此前未用于 V3 开发，且 holdout 协议 SHA-256 `c82504d4...5957f62` 在 outcome 前落盘。

结果 fixed/V3 recovery `417/425`、severe `254/246`、fresh success `417/425`，treatment-only severe 为
`0`。recovery、aggregate safety、zero treatment-only severe、freshness 四项有序 gate 全部通过，
`pilot_claim_supported=true`。recovery 不等，因此原始成本 `6578/6520` 不可比较，delta 为 null；不能用
原始成本差追加效率结论。

该结果把 V3 的证据从“已揭示 no-op 数据上的开发修复”推进到独立 synthetic partial-effect holdout 通过，
但 `hypothesis_evidence_eligible=false` 保持不变。它不覆盖 transient retry、fault timing、多故障、真实工具
副作用或 receipt 完整性，故不授权生产 promotion。后续若继续实验，应冻结新的时序/重试维度或转入受控
shadow executor 验证，而不是继续复用本 768 case 调整 V3。

### D37 E2 nuisance holdout 否决 unknown-first V2

30-case 独立 nuisance/freshness holdout 中，fixed/V2 exact correct 均为 `10`，但 critical error
`28/31`，因此 critical noninferiority 失败；false success 均为 `0`。根因是 V2 在
`broken-runtime-handoff` 首选唯一 critical derived measurement，首读不可用、失效或冲突后没有替代
证据恢复 `synthesis_ready`。失败结果保留，禁止比较成本，生产 measurement policy 不接线。

### D38 E2 V3 修复 nuisance 后被独立 timing holdout 否决

failure-exposure-aware V3 在已揭示 nuisance 集上把 correct `10 -> 13`、critical error `28 -> 16`，只算
开发修复。随后冻结的 attempt-2/3 unavailable/invalidation holdout 得到 fixed/V3 correct `8/5`、critical
error `12/10`。accuracy noninferiority 失败，较少 critical error 不得补偿完整诊断下降；V3 不晋级。

### D39 E2 guarded V4 是 baseline-safe 折中,不是 V3 全面改进

V4 只把第一个 selected attempt 固定为 fixed-v2 applicable order，之后沿用 V3；失败首步仍消费 prefix、
cost 和 availability。两个已揭示集分别过门：nuisance 上 V4 与 fixed 同为 `10 correct/28 critical`，timing
上为 `8 correct/2 critical`，对 fixed 是 `8/12`。但 V4 放弃了 V3 在 nuisance 开发集的 `13/16` 收益，
所以只能描述为跨已揭示失败的 baseline-safe safety compromise，并必须用新 base fixture 独立确认。

### D40 E3 timing V1 只确认 positional no-op 与 optional retry

896-case V1 得到 fixed/V3 recovery `291/299`、severe `474/462`、treatment-only severe `0`，冻结门通过。
两臂共享的是 fault position/kind，fault 分别附着于各臂当步所选 action；retry 是 controller 可选，不是
adapter 强制。V1 未生成 missing receipt 或 delayed validation，且只持久化 aggregate summary，因此不能
被称为完整 E3-R1，也不满足真实环境入口证据要求。

### D41 receipt/validation 必须逐 attempt 持久化且 fail closed

V2 新增 typed attempt ledger，记录 receipt、validation、retry identity，并将原始 ledger SHA-256 绑定
到结果。missing receipt 不进入 trusted state；delayed validation 由独立 checkpoint 闭合；永不抵达的
validation 阻断 `stop_success`。fixed/V3 recovery 为 `29/38`、severe failure 为 `19/11`、fresh success
为 `24/30`，false-success stop 均为 `0`；`5/8` 个物理恢复但证据不完整的 case 被安全停在
`evidence_blocked`。因此 freshness 与总 gate 失败，成本禁止比较。失败结果保留，不能事后修改同一
holdout 的 freshness 定义；生产 Controller 不接线，`hypothesis_evidence_eligible=false`。

### D42 receipt reconciliation V4 只改证据闭合,不改动作策略

V4 绑定 E3-R2 的失败 protocol/result/ledger，明确拒绝把 freshness failure 归因给 action ranking。adapter
要求 same-action retry 复用幂等键；只有成功 receipt 与 affected fields 的完整验证同时存在时才能闭合缺口；
终局最多允许一次零成本、不耗 action budget 的 validation checkpoint。动作选择、原子 effect、动作 cost
和 recovery 均保持不变。

在 revealed 56-case 上，fixed/V3 recovery 仍为 `29/38`、severe `19/11`、cost `474/416`，但 fresh
success 变为 `29/38`、evidence-blocked 变为 `0/0`，false success 仍为 `0`。这是开发修复，不是确认；
必须冻结新的 receipt/validation 位置与失败组合后再运行独立 holdout。

### D43 reconciliation V4 通过新 receipt/validation holdout

确认协议绑定 V4 protocol/result/ledger/adapter fingerprint 后，使用新的 56-case 组合。fixed/V3 recovery
`49/49`、severe `7/7`、fresh success `49/49`、false success `0/0`，treatment-only severe 为 `0`，所有
质量和安全门通过。主结果完全相等后，动作成本 `423/415` 合法比较，delta `-8`。

该结果关闭 E3 的 synthetic receipt/validation 单项出口，但仍不授权真实执行；下一步只开放绑定 E2 V4
confirmation 与 E3 reconciliation confirmation 的最终 joint nuisance holdout，并要求逐 case 测量、动作、
receipt、validation 与 evidence binding 全量持久化。

### D44 D43 经语义审计作废，E3-C4 V1 不是有效 confirmation

独立审计确认 V1 的字节回放和 hash binding 稳定，但 runner 未调用冻结 V4 adapter，而是在
confirmation 内另写逻辑直接清空 missing/pending；`two-checkpoint` 变体还违反 V4
`max_terminal_checkpoints=1`。fresh validation 由 variant 名称直接生成，没有外生 observation
outcome；两个 receipt variant 映射到同一 fault，ledger 中也没有 `idempotent_retry_receipt`。

因此 D43 的数字只保留为 invalid-trial audit output，`holdout_evidence_eligible` 的声称无效，
E3 synthetic exit 仍未关闭，不得进入 joint gate。下一版必须由 development/confirmation 共用单一
reconcile 实现，使用外生 checkpoint outcomes，覆盖 stale/partial/missing 负例，严格限制一次终局
checkpoint，并验证 ledger provenance 后重新冻结未见 holdout。

### D45 E3 V5 用共享函数修复 confirmation 执行边界

V5 绑定 V4 开发 protocol/result/ledger 和作废的 V1 confirmation 三件套，把
development 与 confirmation 都收敛到 `reconcile-case-evidence-v5`。该函数只接受 typed
attempts 和最多一个外生 terminal observation；同动作 retry 必须有 retry identity、present
receipt、fresh validation 和完整字段覆盖，terminal 路径对 stale/partial/missing 一律 fail
closed。provenance 必须指向同 case/同 arm 的已知 attempt 或 observation。

在已揭示 E3-R2 上，fixed/V3 recovery `29/38`、severe `19/11`、cost `474/416` 不变，
fresh success `29/38`，false success `0/0`。31 条 reconciliation 中 retry/terminal 为
`24/7`，provenance 门通过。这是针对已知失败的 development repair，
`hypothesis_evidence_eligible=false`，不单独关闭 E3 出口。

### D46 E3 confirmation V2 通过外生观测与负向 probe

V2 在执行前绑定 V5 protocol/result/ledger/adapter fingerprint，冻结八个新 mask
`11,12,17,18,31,63,95,126` 和七个不同 variant。四个 positive variant 覆盖 retry 与
terminal 两种机制；三个 negative variant 分别提供 stale、partial 和 missing observation，
必须保持 evidence-blocked。

fixed/V3 recovery `33/33`、severe `18/18`、positive fresh success `22/22`、false success
`0/0`；48 个负向 case-arm probe 全部 fail closed，无 treatment-only severe。主结果相等后
cost `584/564` 可比，delta `-20`。ledger 持久 296 attempts、80 observations 和 40
reconciliations（retry/terminal `30/10`）。该结果关闭 E3 的 synthetic component prerequisite，
但不等于 E2 x E3 联合验证，也不授权真实环境。

### D47 joint V1 作废，其测量诊断只能驱动 E2 开发

joint V1 在 E3-C4 语义审计前便依赖该无效前置；协议显式为
`pre_execution_frozen=false` 且 `holdout_evidence_eligible=false`。runner 在 joint 执行前 fail closed，
没有 48-case 联合结果。事后 measurement-only diagnostic 在 12 case 上得到 fixed/V4
correct `12/12`、fresh `11/10`、false success `0/0`、cost `95/101`；失败来自
`attempt-5-unavailable` 只击中更长的 V4 路径。这只是 post-invalid-trial failure
localization，不是 E2 holdout 或 joint effect。

### D48 E2 balanced-prefix V5 是四个 revealed set 上的开发修复

V5 把 fixed-v2 applicable order 的前三次测量冻结为 prefix，失败尝试也消耗 prefix，
每次后重算，再用 failure-exposure-aware V3 作 suffix。它同时绑定 nuisance、timing、
V4 confirmation 和 invalid joint diagnostic 四个已揭示数据集，每组独立过门。fixed/V5
分别为：nuisance correct `10/10`、critical `28/28`、fresh `10/10`；timing `8/8`、
`12/12`、`5/5`；旧 confirmation `23/24`、`3/2`、`14/16`；invalid joint diagnostic
`12/12`、`0/0`、`11/12`。

因为 policy 是看过全部 outcome 后选定，该结果只支持 cross-revealed-set development target，
不支持独立 E2 优势。下一步只能冻结 V5 到新 joint holdout，不能重用 V1 suite。

### D49 joint V2 已冻结但未运行

joint V2 protocol SHA-256 为 `f00d7f72504dff9fd2edd8734ff95bf4112f0df7bd44445b5793d20cbf6a534b`，
新 suite SHA-256 为 `514ec7b803185f5fb5fb1ebf734f65b3bcca79d9a2f60d33ef71f2964e1800b7`。
60 case 由三个新 base、四个 measurement variant 和五个 action/observation variant 交叉生成，
绑定 E2 V5 和 E3 V5/C5，要求每 case-arm EvidenceBundle 与 negative action probe fail closed。
当前没有 result artifact，不得写成“联合门通过”，更不得进入真实环境。

### D50 joint V2 的两个 pre-run revision 因零曝光作废

D49 的 `f00d7f...a534b` revision 在 runner 语义审查中发现 attempt-3 terminal
fault 对原 base 不可达；action schedule 修订后的 `5df117...c8ce` revision 又在
内存 fixture 中暴露三个 base 均只执行三次 measurement，导致 attempt-5/6
measurement nuisance 全部零曝光。两版都不具 holdout 资格，未持久化正式 result。

处理方式不是放宽 gate：重做三组全新 long-horizon base，使 fixed/V5 两臂每个
variant 都至少执行六次 measurement；把 nuisance exposure、E2 accuracy、critical
error、diagnosis false success、measurement freshness 加入显式非补偿门。旧 hash
保留在实验日志作为 pre-run rejected revision。

### D51 final joint V2 通过，开放只读 real-task shadow

最终协议 `285843d8...747721` 绑定 suite `c64dc172...71260`，60 case/arm
得到 diagnosis correct `60/60`、critical error `0/0`、measurement fresh
`60/60`、recovery `60/60`、severe `0/0`、false success `0/0`、positive
action freshness `48/48`。measurement fault、retry、terminal、stale exposure
分别为 `45/45`、`20/20`、`12/12`、`12/12`，所有主门通过；主结果相等后
逻辑成本 `1416/1416` 可比。

240 attempts、48 observations、64 reconciliations 和 120 EvidenceBundle 已
持久化。fresh replay 与 summary/artifact 字节一致，每个 bundle 的四层内容均与
对应 case-arm 结果对账通过。该结论只开放 read-only real-task shadow；不得据此
接线 production Controller、授权写文件/命令/网络或把本地 8B 下载当作真实入口。

## 对既有条目的补记

### D5/D6 补记:mutation boundary 与安全回滚

D5 的“每次改变状态的动作后提取”存在循环定义：不提取就不知道字段是否改变。执行口径改为
“每个可能影响字段 `dependency_refs` 的 mutation boundary 后提取”。复合动作内部仍可能发生
不可观察翻转，因此“别名在构造上不可能”被 D22 的失效规则和边界观测要求收窄，不再作绝对保证。

D6 的“只允许单向放松”保留为正常自适应路径；标定窗口发现漏检后的回到更密档位明确为安全
回滚，不算普通自适应反向迁移。回滚后必须重新建立标定证据，不能立即再次放松。
### D53 real-provider corpus passes E2/E3 shadow input eligibility

RT-R1 froze eight real repository tasks with three scheduled repetitions and a
strict registry containing only `file_reader` plus no-index
`multi_file_reader`. Two 24-run attempts were invalidated because concurrent
documentation/protocol edits changed the repository fingerprint during
collection; their outcomes are not gate evidence.

The final attempt kept repository fingerprint `a89ab95a...df2` stable and
produced `24 terminal / 8 unique / 4 success / 20 failed / 22 complete`, with
task dominance `0.125`. All corpus eligibility gates passed. Only the two read
tools executed; 14 command/write/readme proposals were denied before dispatch,
and no modified-file claim was observed.

Decision: the corpus may enter formal E2 diagnosis shadow and then E3
action-proposal shadow. This decision does not claim a real-task E2/E3 effect and
does not authorize an executor, command, write, tool network, sandbox mutation,
or production promotion. The next protocol must bind provider/model/prompt and
evaluator identities and keep failure runs in the denominator.

### D54 E2 真实轨迹先过盲化 packet smoke，不直接计效果

RT-E2-S0 在执行前绑定 RT-R1 corpus、任务池/结果、source provider/model、111 个
request artifact 的联合 prompt hash、E2 V5 protocol 和确定性 evaluator fingerprint。
每个任务按 `lowest_run_id_per_task` 选一条轨迹，选择规则不读取 outcome；policy-visible
context 删除 `task_finished`/`task_pool_item_finished`，并禁止 `run.final_status` 与
task-stratum oracle。

8 个 packet 覆盖 8 个任务；任务设计预期为 `4 success / 4 failed`，实际 RT-R1 终态作为
独立隐藏 outcome 保留为 `1 success / 7 failed`。每个 packet 有 C1-C8 八个测量机会；
provider 调用和 mutation 都为零，结构门通过。该结果只证明 RT-R1 可以形成
无答案泄漏、可重放的 E2 输入，不证明 V5 优于 fixed-v2。全量 24-run 前必须冻结 checker
outcome、severity、missing/freshness/diagnosis 和非补偿 gate；E3 继续阻塞。

### D55 formal E2 V1 因 EvidenceBundle 支撑范围不足作废

V1 草稿虽得到两臂 outcome `24/24` 和 cost `264 -> 216`，但 C6/C7 把单条绑定记录
表述为 full/indexed freshness。EvidenceBundle replay 不能证明更宽的整链命题。V1 在正式
结果持久化前作废，数字只进入 invalid-trial audit，不进入 E2 gate。

### D56 formal E2 V2 通过，只开放 E3 proposal shadow

V2 把 freshness 命题收窄为“至少存在一条被选中的 same-run、content-bound、可回放证据路径”，
并绑定 V1 protocol/invalid result。24 条轨迹的任务预期为 `12/12`，真实终态为 `4/20`，其中
`16` 条符合任务预期、`8` 条不符合。fixed/V5 的终态诊断、alignment verdict 和 fresh 都是
`24/24`，severe/false success 都是 `0/0`；主结果相等后逻辑成本 `264 -> 216`，delta `-48`。
48 个 EvidenceBundle 全部可离线 replay，provider 调用与 mutation 为零。

决定：E2 real-trajectory diagnostic shadow 通过，下一步开放 E3 的只读 action-proposal shadow。
这不是执行效果估计，不允许 dispatch proposal，也不授权命令、写入、tool network、sandbox
mutation、production Controller 或真实 executor。

### D57 E3 proposal adapter 通过开发目标，但必须新 holdout

RT-R1 durable events 修正了仅看 terminal summary 的机制统计：12 条预期失败轨迹存在 policy denial；
8 条预期成功失败中，6 条是 empty-decision decomposition，2 条是 forbidden reader route。
mechanism-aware adapter 对 8 条提出只读 replan/reroute，对其余 16 条 stop/preserve。相对 fixed status
policy，mechanism match `16 -> 24`；safe/fresh 均 `24/24`，false/missed action 均 `0/0`，48 bundle
可回放，dispatch/provider/mutation 全为零。

由于 proposal taxonomy 在查看 RT-R1 后形成，该结果只能记为 post-corpus development，不能声称
formal E3 effect。下一步只开放冻结并采集新的 provider-backed holdout；真实 executor、命令、写入、
tool network、sandbox mutation 和 production Controller 继续禁止。

### D58 新 E3 holdout 先采集、后冻结确认分析

新的 `E3_HOLDOUT_TASK_POOL_V1` 固定 8 个此前未用于 taxonomy 开发的任务，按两轮产生 16 个唯一
scheduled run ID；原 task identity 只作为 `stratum_task_id` 保留，避免 recorder 将重复轮次合并。
协议绑定 task-pool 原始字节、`openai-compatible/deepseek-v4-flash` 和 endpoint fingerprint，并要求
仓库前后 fingerprint 完全相同。完整轨迹写到仓库外，再以确定性归档保存；失败 run 不能删除。

决策顺序不可倒置：先按 acquisition gate 判断新 corpus 是否可用；通过后才允许冻结独立 E3
confirmation protocol，再用 RT-E3-D1 已固定的机制映射做盲化评价。采集通过本身不等于 E3 效果通过，
也不允许 proposal dispatch、真实 executor、命令、写入、tool network 或 project mutation。

### D59 新 holdout 确认 proposal 选择效果，但不开放真实 executor

采集得到 `16 terminal / 8 unique / 4 success / 12 failed / 15 complete`，仓库 fingerprint
保持不变。独立确认协议在评分前绑定 corpus 与旧 adapter/evaluator；机制分布为 4 completed、8
policy denial、4 empty decision needs。fixed/treatment mechanism match 为 `12/16 -> 16/16`，
safe/fresh 都是 `16/16`，false/missed action 都是 `0/0`，32 bundle 可回放。

V1 因未把 archived event-derived permission gate 和 runner fingerprint 纳入冻结协议而作废，对应 invalid-trial JSON 保留。V2 重新绑定后，事件审计得到 18 file_reader、7 multi_file_reader、8 denial、0 forbidden dispatch，permission gate 通过。决定：可以确认 E3 在“只读 proposal 选择”层面对新 holdout 有效果；不能把它扩大成 proposal 执行、
task-success 或生产效果。下一步只允许设计 disposable sandbox/worktree gate，必须有 scoped
authorization、rollback、side-effect receipt 和独立 outcome evaluator；真实 executor、生产 Controller、
命令/写入/tool network 和直接拉取本地 8B 仍不自动授权。

### D60 当前里程碑收窄为核心主动迭代，专家路由与跨模型延期

当前验证目标固定为：主动测量、基于失败机制的动作选择、受限执行、动作后新鲜验证、恢复和停止。
专家专门化/动态路由与跨模型迁移是架构扩展，不再作为本轮核心主动迭代里程碑的阻塞条件。已有
R1 合成结果仅保留为实验框架预检，不继续推进 R2/R3；M1/M2 同样延期。

该范围调整不扩大现有结论。E2 真实轨迹诊断、E3 新 holdout proposal 选择，以及 S1-S3、C1-C3
合成门已经通过，只能表述为“核心机制在合成闭环和真实只读轨迹上成立”。在 disposable real-task
sandbox 中完成动作执行、独立 task-outcome 评估、回滚与零假成功门之前，仍不能声称真实执行效果，
也不能授权 production Controller。下一主实验只验证这一个缺口。该当时状态已由后续 D61-D63
推进；当前结论以 D63 为准。

### D61 首轮真实沙箱 V1 无效，V2 因 control ceiling 保持阴性

V1 的两臂都缺少完整公开规格，最终为 `0/4 -> 0/4`，并出现三个 public-pass/hidden-fail；同时
execution 只保存 response hash，无法重放实际写入内容。决定：V1 只保留为方法无效试验，不进入真实
执行效果证据。

V2 让两臂平等看到公开测试并持久化写入内容，在新四任务套件上得到 `4/4 -> 4/4`、零 false success、
源仓库不变和完整 cleanup。由于 control 位于天花板，预注册 strict-improvement gate 保持 false；不得
事后把平局改成通过。下一协议必须在 outcome 前冻结 baseline-difficulty 门和独立任务分层。

### D62 V3/V4 基础设施中断保留为 invalid trials，不改变 suite 或门槛

V3 在 outcome 前冻结八任务、四分层以及 control `<=2/8`、active `>=6/8`、至少四个 paired/
measurement-assisted improvement、零 regression、每层改善、零 false success、真实 provider、源仓库
不变和 cleanup 完整门。V3 因 provider JSON 缺 `file_content` 中止；V4 处理该对象后又因空响应触发
`InvalidLLMResponseError` 中止。两轮均没有持久化或检查 outcome。

决定：V3/V4 作为 hash-bound incomplete invalid trials 保留；V5 只能修复 malformed-response 记账，
必须绑定相同 suite、原门槛和两条 invalid record。网络、超时和鉴权异常继续 fail-fast，不能被吞掉。

### D63 RS-V5 通过，核心主动迭代里程碑在 disposable-sandbox 范围闭合

V5 得到 control/active `2/8 -> 8/8`、六个 paired improvements、零 regressions、八个
measurement-assisted successes、四个分层全部改善、false success `0`。所有 16 个最终 public/hidden
outcome 和写入 hash 可独立重放，provider execution 完整，源仓库 fingerprint 不变且沙箱全部清理。

决定：可以报告“冻结 provider/model 与 runtime-contract 分层下，E2 新鲜测量驱动的 E3 沙箱修复提高
独立任务结果”。该决定关闭 D60 指定的核心里程碑缺口，但不授权 production Controller、任意命令、
源项目写入、tool-originated network 或外部副作用。R1-R3、M1-M2、生产 rollout 和任意任务分布仍是
独立主张。
