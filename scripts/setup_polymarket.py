#!/usr/bin/env python3
"""Onboarding helper for the Polymarket (market) source.

Three jobs, so the skill can guide a first-time user end to end:

  1. --probe   : detect the environment and recommend a setup path. Prints a
                 JSON verdict the agent can branch on.
  2. --apply   : write/merge polymarket.config.json (proxy / gamma_base /
                 clob_base). Lets the agent persist config without the user
                 hand-editing JSON.
  3. --verify  : test that the currently-configured path can actually reach
                 Polymarket (or the relay). Prints JSON {ok: bool, ...}.

All stdlib, no deps. stdout is always a single JSON object (agent-parseable);
human-readable hints live in the "message" field and on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

GAMMA_DEFAULT = "https://gamma-api.polymarket.com"
CLOB_DEFAULT = "https://clob.polymarket.com"
CLASH_DEFAULT = "http://127.0.0.1:7897"
PROBE_TIMEOUT = 7

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "polymarket.config.json")


def _direct_alias(value: str) -> bool:
    return str(value).strip().lower() in {"direct", "none", "off", ""}


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] reading config failed: {exc}", file=sys.stderr)
        return {}


def _can_get(url: str, proxy: str | None, timeout: int = PROBE_TIMEOUT) -> bool:
    """True only if a GET actually succeeds (2xx) — i.e. we can pull data.

    A geo-block / GFW captive response (403/451/timeout/reset) counts as NOT
    usable, which is what we care about for the market source.
    """
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": "yucedi-setup", "Accept": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp.read(64)  # touch the body
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 - HTTPError(403/451...), URLError, timeout, refused
        return False


def _probe_endpoint(gamma_base: str, proxy: str | None) -> bool:
    return _can_get(f"{gamma_base.rstrip('/')}/markets?limit=1", proxy)


def cmd_probe() -> dict:
    cfg = _load_config()
    cfg_proxy = cfg.get("proxy")
    cfg_gamma = (cfg.get("gamma_base") or "").strip()
    has_relay = bool(cfg_gamma)

    checks: dict = {
        "config_present": os.path.exists(_CONFIG_PATH),
        "config_proxy": cfg_proxy,
        "config_gamma_base": cfg_gamma or None,
        "relay_ok": None,
        "direct_ok": None,
        "clash_ok": None,
    }

    # If a relay is configured, test it first.
    if has_relay:
        relay_proxy = None if (cfg_proxy is None or _direct_alias(cfg_proxy)) else cfg_proxy
        checks["relay_ok"] = _probe_endpoint(cfg_gamma, relay_proxy)

    # Always probe direct + Clash so the report is informative.
    checks["direct_ok"] = _probe_endpoint(GAMMA_DEFAULT, None)
    checks["clash_ok"] = _probe_endpoint(GAMMA_DEFAULT, CLASH_DEFAULT)

    # Decide verdict + recommendation.
    if has_relay and checks["relay_ok"]:
        verdict = "relay_ok"
        rec = None
        msg = "已配置 relay 且可用，market 源可正常工作。"
    elif has_relay and not checks["relay_ok"]:
        verdict = "relay_broken"
        rec = None
        msg = "已配置 relay 但连不上，请检查 relay 是否在线（访问 <relay>/healthz）或地址是否正确。"
    elif checks["direct_ok"]:
        verdict = "direct"
        rec = {"proxy": "direct"}
        msg = "本机可直连 Polymarket，建议配置 proxy=direct。"
    elif checks["clash_ok"]:
        verdict = "clash"
        rec = {"proxy": CLASH_DEFAULT}
        msg = "检测到本机 Clash(127.0.0.1:7897) 可达 Polymarket，使用默认代理即可。"
    else:
        verdict = "relay_needed"
        rec = None
        msg = ("既不能直连 Polymarket，本机也没有可用的 Clash 代理（疑似国内无出口环境）。"
               "需要在墙外架一个 relay 中转；引导见 relay/README.md。"
               "若不想架 relay，market 源会自动跳过，预测仍用其余四源融合。")

    return {
        "action": "probe",
        "verdict": verdict,
        "checks": checks,
        "recommended_config": rec,
        "guide": {
            "direct": "direct",
            "clash": "clash",
            "relay_needed": "relay",
            "relay_broken": "relay",
            "relay_ok": None,
        }[verdict],
        "config_path": _CONFIG_PATH,
        "message": msg,
    }


def cmd_apply(proxy, gamma_base, clob_base) -> dict:
    cfg = _load_config()
    changed = {}
    if proxy is not None:
        cfg["proxy"] = proxy
        changed["proxy"] = proxy
    if gamma_base is not None:
        cfg["gamma_base"] = gamma_base
        changed["gamma_base"] = gamma_base
    if clob_base is not None:
        cfg["clob_base"] = clob_base
        changed["clob_base"] = clob_base
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return {"action": "apply", "ok": False, "error": str(exc), "config_path": _CONFIG_PATH}
    return {
        "action": "apply",
        "ok": True,
        "changed": changed,
        "config": cfg,
        "config_path": _CONFIG_PATH,
        "message": f"已写入 {_CONFIG_PATH}。建议接着跑 --verify 确认连通。",
    }


def cmd_verify() -> dict:
    cfg = _load_config()
    gamma = (cfg.get("gamma_base") or "").strip() or GAMMA_DEFAULT
    proxy_cfg = cfg.get("proxy")
    proxy = None if (proxy_cfg is None or _direct_alias(proxy_cfg)) else proxy_cfg
    ok = _probe_endpoint(gamma, proxy)
    return {
        "action": "verify",
        "ok": ok,
        "gamma_base": gamma,
        "proxy": proxy or "direct",
        "message": ("连通正常，market 源可用 ✅" if ok else
                    "连不上 ❌：检查 relay 是否在线、地址/代理是否正确，或改用其它方案。"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Onboarding/setup for the Polymarket market source")
    p.add_argument("--probe", action="store_true", help="Detect environment and recommend a setup path")
    p.add_argument("--apply", action="store_true", help="Write polymarket.config.json from the flags below")
    p.add_argument("--verify", action="store_true", help="Test that the configured path reaches Polymarket")
    p.add_argument("--proxy", help="Proxy URL or 'direct' (with --apply)")
    p.add_argument("--gamma-base", help="Gamma API base / relay URL (with --apply)")
    p.add_argument("--clob-base", help="CLOB API base / relay URL (with --apply)")
    args = p.parse_args()

    if args.apply or args.proxy is not None or args.gamma_base is not None or args.clob_base is not None:
        result = cmd_apply(args.proxy, args.gamma_base, args.clob_base)
        if result.get("ok") and not args.probe:
            result["verify"] = cmd_verify()
    elif args.verify:
        result = cmd_verify()
    else:
        result = cmd_probe()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
