# 预测帝 · 示例：湖人 vs 凯尔特人

完整 walkthrough，演示预测帝全流程。数值为示例，实际使用时 Agent 应检索实时数据。

---

## 用户问题

> 湖人能赢凯尔特人吗？

---

## Step 1 — 事件识别

```json
{
  "question": "湖人能赢凯尔特人吗？",
  "domain": "sports",
  "teams": ["Los Angeles Lakers", "Boston Celtics"],
  "outcomes": ["LAL_win", "BOS_win"],
  "event_time": "2026-06-20T00:00:00Z",
  "confidence": 0.95
}
```

`confidence >= 0.7`，无需澄清，进入采集。

---

## Step 2 — 数据采集

### odds

搜索 `Lakers vs Celtics odds`，假设得到 decimal 赔率 LAL 2.15 / BOS 1.72：

```bash
python scripts/normalize_odds.py --input '{"outcomes":[{"outcome":"LAL_win","format":"decimal","odds":2.15},{"outcome":"BOS_win","format":"decimal","odds":1.72}]}'
```

输出概率 ≈ LAL 44.4%, BOS 55.6%

### elo

ClubElo 假设 LAL=1580, BOS=1620，湖人主场：

```bash
python scripts/elo_expected.py --input '{"outcome_a":"LAL_win","outcome_b":"BOS_win","rating_a":1580,"rating_b":1620,"home_outcome":"LAL_win"}'
```

输出 ≈ LAL 58.6%, BOS 41.4%

### historical

假设：LAL 近 10 场 7 胜，BOS 6 胜，H2H 近 5 场 LAL 3 胜。

```
form_a = 0.7, form_b = 0.6, h2h_a = 0.6
raw_a = 0.6*0.7 + 0.4*0.6 = 0.66
raw_b = 0.6*0.6 + 0.4*0.4 = 0.52
→ LAL 55.9%, BOS 44.1%
```

### market

调用脚本直连 Polymarket（**不再靠网页搜索**）：

```bash
python scripts/fetch_polymarket.py --query "Lakers Celtics NBA" \
  --outcome-a LAL_win --outcome-b BOS_win --label-a "Lakers" --label-b "Celtics" --use-clob
```

- 若返回 `{"found": true, ...}` → 把 Signal（去掉 `found` 字段）加入 `signals.json`
- 若返回 `{"found": false, ...}` → 跳过本源，记入 `sources_missing`

本例假设脚本返回 `found:false`（无对应市场）→ 跳过。
（被地域限制时加 `--proxy http://127.0.0.1:7897`；离线验证脚本：`--self-test`）

### sentiment

3 篇新闻：LAL 主力带伤(-0.2)，BOS 全员健康(+0.1)，neutral(0) → avg = -0.033

以 50/50 为基准，sensitivity=0.05：

```
bias = -0.033 * 0.05 = -0.00165
LAL = 0.498, BOS = 0.502
```

### signals.json

```json
{
  "signals": [
    {"source": "odds", "probabilities": {"LAL_win": 0.444, "BOS_win": 0.556}, "note": "Oddschecker median"},
    {"source": "elo", "probabilities": {"LAL_win": 0.5857, "BOS_win": 0.4143}, "note": "ClubElo, LAL home"},
    {"source": "historical", "probabilities": {"LAL_win": 0.559, "BOS_win": 0.441}, "note": "LAL 7/10, BOS 6/10, H2H 3-2"},
    {"source": "sentiment", "probabilities": {"LAL_win": 0.498, "BOS_win": 0.502}, "note": "avg sentiment -0.033"}
  ]
}
```

---

## Step 3 — 融合

```bash
python scripts/fuse_signals.py --input signals.json --output result.json
```

预期 result（4 源，market 缺失）：

```json
{
  "probabilities": {"LAL_win": 0.518, "BOS_win": 0.482},
  "sources_used": ["odds", "elo", "historical", "sentiment"],
  "sources_missing": ["market"],
  "method": "weighted_ensemble_v1",
  "weights_applied": {"odds": 0.3333, "elo": 0.2778, "historical": 0.2222, "sentiment": 0.1667}
}
```

权重说明：market 缺失，剩余 0.30+0.25+0.20+0.15=0.90 归一化。

---

## Step 4 — 报告（摘要）

```markdown
# 预测报告：湖人 vs 凯尔特人

## 结论摘要
- **预测结果**：湖人获胜
- **融合概率**：湖人 **51.8%** vs 凯尔特人 **48.2%**
- **数据基础**：4 个数据源（odds, elo, historical, sentiment）
- **置信度**：中

## 多源信号对比
| 来源 | LAL | BOS |
|------|-----|-----|
| 赔率 | 44.4% | 55.6% |
| Elo | 58.6% | 41.4% |
| 历史 | 55.9% | 44.1% |
| 情绪 | 49.8% | 50.2% |
| **融合** | **51.8%** | **48.2%** |

## 分歧与风险
- 赔率看好凯尔特人；Elo 与历史 form 看好湖人
- 缺失：Polymarket 无对应市场

---
*免责声明：仅供参考，不构成投资或博彩建议。*
```
