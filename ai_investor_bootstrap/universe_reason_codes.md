# universe_reason_codes.md

这份文件用于定义**稳定、可审计、可版本化**的 reason code。首版虽然文件名叫 `universe_reason_codes`，但为了减少早期分散维护，先把与 universe 直接相关的**纳入 / 排除 / 降级 / 阻断**原因码放在一起；风险叠加码也放在附录，供 `score_result.schema.json` 对齐使用。

---

## 1. 设计原则

### 1.1 命名规则

- `INC_*`：纳入原因
- `CND_*`：候选来源原因
- `EXC_*`：排除原因
- `DGD_*`：降级原因
- `BLK_*`：阻断原因
- `RISK_*`：风险叠加码

### 1.2 使用原则

1. **code 稳定，文案可变**：一旦上线，code 不应复用。  
2. **machine first**：程序内使用 code，不直接依赖中文文案。  
3. **一条规则对应一个主要 code**：不要把多个不同逻辑揉成一个码。  
4. **允许多码并存**：同一只股票可以同时命中多个纳入或排除原因。  
5. **reason code 进入 audit**：每次输出都应记录命中的原因码集合。  
6. **`inclusion_reasons` 语义约定**：`universe_membership.inclusion_reasons` 只放**来源码**（如 `INC_FULL_MARKET_SEED`、`INC_USER_WATCHLIST`）和**决定性通过码**（如 `INC_BOARD_ALLOWED`、`INC_LIQUIDITY_PASS`）；完整的命中集合（包括所有通过的细粒度码）进入 `audit.applied_reason_codes`。这样用户侧看到的是简洁的纳入原因，而审计侧保有完整轨迹。

### 1.3 推荐存储位置

- universe builder 输出：`inclusion_reasons`, `exclusion_reasons`
- score result 输出：`state_reason_codes`, `audit.applied_reason_codes`
- audit log：完整保存 `applied_reason_codes`

### 1.4 生命周期管理

原因码一旦上线，不得复用、不得修改语义。如业务规则变更，应新增 code 并废弃旧 code。

每个 code 应在机器可读注册表（`universe_reason_codes.yaml`）中包含以下字段：

| 字段 | 说明 |
|------|------|
| `status` | `active` 或 `deprecated` |
| `introduced_in` | 引入时的 strategy_version |
| `deprecated_in` | 废弃时的 strategy_version（可空） |
| `replaced_by` | 替代 code（可空） |

规则：
1. 新输出不得产生 `deprecated` 状态的 code。
2. 历史快照中包含 deprecated code 仍可解析。
3. 如有 `replaced_by`，必须指向一个 `active` code。

---

## 2. 纳入原因码（INC / CND）

| code | 类别 | 中文含义 | 触发规则 | 备注 |
|---|---|---|---|---|
| INC_FULL_MARKET_SEED | 纳入 | 来自全市场基础股票池 | 当日基础 universe 生成时纳入 | 最基础的 seed |
| INC_USER_WATCHLIST | 纳入 | 来自用户观察池 | 用户显式加入观察列表 | 可与其他纳入码同时出现 |
| INC_THEME_SLICE | 纳入 | 来自主题切片 | 命中主题映射后的候选池 | 例如“算力”“机器人” |
| CND_SIGNAL_CANDIDATE | 候选 | 来自外部关注信号 | 由新闻/舆情/政策热度推入候选队列 | 首版建议不直接提升主分 |
| INC_SECURITY_TYPE_COMMON_STOCK | 纳入 | 证券类型通过 | 证券类型为普通 A 股 | 可选的显式记录码 |
| INC_BOARD_ALLOWED | 纳入 | 板块通过 | 板块属于允许范围 | 首版为 main / chinext |
| INC_LISTED_DAYS_PASS | 纳入 | 上市时间通过 | 上市交易日数 >= 最小阈值 | 与 IPO 冷却规则对应 |
| INC_LIQUIDITY_PASS | 纳入 | 流动性通过 | ADV20 成交额 >= 最小阈值 | 首版阈值 1 亿元 |
| INC_FINANCIALS_READY | 纳入 | 财务数据齐备 | 最新财报关键字段齐全 | |
| INC_AUDIT_PASS | 纳入 | 审计意见通过 | 最新审计意见为标准无保留 | |
| INC_POSITIVE_EQUITY_PASS | 纳入 | 净资产通过 | 净资产为正 | |

