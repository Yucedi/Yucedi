# 首次使用引导：自动识别环境并配置 Polymarket（market 源）

> 本文件是**给 agent 执行的剧本**。目标：用户第一次用本 skill 时，自动探测部署环境，
> 推送对应方案；国内需要 relay 的，一步步引导拿地址、写配置、验证。

## 何时触发本流程

满足任一条件时，agent 应在做预测**之前**先跑一次引导：

- `scripts/polymarket.config.json` **不存在**（典型的首次安装）；或
- 用户明确说「配置 / 初始化 / 联调 Polymarket / market 源」；或
- 某次预测里 market 源返回 `found:false` 且 `reason` 含「网络/代理/超时」。

> 若用户只是想快速做一次预测、且不在意市场源，可跳过：market 源会自动 `found:false`，
> 用其余四源融合。引导不是强制项。

## 第 0 步：探测环境（务必先跑）

```bash
python scripts/setup_polymarket.py --probe
```

输出是一个 JSON，读 `verdict` 字段分支。可能值：`direct` / `clash` / `relay_ok` /
`relay_broken` / `relay_needed`。下面按 verdict 走。

---

## 分支 A — `verdict = "clash"`（本机 Clash 可达，多为作者本机）

直接告诉用户：「检测到本机 Clash(127.0.0.1:7897) 可连 Polymarket，**无需任何配置**，
默认即可。」不用写配置文件。可选地跑一次验证：

```bash
python scripts/setup_polymarket.py --verify
```

`ok:true` → 完成。

## 分支 B — `verdict = "direct"`（本机/服务器可直连，多为海外）

写入 `proxy=direct` 并验证：

```bash
python scripts/setup_polymarket.py --apply --proxy direct
```

它会自动接着 `--verify`。`ok:true` → 告诉用户「环境可直连，已配置完成」。

## 分支 C — `verdict = "relay_ok"`

已经配过 relay 且连通。告诉用户「已配置且可用，无需操作」。

## 分支 D — `verdict = "relay_broken"`

已配 relay 但连不上。让用户检查 relay 是否在线（浏览器访问 `<relay>/healthz` 应返回
`ok`）、地址是否填错。修好后重新 `--verify`。需要换方案就转入分支 E。

---

## 分支 E — `verdict = "relay_needed"`（疑似国内无出口，重点引导）

先把现状和选择讲清楚，再按用户选择走子流程：

> 「你的环境直连不到 Polymarket、本机也没有可用代理（常见于国内服务器）。要让 market 源
> 工作，需要在**墙外**架一个只读中转(relay)，你来连它、它替你取数据。两种方式：
> **① Cloudflare Worker**（免费、免服务器，5 分钟）；**② 香港/海外 VPS**（最稳，适合生产）。
> 也可以**先不配**——market 源会自动跳过，预测仍用其余四个数据源，完全能用。
> 你想用哪种？(Cloudflare / VPS / 先不配)」

用 `ask_user_input` 之类给出三个选项：`Cloudflare`、`VPS`、`暂不配置`。

### E-1：用户选 Cloudflare

逐条引导（每完成一步再给下一步，不要一次倒完）：

1. 打开 https://dash.cloudflare.com ，登录（没账号就注册，免费）。
2. 左侧 **Workers & Pages** → **Create** → **Create Worker** → 起个名字 → **Deploy**。
3. 进入该 Worker → **Edit code**，把编辑器里原有内容**全删**，粘贴
   `relay/cloudflare-worker.js` 的**全文**（agent 可直接把该文件内容贴给用户）→ **Deploy**。
4. 部署后顶部会显示访问地址，形如 `https://<名字>.<账号>.workers.dev`。让用户把这个地址发给你。
5. 拿到地址后，agent 执行（把 `<URL>` 换成用户给的地址）：
   ```bash
   python scripts/setup_polymarket.py --apply --proxy direct \
     --gamma-base <URL>/gamma --clob-base <URL>/clob
   ```
   它会自动验证。`verify.ok:true` → 成功；`false` → 见下方排错。

> 提醒用户：`*.workers.dev` 在国内**时通时不通**。如果验证失败或后续不稳，建议在 Worker 的
> **Settings → Domains & Routes** 绑一个**自定义域名**（套 Cloudflare），稳定很多；换地址后
> 重新 `--apply` 即可。

### E-2：用户选 VPS

1. 准备一台**能连 Polymarket、且国内能访问**的服务器（香港/新加坡/日本 VPS、腾讯轻量国际版等）。
2. 把 `relay/vps-relay.py` 上传到服务器，运行：
   ```bash
   python3 vps-relay.py 0.0.0.0 8787          # 生产建议套 nginx/caddy 上 HTTPS
   # 可选加鉴权：python3 vps-relay.py 0.0.0.0 8787 --key 你的密钥
   ```
3. 放行安全组/防火墙的 8787 端口。让用户浏览器访问 `http://<服务器IP>:8787/healthz`，
   返回 `ok` 表示 relay 正常；把 `http://<服务器IP>:8787` 这个 base 发给你。
4. agent 执行（`<BASE>` = 上一步地址）：
   ```bash
   python scripts/setup_polymarket.py --apply --proxy direct \
     --gamma-base <BASE>/gamma --clob-base <BASE>/clob
   ```
   自动验证。`ok:true` → 成功。

### E-3：用户选「暂不配置」

确认即可：「好的，market 源会自动跳过，预测用其余四源(odds/elo/historical/sentiment)融合。
之后想配随时跟我说『配置 Polymarket』。」不写任何配置。

---

## 验证失败（`verify.ok = false`）排错顺序

1. relay 是否在线：浏览器访问 `<relay>/healthz` 应返回 `ok`；不行就是 relay 没起来/端口没放行。
2. 地址是否填错：必须带 `/gamma`、`/clob` 后缀；VPS 注意 `http/https` 与端口。
3. Cloudflare 用 `*.workers.dev` 不稳 → 绑自定义域名后重配。
4. 仍不行：让用户跑 `python scripts/setup_polymarket.py --verify` 看输出，或暂用「先不配」跑四源。

## 成功后

告诉用户配置已写入 `scripts/polymarket.config.json`，以后做预测会自动经 relay 取 Polymarket，
market 源即可正常参与融合。无需每次带参数。
