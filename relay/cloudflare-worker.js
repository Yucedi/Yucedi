/**
 * Polymarket read-only relay — Cloudflare Worker (free tier, no server).
 *
 * Lets users who cannot reach Polymarket directly (e.g. mainland China) fetch
 * its public read APIs through this Worker, which is hosted on Cloudflare's
 * global edge.
 *
 * Routes:
 *   GET /gamma/<path>?<query>  ->  https://gamma-api.polymarket.com/<path>?<query>
 *   GET /clob/<path>?<query>   ->  https://clob.polymarket.com/<path>?<query>
 *
 * Deploy:
 *   1. https://dash.cloudflare.com -> Workers & Pages -> Create -> Worker
 *   2. Paste this file, Deploy. You get https://<name>.<acct>.workers.dev
 *      (For better reachability from China, bind a custom domain in Worker settings.)
 *   3. In scripts/polymarket.config.json set:
 *        "proxy": "direct",
 *        "gamma_base": "https://<name>.<acct>.workers.dev/gamma",
 *        "clob_base":  "https://<name>.<acct>.workers.dev/clob"
 *
 * Optional abuse protection: set RELAY_KEY below and add the same value to the
 * config as "relay_key" is NOT auto-sent by the script, so prefer restricting by
 * a hard-to-guess Worker subdomain/custom domain, or add your own auth here.
 */

const TARGETS = {
  "/gamma": "https://gamma-api.polymarket.com",
  "/clob": "https://clob.polymarket.com",
};

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method !== "GET") {
      return new Response("only GET", { status: 405 });
    }

    let target = null;
    for (const [prefix, base] of Object.entries(TARGETS)) {
      if (url.pathname === prefix || url.pathname.startsWith(prefix + "/")) {
        target = base + url.pathname.slice(prefix.length) + url.search;
        break;
      }
    }
    if (!target) {
      return new Response("not found (use /gamma/... or /clob/...)", { status: 404 });
    }

    try {
      const upstream = await fetch(target, {
        headers: { "User-Agent": "yucedi-relay", "Accept": "application/json" },
        cf: { cacheTtl: 15, cacheEverything: true },
      });
      const body = await upstream.arrayBuffer();
      return new Response(body, {
        status: upstream.status,
        headers: {
          "content-type": upstream.headers.get("content-type") || "application/json",
          "access-control-allow-origin": "*",
          "cache-control": "public, max-age=15",
        },
      });
    } catch (err) {
      return new Response("relay upstream error: " + err, { status: 502 });
    }
  },
};
