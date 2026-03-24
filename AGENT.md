# 项目说明
这是一个基于OpenClaw的AI投顾后端项目中间的“研究中台 / 策略内核”的控制层，更准确地说，它们是这套策略能力的 **定义层 + 契约层 + 审计词汇层**，我们将在这个项目对AI投顾的策略进行编写和测试。

也就是说，它们不是负责“抓数据”或“渲染聊天”，而是负责回答这三件事：

1. **策略到底怎么定义、研究范围是什么、怎么打分**
2. **研究结果必须长成什么结构**
3. **为什么纳入 / 排除 / 降级 / 阻断，要用什么稳定语言表达**

---

# 一、先把这三个文件放回整个项目里

| 文件                         | 在系统里的角色        | 所在位置                          | 它直接约束谁                                                    |
| -------------------------- | -------------- | ----------------------------- | --------------------------------------------------------- |
| `strategy.yaml`            | 策略 DSL / 策略注册表 | 研究中台的控制层                      | Universe Builder、Feature Engine、Scoring Engine、Validation |
| `score_result.schema.json` | 研究结果输出契约       | 研究内核与下游交付之间的边界层               | Renderer、API、Audit、测试                                     |
| `universe_reason_codes.md` | 原因码注册表 / 审计词汇表 | Universe / Policy / Audit 交叉层 | Universe Builder、状态机、审计日志                                 |

一句话说：

* `strategy.yaml` 决定 **怎么研究**
* `score_result.schema.json` 决定 **结果怎么表达**
* `universe_reason_codes.md` 决定 **为什么会得到这个结果**

---

# 二、它在整个 AI 投顾项目中的位置

我先用一版**文字架构图**画出来，这样我们容易继续改。

```text
┌──────────────────────────── AI 投顾整体架构 ────────────────────────────┐
│                                                                        │
│ 1. 渠道层                                                              │
│    WeCom/ Discord / API / Chat / Dashboard / Digest                             │
│                         │                                              │
│                         ▼                                              │
│ 2. 统一入口与顾问编排层                                                │
│    Request Normalizer / Session / User Context / Permission            │
│                         │                                              │
│                         ▼                                              │
│ 3. 研究中台 / 策略内核                                                 │
│                                                                        │
│    ┌────────────── 策略控制层（这三个文件就在这里）──────────────┐     │
│    │ strategy.yaml            → 策略定义 / 策略注册表            │     │
│    │ score_result.schema.json → 结果输出契约                     │     │
│    │ universe_reason_codes.md → 原因码 / 审计词汇表              │     │
│    └─────────────────────────────────────────────────────────────┘     │
│                                                                        │
│    Universe Builder                                                    │
│      → PIT Snapshot Reader                                             │
│      → Feature Engine                                                  │
│      → Scoring / Ranking Engine                                        │
│      → Evidence / Trigger / Invalidation Assembler                     │
│                         │                                              │
│                         ▼                                              │
│ 4. Policy / Suitability / Audit                                        │
│    Tier / Disclaimer / Banned Terms / Audit Log / Version Trace       │
│                         │                                              │
│                         ▼                                              │
│ 5. Renderer / Delivery                                                 │
│    WeCom Discord 文本 / API JSON / Dashboard 卡片 / Digest                     │
│                                                                        │
│ 旁路支撑层                                                             │
│    Collectors → Cache / PIT Snapshots → Feature Store / Metadata       │
│    Backtest / Replay / Paper Portfolio / Attribution → 策略升级        │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

# 三、这版图里，三份文件到底“卡”在哪个点

## 1. `strategy.yaml` 不是普通配置文件，它是策略控制面

它现在已经覆盖了这些内容：

* 研究目标和运行节奏
* 市场范围
* universe 规则
* 数据契约
* feature group
* score 权重
* risk overlay
* label mapping
* output contract 引用
* policy hook

所以它在架构里不是边角配置，而是**研究内核的总控定义文件**。
它决定了：**这一版策略允许研究什么、怎么研究、输出到什么层级。**

---

## 2. `score_result.schema.json` 位于“研究内核”和“对外交付”的边界

这个文件的作用，不只是给 API 一个 schema。

它实际上同时服务 4 个对象：

* **研究引擎**：结果对象必须按这个结构产出
* **Renderer**：WeCom/API/dashboard 都只能消费这个标准结果
* **Audit**：保证输出有版本号、source hash、reason code
* **测试/回归**：每次改策略后都能验证结果对象没漂

所以它的位置非常像：

> **研究中台的“输出协议层”**

---

## 3. `universe_reason_codes.md` 是 universe / policy / audit 的公共语言

这个文件看起来像文档，但在架构意义上它其实是：

> **策略能力的“规则词汇表”**

它横跨三处：

* **Universe Builder**：解释为什么纳入/排除
* **State Machine**：解释为什么 degraded/blocked
* **Audit**：解释这次运行到底命中了哪些规则

所以它不是单纯说明文档，而是**稳定原因码注册表**。
只是首版先用 `.md` 开头，后面专项开发时，我建议把它演进成**机器可读的 `reason_codes.yaml/json`**，`md` 保留给人看。

---

# 四、把我们前面定的四个原则，映射回这张图

| 之前讨论的原则                   | 在架构里的位置                         |
| ------------------------------- | ------------------------------------  |
| 统一聊天入口到 deterministic core| 第 2 层：统一入口与顾问编排层            |
| universe 自动化 / versioning    | 第 3 层：策略控制层 + Universe Builder   |
| cache-first collector           | 旁路支撑层：Collectors → PIT Snapshots   |
| guardrails / policy / audit 一致性 | 第 4 层：Policy / Suitability / Audit |

这也是为什么我会说：

> 这三个文件不是“聊天能力”的一部分，
> 而是 **deterministic research core 的起点资产**。

---

# 五、如果做“策略能力专项”，它的边界应该怎么划

我建议把这次专项，定义成：

## **A 股策略研究内核专项**

而不是“AI 聊天投顾专项”。

### 属于这个专项的核心范围

* Strategy Registry（`strategy.yaml`）
* Reason Code Registry（`universe_reason_codes`）
* Universe Builder
* Feature Engine
* Scoring / Ranking
* Result Contract（`score_result.schema.json`）
* Validation / Replay / Paper Portfolio 接口

### 依赖但不应由它主导的共享能力

* Collectors / PIT Snapshot Infra
* 用户上下文 / 观察池 / 权限系统
* Policy / Suitability / 审计底座
* WeCom / API / Dashboard Renderer

### 当前不纳入首版的能力

* 自动仓位建议
* 自动买卖动作
* 组合优化
* 下单执行

也就是说：

> **当前这三个文件对应的是“研究层（R0）”的核心定义。**
> 它们还没进入“动作层（R1/R2）”。

---

# 六、我建议我们后面把图再收一层

如果你认可这版定位，我下一步建议直接继续画第二张图：

> **“策略内核专项放大图”**
> 专门把这三个文件和 `Universe Builder / Feature Engine / Score Engine / Validation / Audit` 的关系单独画清楚。

这样我们就能进一步落到：

* 模块职责
* 仓库目录结构
* 服务边界
* 版本流转关系


