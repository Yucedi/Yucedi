# 数据采集指南

Agent 并行采集以下五类信号。找不到就跳过，记入 `sources_missing`。

## 通用规则

- 搜索时使用英文全名 + 比赛日期，命中率更高
- 每条 Signal 必须含 `source`、`probabilities`、`note`（注明 URL 或来源与日期）
- `probabilities` 的 key 必须与 Event 的 `outcomes` 一致
- 所有概率之和 ≈ 1.0

---

## 1. 博彩赔率（odds）

**去哪找：**

- 搜索：`"{Team A} vs {Team B} odds"` 或 `"{match} betting odds"`
- 常用公开页：Oddschecker、ESPN BET line、Action Network

**怎么提取：**

1. 取 moneyline（独赢盘），优先 Pinnacle / Bet365 / 中位数
2. 将原始赔率交给脚本：

```bash
python scripts/normalize_odds.py --input-file odds_input.json
```

`odds_input.json`：`{"outcomes":[{"outcome":"LAL_win","format":"decimal","odds":2.15},{"outcome":"BOS_win","format":"decimal","odds":1.72}]}`

支持 `format`：`decimal` | `american` | `fractional`（如 `"5/2"`）

3. 用脚本输出的 `probabilities` 填入 Signal

**Signal 示例：**

```json
{
  "source": "odds",
  "probabilities": {"LAL_win": 0.44, "BOS_win": 0.56},
  "note": "Oddschecker moneyline median, 2026-06-15"
}
```

---

## 2. Prediction Markets（market）

> ⚠️ **不要靠网页搜索抓 Polymarket。** 网页搜索几乎搜不到具体某场比赛的实时
> Yes 报价，且 polymarket.com 是 JS 渲染、`web_fetch` 拿不到价格。
> **必须**调用脚本，直连 Polymarket 公开只读 API（Gamma + CLOB，无需 key）。

**怎么提取（脚本，首选）：**

```bash
python scripts/fetch_polymarket.py \
  --query "Spain Cape Verde World Cup" \
  --outcome-a ESP_win --outcome-b CPV_win \
  --label-a "Spain" --label-b "Cape Verde" \
  --use-clob
```

参数说明：

- `--query`：英文队名 + 赛事关键词，命中率最高
- `--outcome-a/-b`：本 Skill 的 outcome key（与 Event JSON 一致）
- `--label-a/-b`：Polymarket 一侧的队名，用于把市场结果正确映射到 A/B
- `--yes-is a|b`：**仅当**市场是 Yes/No 二元盘时给出（Yes 代表哪支队）
- `--use-clob`：用 CLOB 订单簿中价精修报价（更接近实时），可选
- 代理：**默认自动走本地 Clash `http://127.0.0.1:7897`**（Polymarket 有地域限制），
  无需手动指定；Clash 没开会自动回退直连。`--proxy URL` 覆盖、`--no-proxy` 强制直连、
  `--timeout 秒` 调每次请求超时（默认 25s）。也可用环境变量 `POLYMARKET_PROXY` / `HTTPS_PROXY`

> ⚠️ **「API 超时」排查**：若 market 源报超时，几乎都是**代理没生效**——确认 Clash 在
> 127.0.0.1:7897 运行（Global 模式）。脚本现在默认就用这个代理，所以 agent 调用时
> 不必再加 `--proxy`。仍超时则加 `--debug` 看 `[debug] proxy chain` 与 `[warn] request via ...`。

**多环境部署（重要）：**

默认代理 `127.0.0.1:7897` 只适合「本机装了 Clash」的环境。**部署到国内服务器或其它机器时**，
按下面之一配置（优先级从高到低）：

1. 命令行：`--proxy http://你的代理:端口`，或 `--proxy direct`（直连），或 `--no-proxy`
2. 环境变量：`POLYMARKET_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY`
3. 配置文件：把 `scripts/polymarket.config.example.json` 复制为 `scripts/polymarket.config.json`，
   填 `"proxy"`（代理地址，或 `"direct"`）。**每台机器配一次即可，agent 无需改命令。**
4. 都没配 → 用内置默认 Clash，连不上再自动回退直连

各环境对照：

| 部署环境 | 配置 |
|---|---|
| 本机 + Clash | 不用配，默认即可（或配置文件填 `127.0.0.1:7897`） |
| 服务器有自有出口代理 | 配置文件/环境变量填该代理地址 |
| 海外服务器可直连 Polymarket | 配 `"direct"` |
| 国内、无出口 | 配 `"direct"` 或不管它 → market 源自动 `found:false` 跳过 |

> 💡 **market 源是可选的**：连不上 Polymarket 时脚本返回 `found:false`，Skill 会把它记入
> `sources_missing`、用其余四源（odds/elo/historical/sentiment）正常融合。所以国内无代理的
> 部署**不会报错，只是跑「四源预测」**，与有 Polymarket 时的差别仅是少一个市场信号。

**脚本行为：**

1. 多查询召回：用 `--query` + 纯队名 `"A B"` / `"A vs B"` 各搜一次合并去重
   （避免被「World Cup」带偏到一堆夺冠期货盘）
2. **识别足球比赛的三盘结构**：Polymarket 把一场球拆成同一 event 下三个独立 Yes/No 盘
   （`Will A win on 日期?` / `... end in a draw?` / `Will B win on 日期?`），脚本自动合成为
   一场「胜/平/负」，再丢掉平局、把两队归一成二元（即 `P(胜 | 不平局)`，与其它源口径一致）
