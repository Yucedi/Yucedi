---
name: yucedi
description: >-
  预测帝 — multi-source event prediction: odds, prediction markets, Elo,
  historical stats, news sentiment, weighted fusion, and narrative report.
  Use when the user asks who will win, outcome probabilities, or forecast
  analysis for sports or other events. Trigger terms: 预测帝, 预测, 胜率, 谁赢.
---

# 预测帝

对用户预测类问题执行固定四阶段流程。**零配置**，全部内置。

```
用户问题 → 事件识别 → 数据采集 → 预测引擎 → LLM 解释
```

## 首次使用：环境自检与引导（仅 Polymarket 源需要）

四个数据源开箱即用、无需配置。**只有 `market`（Polymarket）源**因有地域限制，可能需要一次性配置。

触发引导的时机（满足任一）：`scripts/polymarket.config.json` 不存在（首次安装）；用户说要
「配置/初始化 Polymarket」；或某次预测里 market 源因网络/代理/超时返回 `found:false`。

此时**先跑探测，再按结果引导**：

```bash
python scripts/setup_polymarket.py --probe
```

读返回 JSON 的 `verdict`，按 [onboarding.md](onboarding.md) 的对应分支执行：

- `clash` / `direct` / `relay_ok` → 一条命令搞定或无需配置，照剧本告知用户即可。
- `relay_needed`（疑似国内无出口）→ 按 onboarding.md 分支 E **一步步引导**用户用
  Cloudflare Worker 或 VPS 架 relay，拿到地址后用
  `python scripts/setup_polymarket.py --apply ...` 自动写配置并验证。

> 用户不想配也没关系：market 源自动跳过，预测用其余四源融合。引导非强制。
> 详细剧本（含 relay 获取教程、排错）见 [onboarding.md](onboarding.md)。

## Quick Start Checklist

复制并跟踪进度：

```
Prediction Task:
- [ ] Step 1: 解析事件 → Event JSON
- [ ] Step 2: 并行采集五类信号 → signals.json
- [ ] Step 3: 运行 fuse_signals.py → result.json
- [ ] Step 4: 按 output-template 撰写报告
- [ ] Step 5: 自检（概率一致、有溯源、有免责声明）
```

## Step 1 — 事件识别

判断是否为预测问题（谁会赢、概率多少、能否发生）。**不是则退出本 Skill。**

从用户问题提取并输出 Event JSON：

```json
{
  "question": "用户原问题",
  "domain": "sports",
  "teams": ["Team A 全名", "Team B 全名"],
  "outcomes": ["A_win", "B_win"],
  "event_time": "ISO8601 或 null",
  "confidence": 0.9
}
```

规则：

- `outcomes` 用简短 key（如 `LAL_win`），全文报告再写可读名称
- `confidence < 0.7` → **停止**，向用户澄清（一次一问）：哪场比赛、什么时间
- v1 聚焦 **体育 binary 胜负**

## Step 2 — 数据采集

阅读 [references/data-sources.md](references/data-sources.md)，**并行**采集五类信号：

| 源 | source 值 |
|----|-----------|
| 博彩赔率 | `odds` |
| Prediction Markets | `market` |
| Elo | `elo` |
| 历史统计 | `historical` |
| 新闻情绪 | `sentiment` |

采集 Checklist：

```
- [ ] odds
- [ ] market
- [ ] elo
- [ ] historical
- [ ] sentiment
```

### 脚本调用

**赔率归一化：**

```bash
python scripts/normalize_odds.py --input-file odds_input.json
```

`odds_input.json` 示例：`{"outcomes":[{"outcome":"A_win","format":"decimal","odds":2.1},{"outcome":"B_win","format":"decimal","odds":1.8}]}`

**Elo 胜率：**

```bash
python scripts/elo_expected.py --input-file elo_input.json
```

`elo_input.json` 示例：`{"outcome_a":"A_win","outcome_b":"B_win","rating_a":1580,"rating_b":1620,"home_outcome":"A_win","home_advantage":100}`

