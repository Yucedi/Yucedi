# 让国内用户也能取到 Polymarket 数据（relay 中转）

国内连不上 Polymarket，**不是因为它封中国 IP**（它的只读 API 全球可读），而是 **GFW 挡住了
直连 `gamma-api.polymarket.com`**。解决办法：在**墙外**架一个极小的只读中转（relay），国内用户
连这个 relay，relay 替他们去取 Polymarket 再把 JSON 原样返回。

你只需架 **一个** relay，所有国内用户在配置里把 API 地址指过去即可，无需各自翻墙。

## 方案 A：Cloudflare Worker（免费、免服务器，推荐先试）

1. 登录 https://dash.cloudflare.com → Workers & Pages → Create → Worker。
2. 把 `cloudflare-worker.js` 全文粘进去，Deploy。会得到 `https://<名字>.<账号>.workers.dev`。
3. （可选但建议）给 Worker 绑一个**自定义域名**，国内可达性比 `*.workers.dev` 更稳。
4. 国内用户在 `scripts/polymarket.config.json` 填：
   ```json
   {
     "proxy": "direct",
     "gamma_base": "https://<名字>.<账号>.workers.dev/gamma",
     "clob_base":  "https://<名字>.<账号>.workers.dev/clob"
   }
   ```

> 注意：`*.workers.dev` 在国内时通时不通，绑自定义域名（且套 Cloudflare）会好很多。

## 方案 B：海外/香港 VPS（最稳，推荐生产用）

在一台**能连 Polymarket、且国内能访问**的服务器上（香港/新加坡/日本 VPS、腾讯轻量国际版等）运行：

```bash
python3 vps-relay.py 0.0.0.0 8787
# 生产环境建议用 nginx/caddy 套 HTTPS；可选 --key 加一道简单鉴权
python3 vps-relay.py 0.0.0.0 8787 --key 你的密钥
```

国内用户在 `scripts/polymarket.config.json` 填：
```json
{
  "proxy": "direct",
  "gamma_base": "http://你的VPS地址:8787/gamma",
  "clob_base":  "http://你的VPS地址:8787/clob"
}
```

健康检查：浏览器/curl 访问 `http://你的VPS地址:8787/healthz` 应返回 `ok`。

## 也可以用命令行/环境变量（不改配置文件）

```bash
python scripts/fetch_polymarket.py --query "France Senegal" \
  --outcome-a FRA_win --outcome-b SEN_win --label-a France --label-b Senegal \
  --proxy direct \
  --gamma-base https://你的relay/gamma --clob-base https://你的relay/clob
```
或设环境变量 `POLYMARKET_GAMMA_BASE` / `POLYMARKET_CLOB_BASE`（以及 `POLYMARKET_PROXY`）。

## 优先级

`--gamma-base/--clob-base 参数` > 环境变量 > `polymarket.config.json` > 内置默认（直连官方）。
代理同理：`--proxy` > 环境变量 > 配置文件 > 内置默认（Clash 127.0.0.1:7897）。

## 安全与成本

- relay 是**只读**的，只转发 GET，不涉及下单/私钥。
- Cloudflare Worker 免费额度对个人用量绰绰有余；VPS 版几乎零负载。
- 想防滥用：Worker 绑私有域名或自行加鉴权；VPS 版用 `--key` + 防火墙限源 IP。
- 实在不想架 relay 也没关系：market 源会返回 `found:false`，Skill 自动跳过、用其余四源融合。