3. **硬性要求市场同时提及两支队**，剔除「某队夺冠/出线/小组第一/最佳射手」等只提一队的期货盘
4. **陈旧/已结算检测**：比赛日期已过、或平局 Yes > 0.9、或两队胜率均≈0 → 判定盘已结算/未开盘，
   **跳过**并在 `reason` 说明（这对已开赛/已结束的比赛是正确行为，不是 bug）
5. 低流动性（vol < $10k）在 `note` 标注 ⚠️
6. **找不到可用对阵盘** → 输出 `{"found": false, "reason": "..."}`，跳过本源、记入
   `sources_missing`，**不要捏造**

> ⚠️ **重要现实**：Polymarket 世界杯**小组赛单场对阵盘可能存在、也可能没有**；即便有，已开赛/已结束的
> 盘价格已结算，不能当赛前预测。`found:false` 在这些情况下是**正确结果**。用 `--debug` 可看到
> API 返回的原始盘（含 `mention BOTH teams: N` 统计与合成结果）。

**脚本输出（直接就是 Signal）：**

```json
{
  "source": "market",
  "probabilities": {"ESP_win": 0.86, "CPV_win": 0.14},
  "note": "Polymarket CLOB midpoint: \"Will Spain beat Cape Verde?\" vol=$142,000 liq=$38,000",
  "found": true
}
```

把 `found:true` 的输出去掉 `found` 字段后直接放进 `signals.json` 的 `signals` 数组即可。

> 离线自检（不联网，验证脚本本身没坏）：
> `python scripts/fetch_polymarket.py --self-test`

Kalshi 暂无脚本，若需要可手动按上面的 Yes 价格 → 概率方式补充，并在 `note` 注明来源。

---

## 3. Elo 评分（elo）

**去哪找：**

- 足球：clubelo.com
- NBA：搜索 `"{team} elo rating"` 或 FiveThirtyEight RAPTOR/Elo（历史）
- 通用：搜索 `"{Team A} {Team B} elo rating"`

**怎么提取：**

1. 获取双方 Elo 分
2. 确认主队，传入脚本：

```bash
python scripts/elo_expected.py --input-file elo_input.json
```

`elo_input.json`：`{"outcome_a":"LAL_win","outcome_b":"BOS_win","rating_a":1580,"rating_b":1620,"home_outcome":"LAL_win","home_advantage":100}`

3. 非 NBA 赛事可将 `home_advantage` 设为 0 或运动默认值（足球 ~65，NBA ~100）

**Signal 示例：**

```json
{
  "source": "elo",
  "probabilities": {"LAL_win": 0.41, "BOS_win": 0.59},
  "note": "ClubElo LAL=1580 BOS=1620, home=LAL, 2026-06-15"
}
```

---

## 4. 历史统计（historical）

**去哪找：**

- 搜索：`"{Team A} vs {Team B} head to head last 5 games"`
- 搜索：`"{Team A} last 5 games results"`
- 来源：ESPN、Flashscore、Basketball-Reference、Transfermarkt

**怎么提取：**

1. 收集：H2H 近 N 场胜率、双方近 5–10 场胜率、主客场
2. 合成简单胜率估计（Agent 计算，公式见 SKILL.md）
3. 映射为 `probabilities`

**推荐公式：**

```
form_a = 近10场胜场 / 10
form_b = 近10场胜场 / 10
h2h_a = H2H中A胜 / H2H总场次   （无 H2H 则省略）
raw_a = 0.6 * form_a + 0.4 * h2h_a   （无 H2H 则 raw_a = form_a）
raw_b = 0.6 * form_b + 0.4 * (1 - h2h_a)
归一化 → probabilities
```

**Signal 示例：**

```json
{
  "source": "historical",
  "probabilities": {"LAL_win": 0.55, "BOS_win": 0.45},
  "note": "LAL 7/10 recent, BOS 6/10, H2H 3-2 LAL, ESPN 2026-06-15"
}
```

---

## 5. 新闻情绪（sentiment）

**去哪找：**

- 搜索：`"{Team A} {Team B} injury news"` / `"{team} injury report"`
- 搜索：`"{event} preview"` 或 `"{team} news last week"`
- 时间窗：比赛前 7 天内

**怎么提取：**

1. 阅读 3–5 条相关新闻标题/摘要
2. 对「有利于 outcome A 胜」的方向打 sentiment：+1（极利好）~ -1（极利空）
3. 取平均 `avg_sentiment`
4. 映射为概率（公式见 SKILL.md § Sentiment）

**Signal 示例：**

```json
{
  "source": "sentiment",
  "probabilities": {"LAL_win": 0.48, "BOS_win": 0.52},
  "note": "LAL 主力缺阵(-0.3), BOS 全员健康(+0.2), avg=-0.05, 3 articles"
}
```

---

## signals.json 格式

采集完成后写入一个文件供融合脚本使用：

```json
{
  "signals": [
    {"source": "odds", "probabilities": {"LAL_win": 0.44, "BOS_win": 0.56}, "note": "..."},
    {"source": "elo", "probabilities": {"LAL_win": 0.41, "BOS_win": 0.59}, "note": "..."}
  ]
}
```
