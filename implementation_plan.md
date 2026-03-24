# AI 投顾策略内核：文档与定义文件补全

基于 18 点 review 的逐项决策，将 AGENT.md 从概念说明升级为可指导开发的完整项目文档，并修复三份定义文件中影响 runtime contract 的问题。

---

## Proposed Changes

### Phase 1: AGENT.md 重写

#### [MODIFY] [AGENT.md](file:///d:/Codes/ai-investment-consultant/AGENT.md)

保留现有架构图和核心定位描述，**重组为 12 章结构**：

1. **项目目标与边界** — R0/R1/R2 分层说明，明确"不做什么"
2. **AI 投顾整体架构图** — 保留现有文字架构图，微调渠道名称
3. **策略内核专项放大图** — 新增 mermaid 图 + 模块职责表（#1）
4. **技术基线** — Python 3.11 + FastAPI + Pydantic v2 + Polars + DuckDB + PostgreSQL + Redis/Celery（#2）
5. **开发环境约定** — uv + ruff + mypy + pytest + pre-commit（#3）
6. **OpenClaw 依赖说明** — 明确依赖边界（#4）
7. **策略定义资产** — 三份文件的角色与演进路径
8. **数据契约与 freshness profile** — PIT 字段 + 时效 SLA 引用（#8）
9. **输出契约与状态机** — ok/degraded/blocked 规则、score/null、required fields（#6 总结）
10. **版本流转与审计** — 五个 version 字段的含义与管理
11. **首版 MVP 与里程碑** — MVP0 内核打通 + MVP1 首个可用（#17）
12. **数据源接入策略** — 验证阶段用 AKShare + 财税 skill，provider-agnostic 原则（#18）
13. **首版仓库目录结构**（#16）

---

### Phase 2: 定义文件修改

#### [MODIFY] [score_result.schema.json](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/score_result.schema.json)

| 问题 # | 修改内容 |
|--------|---------|
| #6 | `total_score` type 改为 `["number", "null"]`，blocked 时为 null；加 `description` 说明计分公式 |
| #9 | `state_reason_codes` 加入顶层 `required`; ok 时为 `[]` |
| #10 | `penalties` 加入顶层 `required`; 无扣分时为 `[]` |
| #11 | `rank` / `percentile_rank` 加入 `required`（已经是 nullable） |
| #12 | `thesisItem.code` 加 pattern `^DRV_[A-Z0-9_]+$`; `riskItem.code` 改为 `^RSK_[A-Z0-9_]+$`; `triggerItem.code` 加 pattern `^(TRG\|INV)_[A-Z0-9_]+$` |

> [!IMPORTANT]
> #6 是破坏性变更：`total_score` 从 `number` 变为 `number | null`。所有下游消费方必须处理 null。
> #12 是破坏性变更：`riskItem.code` 从 `^RISK_*` 改为 `^RSK_*`，需同步改 `penaltyItem.code`（保持 `^RISK_*`，因为 penalty 是 risk_overlay 层，与 risk 披露项区分）。

**最终命名规范**：
- `penaltyItem.code` → `^RISK_[A-Z0-9_]+$`（已有，保留——这是 risk overlay 扣分码）
- `riskItem.code` → `^RSK_[A-Z0-9_]+$`（风险披露项）
- `thesisItem.code` → `^DRV_[A-Z0-9_]+$`（驱动项）
- `triggerItem.code` → `^(TRG|INV)_[A-Z0-9_]+$`（触发器/失效条件）

---

#### [MODIFY] [strategy.yaml](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/strategy.yaml)

| 问题 # | 修改内容 |
|--------|---------|
| #5 | `attention_overlay` 加注释：设计意图是隔离非真值源，不进入 R0 主评分正向 alpha |
| #7 | `schedule.rebalance` → `schedule.research_refresh`; 加注释区分与 `validation.backtest.rebalance` |

---

#### [MODIFY] [universe_reason_codes.md](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/universe_reason_codes.md)