---

## 3. 排除原因码（EXC）

| code | 中文含义 | 触发规则 | 是否通常直接排除 | 备注 |
|---|---|---|---|---|
| EXC_NOT_COMMON_STOCK | 非普通 A 股 | 证券类型不为 common_stock | 是 | 顶层过滤 |
| EXC_BOARD_NOT_ALLOWED | 板块不在允许范围 | 不属于 main / chinext | 是 | 例如 star |
| EXC_ETF | ETF 被排除 | 证券类型为 ETF | 是 | |
| EXC_REIT | REIT 被排除 | 证券类型为 REIT | 是 | |
| EXC_B_SHARE | B 股被排除 | 证券类型为 B 股 | 是 | |
| EXC_BOND_LIKE | 债性品种被排除 | 可转债/债券类 | 是 | |
| EXC_ST_OR_RISK_WARNING | ST / 风险警示被排除 | 命中 ST、*ST 或风险警示标签 | 是 | |
| EXC_DELISTING_PHASE | 退市整理期被排除 | 证券处于退市整理阶段 | 是 | |
| EXC_SUSPENDED | 停牌被排除 | 当日不可正常交易 | 是 | |
| EXC_LISTED_DAYS_LT_60 | 上市未满 60 个交易日 | 上市交易日数 < 60 | 是 | 首版冷却规则 |
| EXC_ADV20_AMOUNT_LT_100M | 流动性不足 | 20 日平均成交额 < 1 亿元 | 是 | 阈值可版本化 |
| EXC_NEGATIVE_EQUITY | 净资产为负 | 最新净资产 < 0 | 是 | |
| EXC_MISSING_LATEST_FINANCIALS | 最新财务不齐 | 关键财务字段缺失 | 是 | 可与 BLK 区分 |
| EXC_AUDIT_NON_STANDARD | 审计意见不达标 | 非标准无保留意见 | 是 | |
| EXC_POLICY_RESTRICTED | 策略政策限制 | 因策略或业务限制不允许研究 | 是 | 例如灰名单 |

---

## 4. 降级原因码（DGD）

降级的意思不是“低分”，而是：**还能输出研究结果，但必须显式标记结果质量下降**。

| code | 中文含义 | 触发规则 | 建议动作 |
|---|---|---|---|
| DGD_MISSING_NONCRITICAL_FEATURES | 缺失非关键特征 | 非关键字段缺失，但关键字段完整 | 输出结果，但降低 confidence |
| DGD_STALE_ATTENTION_SOURCE | 关注信号过期 | attention 层数据超时 | 仅影响 attention/risk flag |
| DGD_PARTIAL_VALUATION_FALLBACK | 估值指标部分回退 | PE/PB/EV/EBITDA 中部分指标不可用，采用回退逻辑 | 允许输出，但记录回退 |
| DGD_PARTIAL_INDUSTRY_MAPPING | 行业映射不完整 | 行业层级缺失，退化到更粗粒度分组 | 允许输出，但降低可比性 |
| DGD_PARTIAL_MARKET_CONFIRMATION | 市场确认项部分缺失 | 技术面某些指标不可用 | 允许输出，但降低置信度 |

---

## 5. 阻断原因码（BLK）

阻断的意思是：**不允许形成正常研究结论**。用户侧通常仍可看到一个结果对象，但 `result_state = blocked`，并伴随明确原因。