**Prediction Market（Polymarket，必须用脚本，禁止网页搜索）：**

```bash
python scripts/fetch_polymarket.py --query "Team A Team B 赛事" \
  --outcome-a A_win --outcome-b B_win --label-a "Team A" --label-b "Team B" --use-clob
```

脚本**默认就走本地 Clash 代理 `http://127.0.0.1:7897`**（Polymarket 有地域限制），
agent 无需再手动加 `--proxy`；Clash 没开时会自动回退直连。需要时可用 `--proxy URL`
覆盖、`--no-proxy` 强制直连、`--timeout 秒` 调超时。
**非本机/国内服务器部署**：把 `scripts/polymarket.config.example.json` 复制为
`polymarket.config.json` 填自有代理或 `"direct"`（每台机器配一次）；连不上则本源自动
`found:false` 跳过、用其余四源融合。
输出 `found:true` 即为可用 Signal；`found:false` 则记入 `sources_missing`，不捏造。
细节见 [references/data-sources.md](references/data-sources.md) §2。

脚本路径相对于本 Skill 根目录 `yucedi/`。

### 历史统计（Agent 计算）

```
form_a = 近10场胜场 / 10
form_b = 近10场胜场 / 10
h2h_a  = H2H 中 A 胜 / H2H 总场（无则省略 h2h 项）
raw_a  = 0.6 * form_a + 0.4 * h2h_a   （无 H2H → raw_a = form_a）
raw_b  = 0.6 * form_b + 0.4 * (1 - h2h_a)
prob_a = raw_a / (raw_a + raw_b)
```

### 新闻情绪（Agent 计算）

1. 搜索赛前 7 天内 3–5 条新闻
2. 对「A 获胜」方向逐条打分：+1（极利好 A）~ -1（极利空 A）
3. `avg_sentiment = 平均分`
4. 映射概率：

```
bias = avg_sentiment * 0.05
prob_a = 0.5 + bias
prob_b = 1.0 - prob_a
```

### 采集规则

- 找不到某源 → 跳过，不捏造
- **至少 1 个**信号成功才能继续；0 个则停止并说明
- 写入 `signals.json`：

```json
{
  "signals": [
    {"source": "odds", "probabilities": {"A_win": 0.44, "B_win": 0.56}, "note": "来源与日期"}
  ]
}
```

## Step 3 — 预测引擎

**禁止 LLM 自行估算融合概率**，必须运行脚本：

```bash
python scripts/fuse_signals.py --input signals.json --output result.json
```

v1 固定权重（缺失源自动剔除并归一化）：

| 来源 | 权重 |
|------|------|
| odds | 0.30 |
| market | 0.10 |
| elo | 0.25 |
| historical | 0.20 |
| sentiment | 0.15 |

## Step 4 — LLM 解释

按 [references/output-template.md](references/output-template.md) 撰写报告。

硬性要求：

- 融合概率 **必须**与 `result.json` 完全一致
- 各源概率 **必须**与 `signals.json` 一致
- 列出 `sources_missing` 及原因
- 1 个源 → 标注「低置信」；2 个 →「中」；3+ →「高」
- 末尾加免责声明
- **禁止**下注/投资建议

## 错误处理

| 情况 | 动作 |
|------|------|
| 事件歧义 | 澄清，不继续 |
| 0 个信号 | 停止，说明缺什么 |
| 1 个信号 | 可继续，标注低置信 |
| 数据过旧 | 可用，报告中警告 |
| 源间分歧大（差 >10%） | 在「分歧与风险」中说明 |

## 置信度规则

| 成功源数量 | 标签 |
|-----------|------|
| 1 | 低 |
| 2 | 中 |
| 3+ | 高 |

## 附加资源

- 数据采集细节：[references/data-sources.md](references/data-sources.md)
- 报告模板：[references/output-template.md](references/output-template.md)
- 完整示例：[examples.md](examples.md)