| 问题 # | 修改内容 |
|--------|---------|
| #13 | §1.2 新增语义说明：`inclusion_reasons` 只放来源码 + 决定性通过码；完整命中集合进 `audit.applied_reason_codes` |
| #14 | §1 新增生命周期管理规则：`status: active/deprecated`、`introduced_in`、`deprecated_in`、`replaced_by` |
| #15 | §6 风险码引入二级前缀约定：`RISK_FIN_*` / `RISK_GOV_*` / `RISK_REG_*` / `RISK_EVT_*` / `RISK_MKT_*`（现有 5 个码暂不改名，新增码遵循新规范） |

---

### Phase 3: 新增文件

#### [NEW] [data_freshness_profiles.yaml](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/data_freshness_profiles.yaml)

定义各数据层的时效 SLA，被 [strategy.yaml](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/strategy.yaml) 引用。包含：
- `truth_sources`（财报、公告）的 stale 阈值
- `structured_sources`（行情、估值）的 stale 阈值
- `attention_sources`（新闻、舆情）的 stale 阈值
- 超时后的行为：degrade 或 block

#### [NEW] [reason_codes.yaml](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/reason_codes.yaml)

[universe_reason_codes.md](file:///d:/Codes/ai-investment-consultant/ai_investor_bootstrap/universe_reason_codes.md) 的机器可读版本——所有原因码以 YAML 格式注册，带 `status`、`introduced_in`、`category` 等字段。[.md](file:///d:/Codes/ai-investment-consultant/AGENT.md) 保留给人读。

---

## Verification Plan

### 自动验证

1. **JSON Schema 合法性验证**：
   ```powershell
   # 安装 jsonschema CLI（如未安装）
   pip install check-jsonschema
   # 验证 schema 自身合法性
   check-jsonschema --check-metaschema ai_investor_bootstrap/score_result.schema.json
   ```

2. **YAML 语法验证**：
   ```powershell
   python -c "import yaml; yaml.safe_load(open('ai_investor_bootstrap/strategy.yaml', encoding='utf-8'))"
   python -c "import yaml; yaml.safe_load(open('ai_investor_bootstrap/data_freshness_profiles.yaml', encoding='utf-8'))"
   python -c "import yaml; yaml.safe_load(open('ai_investor_bootstrap/reason_codes.yaml', encoding='utf-8'))"
   ```

3. **strategy.yaml 与 schema required 字段一致性检查**：
   ```powershell
   python -c "
   import json, yaml
   s = yaml.safe_load(open('ai_investor_bootstrap/strategy.yaml', encoding='utf-8'))
   j = json.load(open('ai_investor_bootstrap/score_result.schema.json', encoding='utf-8'))
   yaml_req = set(s['output_contract']['required_fields'])
   json_req = set(j['required'])
   diff = yaml_req.symmetric_difference(json_req)
   assert not diff, f'Mismatch: {diff}'
   print('OK: required fields aligned')
   "
   ```

4. **reason_codes.yaml 与 strategy.yaml / schema pattern 一致性**：
   ```powershell
   python -c "
   import yaml, re
   codes = yaml.safe_load(open('ai_investor_bootstrap/reason_codes.yaml', encoding='utf-8'))
   for c in codes['codes']:
       code = c['code']
       prefix = code.split('_')[0]
       if prefix == 'RISK' and len(code.split('_')) > 2:
           # Risk codes: RISK_xxx pattern
           assert re.match(r'^RISK_[A-Z0-9_]+$', code), f'Bad risk code: {code}'
       elif prefix in ('INC','CND','EXC','DGD','BLK'):
           assert re.match(rf'^{prefix}_[A-Z0-9_]+$', code), f'Bad code: {code}'
   print('OK: all codes match naming convention')
   "
   ```

### 人工验证

- 请用户审阅 AGENT.md 重写后的完整结构是否覆盖所有需求
- 请用户确认 `total_score: null` 在 blocked 场景下是否符合下游预期