| code | 中文含义 | 触发规则 | 用户侧建议呈现 |
|---|---|---|---|
| BLK_MISSING_CRITICAL_INPUTS | 缺失关键输入 | 关键特征或关键快照缺失 | 输出“当前无法形成稳定结论” |
| BLK_POLICY_DISALLOWED | 策略/政策不允许 | 命中合规、业务、权限限制 | 输出“当前不提供该类研究结论” |
| BLK_UNIVERSE_EXCLUDED | 已被股票池排除 | 命中任何硬排除逻辑 | 输出“当前不在可研究股票池” |
| BLK_DATA_CONTRACT_BROKEN | 数据契约不完整 | PIT 必要字段缺失或 source_hash 无法确认 | 输出“数据证据不足” |
| BLK_AUDIT_TRAIL_MISSING | 审计链不完整 | 无法写入 audit log 或缺少关键版本号 | 输出“结果不可审计，已阻断” |

---

## 6. 附录：风险叠加码（RISK）

这些码不属于 universe 纳入/排除本身，但会在评分阶段形成扣分，并进入 `score_result.penalties[*].code`。

### 二级前缀命名约定

新增风险码应遵循以下二级前缀，现有 5 个码为首版命名，暂不改名：

| 前缀 | 分类 | 示例 |
|------|------|------|
| `RISK_FIN_*` | 财务风险（商誉、质押等） | `RISK_FIN_HIGH_LEVERAGE` |
| `RISK_GOV_*` | 公司治理风险 | `RISK_GOV_BOARD_CONFLICT` |
| `RISK_REG_*` | 监管/合规风险 | `RISK_REG_PENALTY` |
| `RISK_EVT_*` | 事件驱动风险（减持、诉讼等） | `RISK_EVT_LAWSUIT` |
| `RISK_MKT_*` | 市场结构风险 | `RISK_MKT_CONCENTRATION` |

| code | 中文含义 | 示例触发条件 | 默认扣分 |
|---|---|---|---:|
| RISK_GOODWILL_HIGH | 商誉占比高 | 商誉/净资产超过阈值 | -5 |
| RISK_EQUITY_PLEDGE_HIGH | 股权质押比例高 | 主要股东质押比例超过阈值 | -5 |
| RISK_REGULATORY_PROBE | 监管立案或调查 | 命中监管调查、立案等公告 | -8 |
| RISK_MAJOR_REDUCTION | 重要股东减持压力 | 命中重大减持计划或实施 | -3 |
| RISK_MATERIAL_NEGATIVE_ANNOUNCEMENT | 重大负面公告 | 诉讼、业绩爆雷、内控缺陷等 | -8 |

---

## 7. 一个最小示例

### 7.1 纳入示例

```json
{
  "included": true,
  "inclusion_reasons": [
    "INC_FULL_MARKET_SEED",
    "INC_BOARD_ALLOWED",
    "INC_LISTED_DAYS_PASS",
    "INC_LIQUIDITY_PASS",
    "INC_FINANCIALS_READY",
    "INC_AUDIT_PASS"
  ],
  "exclusion_reasons": []
}
```

### 7.2 排除示例

```json
{
  "included": false,
  "inclusion_reasons": [
    "INC_FULL_MARKET_SEED"
  ],
  "exclusion_reasons": [
    "EXC_ST_OR_RISK_WARNING",
    "EXC_ADV20_AMOUNT_LT_100M"
  ]
}
```

### 7.3 阻断示例

```json
{
  "result_state": "blocked",
  "state_reason_codes": [
    "BLK_UNIVERSE_EXCLUDED"
  ]
}
```

---

## 8. 首版维护建议

1. 新增 code 时，优先追加，不修改已有 code 语义。  
2. 若业务逻辑有重大变化，优先改 `strategy_version`，而不是复用旧 code 表示新含义。  
3. 后续如果体系变大，可以把本文件拆成：
   - `universe_reason_codes.md`
   - `state_reason_codes.md`
   - `risk_overlay_codes.md`

