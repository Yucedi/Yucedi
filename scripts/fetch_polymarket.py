#!/usr/bin/env python3
"""Fetch prediction-market probabilities from Polymarket (Gamma + CLOB).

Replaces the old "搜索 polymarket 网页" approach for the `market` signal source.
Hits Polymarket's public read APIs (no API key required):

  - Gamma:  https://gamma-api.polymarket.com   (search markets, read outcomePrices)
  - CLOB:   https://clob.polymarket.com         (live order-book midpoint, optional)

Outputs a ready-to-use Signal object in the skill's signals format:

  {"source": "market", "probabilities": {...}, "note": "..."}

Network notes
-------------
* Polymarket read APIs are public, but the host network may be geo-restricted.
  Pass --proxy http://127.0.0.1:7897 (Clash) or set HTTPS_PROXY / HTTP_PROXY.
* --self-test runs the parsing/mapping logic against a bundled fixture with NO
  network, so you can verify the script works offline before going live.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
USER_AGENT = "yucedi-prediction/1.0 (+polymarket-fetch)"
TIMEOUT = 25
# Polymarket is geo-restricted; default to the local Clash proxy so the skill
# works without the caller having to remember --proxy. Override with --proxy,
# disable with --no-proxy, or set POLYMARKET_PROXY / HTTPS_PROXY.
DEFAULT_PROXY = "http://127.0.0.1:7897"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _build_opener(proxy: Optional[str]) -> urllib.request.OpenerDirector:
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})  # force direct, ignore env
    return urllib.request.build_opener(handler)


def http_get_json(
    url: str,
    proxy_chain: Optional[List[Optional[str]]] = None,
    timeout: int = TIMEOUT,
) -> object:
    """GET JSON, trying each proxy option in order until one succeeds.

    proxy_chain is a list like ["http://127.0.0.1:7897", None] meaning
    "try Clash first, then direct". The last error is raised if all fail.
    """
    if proxy_chain is None:
        proxy_chain = [None]
    last_exc: Optional[Exception] = None
    for proxy in proxy_chain:
        try:
            opener = _build_opener(proxy)
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - try next proxy option
            last_exc = exc
            via = proxy or "direct"
            print(f"[warn] request via {via} failed: {exc}", file=sys.stderr)
    if last_exc:
        raise last_exc
    raise RuntimeError("no proxy option attempted")


def _direct_alias(value: str) -> bool:
    return value.strip().lower() in {"direct", "none", "off", ""}


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "polymarket.config.json")
_CONFIG_CACHE: Optional[dict] = None


def _load_config() -> dict:
    """Read polymarket.config.json next to this script (cached). {} if absent."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            loaded = json.load(f)
        _CONFIG_CACHE = loaded if isinstance(loaded, dict) else {}
    except FileNotFoundError:
        _CONFIG_CACHE = {}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] reading {_CONFIG_PATH} failed: {exc}", file=sys.stderr)
        _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _load_config_proxy() -> Optional[str]:
    """Proxy from config (kept as a separate hook so tests can stub it)."""
    val = _load_config().get("proxy")
    return str(val) if val is not None else None


def _resolve_base(cli_val: Optional[str], env_keys: tuple, cfg_key: str, default: str) -> str:
    """CLI > env > config > default, for an API base URL (trailing slash trimmed)."""
    if cli_val:
        return cli_val.rstrip("/")
    for k in env_keys:
        v = os.environ.get(k)
        if v:
            return v.rstrip("/")
    v = _load_config().get(cfg_key)
    if v:
        return str(v).rstrip("/")
    return default


def resolve_proxy_chain(arg_proxy: Optional[str], no_proxy: bool) -> List[Optional[str]]:
    """Decide which proxy(ies) to try, in order, with a direct fallback.

    Precedence: --no-proxy > --proxy > env (POLYMARKET_PROXY/HTTPS_PROXY/HTTP_PROXY)
    > polymarket.config.json > built-in default (Clash). "direct"/"none"/"off"
    anywhere means connect with no proxy. This lets each deployment (your Clash
    box, a domestic server with its own egress, an overseas server) set it once
    instead of relying on one hardcoded address.
    """
    if no_proxy:
        return [None]
    if arg_proxy is not None:
        return [None] if _direct_alias(arg_proxy) else [arg_proxy, None]
    env = (
        os.environ.get("POLYMARKET_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
    )
    if env:
        return [None] if _direct_alias(env) else [env, None]
    cfg = _load_config_proxy()
    if cfg is not None:
        return [None] if _direct_alias(cfg) else [cfg, None]
    return [DEFAULT_PROXY, None]  # built-in default: Clash, then fall back to direct


# --------------------------------------------------------------------------- #
# Parsing helpers (pure functions — covered by --self-test)
# --------------------------------------------------------------------------- #
def _as_list(value: object) -> List:
    """Gamma returns `outcomes`/`outcomePrices`/`clobTokenIds` as JSON *strings*."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            return [s]
    return [value]


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


def parse_market_prices(market: dict) -> List[Tuple[str, float]]:
    """Return [(outcome_label, price), ...] for a Gamma market object."""
    labels = [str(x) for x in _as_list(market.get("outcomes"))]
    prices = [_num(x) for x in _as_list(market.get("outcomePrices"))]
    if not labels or len(labels) != len(prices):
        return []
    return list(zip(labels, prices))


def collect_markets(search_payload: object) -> List[dict]:
    """Flatten a Gamma /public-search or /events response into a market list.

    Handles: {"events":[{...,"markets":[...]}]}, a bare list of events,
    a bare list of markets, or a single event/market dict.
    """
    out: List[dict] = []

    def add_event(ev: dict) -> None:
        markets = ev.get("markets")
        if isinstance(markets, list):
            for m in markets:
                if isinstance(m, dict):
                    # Inherit event title to help relevance scoring.
                    m.setdefault("_event_title", ev.get("title") or ev.get("slug") or "")
                    out.append(m)

    if isinstance(search_payload, dict):
        if isinstance(search_payload.get("events"), list):
            for ev in search_payload["events"]:
                if isinstance(ev, dict):
                    add_event(ev)
        if isinstance(search_payload.get("markets"), list):
            out.extend(m for m in search_payload["markets"] if isinstance(m, dict))
        if "outcomes" in search_payload and "outcomePrices" in search_payload:
            out.append(search_payload)
    elif isinstance(search_payload, list):
        for item in search_payload:
            if not isinstance(item, dict):
                continue
            if "markets" in item:
                add_event(item)
            elif "outcomes" in item:
                out.append(item)
    return out


def is_usable(market: dict) -> bool:
    if market.get("closed") is True or market.get("archived") is True:
        return False
    pairs = parse_market_prices(market)
    if len(pairs) != 2:  # v1 only handles binary markets
        return False
    total = sum(p for _, p in pairs)
    return 0.5 < total < 1.5  # sane price pair (~sums to 1)


_DRAW_TOKENS = {"draw", "tie", "tied", "drawn"}


def _is_draw_label(label: str) -> bool:
    return bool(_tokens(label) & _DRAW_TOKENS)


def extract_team_pairs(market: dict, ta: set, tb: set):
    """Reduce a market to the two team (label, price) pairs.

    Handles binary moneyline (2 outcomes) AND 3-way soccer markets
    (Team A / Draw / Team B) by dropping the draw outcome. build_signal
    renormalizes, so a dropped draw yields P(win | not draw).

    Returns (pairs, dropped_draw) or (None, False) if not reducible to the teams.
    """
    pairs = parse_market_prices(market)
    if not pairs:
        return None, False
    if len(pairs) == 2:
        total = sum(p for _, p in pairs)
        return (pairs, False) if 0.5 < total < 1.5 else (None, False)
    if len(pairs) == 3 and ta and tb:
        non_draw = [(l, p) for l, p in pairs if not _is_draw_label(l)]
        draws = [(l, p) for l, p in pairs if _is_draw_label(l)]
        if len(non_draw) == 2 and len(draws) == 1:
            lt = [_tokens(l) for l, _ in non_draw]
            if any(ta & t for t in lt) and any(tb & t for t in lt):
                return non_draw, True
    return None, False


_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_WIN_RE = re.compile(r"\bwill\s+(.+?)\s+win\b", re.IGNORECASE)


def _yes_price(market: dict) -> Optional[float]:
    for label, price in parse_market_prices(market):
        if label.strip().lower() == "yes":
            return price
    return None


def _yes_token(market: dict) -> Optional[str]:
    """CLOB token id aligned with the 'Yes' outcome of a binary market, if any."""
    labels = [str(x) for x in _as_list(market.get("outcomes"))]
    tokens = [str(t) for t in _as_list(market.get("clobTokenIds"))]
    if len(labels) != len(tokens):
        return None
    for label, tok in zip(labels, tokens):
        if label.strip().lower() == "yes":
            return tok
    return None


def synthesize_match_markets(
    markets: List[dict],
    ta: set,
    tb: set,
    label_a: str,
    label_b: str,
    today: Optional[_dt.date] = None,
):
    """Combine Polymarket's 3-separate-Yes/No-markets soccer structure into one.

    Polymarket models a match as an event "TeamA vs. TeamB" containing three
    binary markets: "Will TeamA win on DATE?", "Will ... end in a draw?",
    "Will TeamB win on DATE?". This fuses them into a single synthetic 3-way
    market [TeamA, Draw, TeamB] so the normal pipeline can reduce it to binary.

    Returns (synthetic_markets, drop_event_titles, skip_reason).
    drop_event_titles: events that were handled here and whose raw sub-markets
    must be removed from the pool (so they aren't treated as standalone Yes/No).
    skip_reason: set when a match event was found but is stale/settled.
    """
    today = today or _dt.date.today()
    groups: Dict[str, List[dict]] = {}
    for m in markets:
        groups.setdefault(str(m.get("_event_title", "") or ""), []).append(m)

    synthetic: List[dict] = []
    drop_titles: set = set()
    skip_reason: Optional[str] = None

    for title, group in groups.items():
        tt = _tokens(title)
        if not (ta & tt and tb & tt):
            continue  # not the event for our two teams

        a_yes = b_yes = draw_yes = None
        a_closed = b_closed = draw_closed = False
        a_token = b_token = None
        match_date: Optional[_dt.date] = None
        vol = liq = 0.0
        for m in group:
            yp = _yes_price(m)
            if yp is None:
                continue
            q = str(m.get("question", ""))
            closed = m.get("closed") is True or m.get("archived") is True
            vol += _num(m.get("volumeNum") or m.get("volume"))
            liq += _num(m.get("liquidityNum") or m.get("liquidity"))
            d = _DATE_RE.search(q)
            if d and match_date is None:
                try:
                    match_date = _dt.date.fromisoformat(d.group(1))
                except ValueError:
                    pass
            if _tokens(q) & _DRAW_TOKENS:
                draw_yes, draw_closed = yp, closed
            else:
                wm = _WIN_RE.search(q)
                subj = _tokens(wm.group(1)) if wm else set()
                if (subj & ta) and not (subj & tb):
                    a_yes, a_closed, a_token = yp, closed, _yes_token(m)
                elif (subj & tb) and not (subj & ta):
                    b_yes, b_closed, b_token = yp, closed, _yes_token(m)

        if a_yes is None or b_yes is None:
            continue  # not the standard win/draw/win triple

        drop_titles.add(title)  # handled — remove raw sub-markets from pool either way

        # --- staleness / settlement guards ---
        if a_closed or b_closed or draw_closed:
            skip_reason = (
                f"{title} 的盘已结算（closed/archived），比赛已开赛或结束 → 跳过 market 源"
            )
            continue
        if match_date and match_date < today:
            skip_reason = (
                f"match {match_date}已结束（今天 {today}），Polymarket 盘已结算/陈旧，"
                f"不是赛前预测信号 → 跳过 market 源"
            )
            continue
        if draw_yes is not None and draw_yes > 0.90:
            skip_reason = (
                f"平局 Yes={draw_yes:.3f}（>0.9），盘已结算或未注入流动性，数据不可信 → 跳过"
            )
            continue
        if (a_yes + b_yes) < 0.05:
            skip_reason = "两队胜率均≈0，盘已结算或未开盘，数据不可信 → 跳过"
            continue

        if draw_yes is not None:
            outcomes = [label_a, "Draw", label_b]
            prices = [a_yes, draw_yes, b_yes]
        else:
            outcomes = [label_a, label_b]
            prices = [a_yes, b_yes]
        synth = {
            "question": f"{title} (match result, combined)",
            "_event_title": title,
            "_match_date": match_date.isoformat() if match_date else None,
            "outcomes": json.dumps(outcomes, ensure_ascii=False),
            "outcomePrices": json.dumps([str(round(x, 4)) for x in prices]),
            "volumeNum": vol,
            "liquidityNum": liq,
            "_synthetic": True,
            "closed": False,
        }
        # Carry the two teams' Yes-side CLOB tokens (A/B order) so --use-clob can
        # refine the binary win prices; refine_with_clob pairs these with the
        # draw-dropped [A, B] pairs (both length 2).
        if a_token and b_token:
            synth["clobTokenIds"] = json.dumps([a_token, b_token])
        synthetic.append(synth)

    return synthetic, drop_titles, skip_reason


def score_market(market: dict, query_tokens: set) -> float:
    """Higher is better: token overlap (title) + liquidity/volume tiebreak."""
    title = " ".join(
        str(market.get(k, "")) for k in ("question", "_event_title", "groupItemTitle")
    )
    overlap = len(query_tokens & _tokens(title))
    liq = _num(market.get("liquidityNum") or market.get("liquidity"))
    vol = _num(market.get("volumeNum") or market.get("volume"))
    # Overlap dominates; liquidity/volume only break ties (log-ish via small weight).
    return overlap * 1000.0 + min(vol, 1_000_000) / 1_000.0 + min(liq, 1_000_000) / 10_000.0


def market_text_tokens(market: dict) -> set:
    """All searchable tokens for a market: question + event title + outcome labels."""
    parts = [str(market.get(k, "")) for k in ("question", "_event_title", "groupItemTitle")]
    parts += [str(x) for x in _as_list(market.get("outcomes"))]
    return _tokens(" ".join(parts))


def outcomes_are_team_names(market: dict, ta: set, tb: set) -> bool:
    """True when the outcome labels include both team names (2-way moneyline or 3-way+draw)."""
    labels = [str(x) for x in _as_list(market.get("outcomes"))]
    if len(labels) not in (2, 3):
        return False
    label_tokens = [_tokens(x) for x in labels]
    a_hit = any(ta & lt for lt in label_tokens)
    b_hit = any(tb & lt for lt in label_tokens)
    return a_hit and b_hit


_SUBJECT_RE = re.compile(
    r"\bwill\s+(.+?)\s+(?:beat|defeat|win\s+against|edge|overcome|get\s+past|advance\s+past|top)\b",
    re.IGNORECASE,
)


def infer_yes_is(question: str, ta: set, tb: set) -> Optional[str]:
    """For a Yes/No head-to-head, infer which team 'Yes' represents from the question."""
    m = _SUBJECT_RE.search(question or "")
    if not m:
        return None
    subj = _tokens(m.group(1))
    a, b = bool(ta & subj), bool(tb & subj)
    if a and not b:
        return "a"
    if b and not a:
        return "b"
    return None


def map_to_outcomes(
    pairs: List[Tuple[str, float]],
    outcome_a: str,
    outcome_b: str,
    label_a: Optional[str],
    label_b: Optional[str],
    yes_is: Optional[str],
) -> Dict[str, float]:
    """Map two Polymarket (label, price) pairs onto the skill's outcome keys.

    Strategies, in order:
      1. yes_is given + labels look like Yes/No → Yes price -> that team.
      2. label_a / label_b given → fuzzy-match labels to A/B by token overlap.
      3. Fall back to positional (first->A, second->B) with a warning note.
    """
    (l0, p0), (l1, p1) = pairs
    low0, low1 = l0.lower(), l1.lower()

    # Strategy 1: Yes/No market
    if {low0, low1} == {"yes", "no"} and yes_is:
        yes_price = p0 if low0 == "yes" else p1
        if yes_is == "a":
            return {outcome_a: round(yes_price, 4), outcome_b: round(1 - yes_price, 4)}
        return {outcome_b: round(yes_price, 4), outcome_a: round(1 - yes_price, 4)}

    # Strategy 2: name matching
    if label_a or label_b:
        ta, tb = _tokens(label_a or ""), _tokens(label_b or "")
        t0, t1 = _tokens(l0), _tokens(l1)
        score_0a = len(ta & t0) + len(tb & t1)
        score_0b = len(ta & t1) + len(tb & t0)
        if score_0a != score_0b:
            if score_0a > score_0b:
                return {outcome_a: round(p0, 4), outcome_b: round(p1, 4)}
            return {outcome_a: round(p1, 4), outcome_b: round(p0, 4)}

    # Strategy 3: positional fallback
    return {outcome_a: round(p0, 4), outcome_b: round(p1, 4)}


# --------------------------------------------------------------------------- #
# Live fetch
# --------------------------------------------------------------------------- #
def _market_key(m: dict) -> str:
    return "|".join(
        str(m.get(k, "")) for k in ("id", "conditionId", "question", "_event_title")
    )


def _has_both_teams(markets: List[dict], ta: set, tb: set) -> bool:
    for m in markets:
        mt = market_text_tokens(m)
        if (ta & mt) and (tb & mt):
            return True
    return False


def search_polymarket(
    queries: List[str],
    proxy_chain: List[Optional[str]],
    ta: Optional[set] = None,
    tb: Optional[set] = None,
    timeout: int = TIMEOUT,
    gamma_base: str = GAMMA_BASE,
) -> List[dict]:
    """Run public-search queries and merge/dedup markets.

    Stops early as soon as a market mentioning BOTH teams has been collected —
    that usually happens on the first (most targeted) query, cutting 3 network
    round-trips down to 1.
    """
    merged: Dict[str, dict] = {}

    def ingest(payload: object) -> None:
        for m in collect_markets(payload):
            merged.setdefault(_market_key(m), m)

    for q in queries:
        encoded = urllib.parse.quote(q)
        try:
            payload = http_get_json(
                f"{gamma_base}/public-search?q={encoded}&limit_per_type=50&events_status=active",
                proxy_chain,
                timeout,
            )
            ingest(payload)
        except Exception as exc:  # noqa: BLE001 - try next query / fallback
            print(f"[warn] public-search '{q}' failed: {exc}", file=sys.stderr)
        if ta and tb and _has_both_teams(list(merged.values()), ta, tb):
            break  # got the match event — no need for more queries

    # Fallback: scan recent active events if every search failed or returned nothing.
    if not merged:
        try:
            payload = http_get_json(
                f"{gamma_base}/events?closed=false&active=true&limit=200&order=volume24hr&ascending=false",
                proxy_chain,
                timeout,
            )
            ingest(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] events fallback failed: {exc}", file=sys.stderr)

    return list(merged.values())


def clob_midpoint(
    token_id: str, proxy_chain: List[Optional[str]], timeout: int = TIMEOUT, clob_base: str = CLOB_BASE
) -> Optional[float]:
    try:
        payload = http_get_json(
            f"{clob_base}/midpoint?token_id={urllib.parse.quote(token_id)}", proxy_chain, timeout
        )
        if isinstance(payload, dict) and "mid" in payload:
            return _num(payload["mid"], default=-1) if _num(payload["mid"], -1) >= 0 else None
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] CLOB midpoint failed for {token_id}: {exc}", file=sys.stderr)
    return None


def refine_with_clob(
    market: dict,
    pairs: List[Tuple[str, float]],
    proxy_chain: List[Optional[str]],
    timeout: int = TIMEOUT,
    clob_base: str = CLOB_BASE,
) -> List[Tuple[str, float]]:
    token_ids = [str(t) for t in _as_list(market.get("clobTokenIds"))]
    if len(token_ids) != len(pairs):
        return pairs
    refined: List[Tuple[str, float]] = []
    got_any = False
    for (label, price), tid in zip(pairs, token_ids):
        mid = clob_midpoint(tid, proxy_chain, timeout, clob_base)
        if mid is not None:
            refined.append((label, mid))
            got_any = True
        else:
            refined.append((label, price))
    return refined if got_any else pairs


def dump_raw_markets(
    markets: List[dict],
    ta: Optional[set] = None,
    tb: Optional[set] = None,
    limit: int = 40,
) -> None:
    """Print raw markets (pre-filter) to stderr.

    When team tokens are known, show ONLY markets mentioning at least one team
    (otherwise a broad query buries the match under hundreds of futures markets).
    Markets mentioning BOTH teams are flagged ◆.
    """
    print(f"[debug] raw markets returned by search: {len(markets)}", file=sys.stderr)

    if ta and tb:
        relevant = [m for m in markets if (ta | tb) & market_text_tokens(m)]
        both = [m for m in relevant if (ta & market_text_tokens(m)) and (tb & market_text_tokens(m))]
        print(
            f"[debug] mention a team: {len(relevant)} | mention BOTH teams: {len(both)}",
            file=sys.stderr,
        )
        both_ids = {id(m) for m in both}
        shown = both + [m for m in relevant if id(m) not in both_ids]
    else:
        shown = markets

    for m in shown[:limit]:
        mt = market_text_tokens(m)
        both_flag = "◆BOTH " if (ta and tb and (ta & mt) and (tb & mt)) else ""
        o = _as_list(m.get("outcomes"))
        p = _as_list(m.get("outcomePrices"))
        flags = []
        if m.get("closed") is True:
            flags.append("closed")
        if m.get("archived") is True:
            flags.append("archived")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        print(
            f"[debug] {both_flag}raw: \"{m.get('question', '')}\"{flag_str} "
            f"| event=\"{m.get('_event_title', '')}\" | outcomes={o} prices={p}",
            file=sys.stderr,
        )
    if len(shown) > limit:
        print(f"[debug] ... ({len(shown) - limit} more relevant)", file=sys.stderr)


def pick_best(
    markets: List[dict],
    query: str,
    ta: Optional[set] = None,
    tb: Optional[set] = None,
    debug: bool = False,
):
    """Pick the best genuine head-to-head market and return (market, pairs, dropped_draw).

    Hard filter (when team tokens known): the market must mention BOTH teams and
    be reducible to the two teams (binary, or 3-way with a draw). This rejects
    single-team futures like "Will Cape Verde win the World Cup?".
    Markets whose outcomes ARE the two team names get a strong bonus.
    """
    qt = _tokens(query)
    candidates: List[Tuple[float, dict, list, bool]] = []
    for m in markets:
        if m.get("closed") is True or m.get("archived") is True:
            continue
        if ta and tb:
            mt = market_text_tokens(m)
            if not (ta & mt) or not (tb & mt):
                continue  # must reference both teams
            pairs, dropped = extract_team_pairs(m, ta, tb)
            if not pairs:
                continue  # not reducible to the two teams (e.g. futures, 4-way)
        else:
            if not is_usable(m):
                continue
            pairs, dropped = parse_market_prices(m), False
        score = score_market(m, qt)
        if ta and tb and outcomes_are_team_names(m, ta, tb):
            score += 5000.0  # true head-to-head signature
        candidates.append((score, m, pairs, dropped))

    candidates.sort(key=lambda x: x[0], reverse=True)

    if debug:
        if not candidates:
            print("[debug] no candidate passed the both-teams / reducible filter", file=sys.stderr)
        for s, m, pairs, dropped in candidates[:8]:
            tag = " (3-way, draw dropped)" if dropped else ""
            print(
                f"[debug] cand score={s:.0f} | {m.get('question', '')}{tag} | {pairs} | "
                f"vol=${_num(m.get('volumeNum') or m.get('volume')):,.0f}",
                file=sys.stderr,
            )

    if not candidates:
        return None, None, False
    _, market, pairs, dropped = candidates[0]
    return market, pairs, dropped


def build_signal(
    market: dict,
    pairs: List[Tuple[str, float]],
    outcome_a: str,
    outcome_b: str,
    label_a: Optional[str],
    label_b: Optional[str],
    yes_is: Optional[str],
    used_clob: bool,
    dropped_draw: bool = False,
) -> dict:
    probs = map_to_outcomes(pairs, outcome_a, outcome_b, label_a, label_b, yes_is)
    # Re-normalize defensively (also converts a 3-way pair to P(win | not draw)).
    total = sum(probs.values())
    if total > 0:
        probs = {k: round(v / total, 4) for k, v in probs.items()}
    vol = _num(market.get("volumeNum") or market.get("volume"))
    liq = _num(market.get("liquidityNum") or market.get("liquidity"))
    src = "Polymarket CLOB midpoint" if used_clob else "Polymarket outcomePrices"
    low_liq = " ⚠️低流动性" if vol < 10_000 else ""
    draw_note = " [3-way market, draw dropped → P(win|no draw)]" if dropped_draw else ""
    date_note = ""
    md = market.get("_match_date")
    if md:
        try:
            if _dt.date.fromisoformat(md) == _dt.date.today():
                date_note = f" [比赛日 {md}，价格可能含临场盘口]"
            else:
                date_note = f" [比赛日 {md}]"
        except ValueError:
            pass
    note = (
        f"{src}: \"{market.get('question', '')}\" "
        f"vol=${vol:,.0f} liq=${liq:,.0f}{low_liq}{draw_note}{date_note}"
    )
    return {"source": "market", "probabilities": probs, "note": note}


# --------------------------------------------------------------------------- #
# Self-test (offline)
# --------------------------------------------------------------------------- #
def _self_test() -> int:
    fixture = {
        "events": [
            {
                "title": "Spain vs Cape Verde",
                "markets": [
                    {
                        "question": "Will Spain beat Cape Verde?",
                        "outcomes": '["Spain", "Cape Verde"]',
                        "outcomePrices": '["0.86", "0.14"]',
                        "clobTokenIds": '["111", "222"]',
                        "volumeNum": 142000,
                        "liquidityNum": 38000,
                        "closed": False,
                    }
                ],
            },
            {
                "title": "World Cup 2026 Winner",
                "markets": [
                    {
                        # Single-team futures — must be rejected (Spain not mentioned).
                        "question": "Will Cape Verde win the 2026 FIFA World Cup?",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.0005", "0.9995"]',
                        "volumeNum": 42000000,
                        "closed": False,
                    }
                ],
            },
            {
                "title": "Unrelated NBA market",
                "markets": [
                    {
                        "question": "Lakers vs Celtics winner",
                        "outcomes": '["Lakers", "Celtics"]',
                        "outcomePrices": '["0.45", "0.55"]',
                        "volumeNum": 9000,
                        "closed": False,
                    }
                ],
            },
        ]
    }
    markets = collect_markets(fixture)
    assert len(markets) == 3, markets

    ta, tb = _tokens("Spain"), _tokens("Cape Verde")

    # With team tokens: the $42M futures market must NOT win — both-teams filter kills it.
    best, pairs, dropped = pick_best(markets, "Spain Cape Verde World Cup", ta, tb)
    assert best is not None and "beat Cape Verde" in best["question"], best
    assert dropped is False and pairs == [("Spain", 0.86), ("Cape Verde", 0.14)], (pairs, dropped)

    sig = build_signal(best, pairs, "ESP_win", "CPV_win", "Spain", "Cape Verde", None, False, dropped)
    assert abs(sig["probabilities"]["ESP_win"] - 0.86) < 1e-6, sig
    assert abs(sig["probabilities"]["CPV_win"] - 0.14) < 1e-6, sig

    # Futures-only fixture (no H2H) → found nothing.
    futures_only = {"events": [fixture["events"][1]]}
    fb, fp, _ = pick_best(collect_markets(futures_only), "Spain Cape Verde", ta, tb)
    assert fb is None and fp is None

    # 3-way soccer market (Spain / Draw / Cape Verde) → draw dropped, renormalized.
    threeway = {
        "events": [{
            "title": "Spain vs Cape Verde",
            "markets": [{
                "question": "Spain vs Cape Verde result",
                "outcomes": '["Spain", "Draw", "Cape Verde"]',
                "outcomePrices": '["0.70", "0.20", "0.10"]',
                "volumeNum": 50000,
                "closed": False,
            }],
        }]
    }
    tb_, tp, td = pick_best(collect_markets(threeway), "Spain Cape Verde", ta, tb)
    assert tb_ is not None and td is True, (tb_, td)
    tsig = build_signal(tb_, tp, "ESP_win", "CPV_win", "Spain", "Cape Verde", None, False, td)
    # 0.70 / (0.70+0.10) = 0.875
    assert abs(tsig["probabilities"]["ESP_win"] - 0.875) < 1e-3, tsig
    assert "draw dropped" in tsig["note"], tsig

    # Yes/No head-to-head subject inference.
    assert infer_yes_is("Will Spain beat Cape Verde?", ta, tb) == "a"
    assert infer_yes_is("Will Cape Verde beat Spain?", ta, tb) == "b"
    assert infer_yes_is("Spain vs Cape Verde", ta, tb) is None  # no clear subject

    yn = [("Yes", 0.72), ("No", 0.28)]
    m = map_to_outcomes(yn, "ESP_win", "CPV_win", None, None, "a")
    assert abs(m["ESP_win"] - 0.72) < 1e-6, m

    # Swapped name order still maps correctly.
    swapped = [("Cape Verde", 0.14), ("Spain", 0.86)]
    m2 = map_to_outcomes(swapped, "ESP_win", "CPV_win", "Spain", "Cape Verde", None)
    assert abs(m2["ESP_win"] - 0.86) < 1e-6, m2

    # --- Polymarket's real structure: 3 separate Yes/No markets per match ---
    future = _dt.date.today() + _dt.timedelta(days=5)
    def _ev(date):
        return [
            {"question": f"Will Spain win on {date}?", "_event_title": "Spain vs. Cabo Verde",
             "outcomes": '["Yes","No"]', "outcomePrices": '["0.82","0.18"]', "volumeNum": 19000000, "closed": False},
            {"question": "Will Spain vs. Cabo Verde end in a draw?", "_event_title": "Spain vs. Cabo Verde",
             "outcomes": '["Yes","No"]', "outcomePrices": '["0.13","0.87"]', "volumeNum": 4000000, "closed": False},
            {"question": f"Will Cabo Verde win on {date}?", "_event_title": "Spain vs. Cabo Verde",
             "outcomes": '["Yes","No"]', "outcomePrices": '["0.05","0.95"]', "volumeNum": 6000000, "closed": False},
        ]
    syn, drop, reason = synthesize_match_markets(_ev(future.isoformat()), ta, tb, "Spain", "Cape Verde")
    assert reason is None and len(syn) == 1 and "Spain vs. Cabo Verde" in drop, (syn, drop, reason)
    sb, sp, sd = pick_best(syn, "Spain Cape Verde", ta, tb)
    ssig = build_signal(sb, sp, "ESP_win", "CPV_win", "Spain", "Cape Verde", None, False, sd)
    # 0.82 / (0.82 + 0.05) = 0.9425  (P(Spain | not draw))
    assert abs(ssig["probabilities"]["ESP_win"] - 0.9425) < 2e-3, ssig

    # Synthesized soccer market must carry the two teams' Yes CLOB tokens (A/B order)
    # so --use-clob can refine it; refine pairs them with the draw-dropped [A,B] pairs.
    _ev_tok = _ev(future.isoformat())
    _ev_tok[0]["clobTokenIds"] = '["spainYes","spainNo"]'   # "Will Spain win" market
    _ev_tok[2]["clobTokenIds"] = '["cvYes","cvNo"]'         # "Will Cabo Verde win" market
    syn_t, _, _ = synthesize_match_markets(_ev_tok, ta, tb, "Spain", "Cape Verde")
    assert _as_list(syn_t[0].get("clobTokenIds")) == ["spainYes", "cvYes"], syn_t[0].get("clobTokenIds")
    sb2, sp2, _sd2 = pick_best(syn_t, "Spain Cape Verde", ta, tb)
    assert len(_as_list(sb2.get("clobTokenIds"))) == len(sp2) == 2  # refine_with_clob can zip them

    # Stale/settled match (date in the past) → synthesized nothing, reason set.
    past = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    syn2, drop2, reason2 = synthesize_match_markets(_ev(past), ta, tb, "Spain", "Cape Verde")
    assert syn2 == [] and reason2 and "已结束" in reason2, (syn2, reason2)

    # Settled-as-draw degenerate prices (draw>0.9) → skipped even if date parsed today.
    today_iso = _dt.date.today().isoformat()
    degen = [
        {"question": f"Will Spain win on {today_iso}?", "_event_title": "Spain vs. Cabo Verde",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.0005","0.9995"]', "volumeNum": 1, "closed": False},
        {"question": "Will Spain vs. Cabo Verde end in a draw?", "_event_title": "Spain vs. Cabo Verde",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.9995","0.0005"]', "volumeNum": 1, "closed": False},
        {"question": f"Will Cabo Verde win on {today_iso}?", "_event_title": "Spain vs. Cabo Verde",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.0005","0.9995"]', "volumeNum": 1, "closed": False},
    ]
    syn3, _, reason3 = synthesize_match_markets(degen, ta, tb, "Spain", "Cape Verde")
    assert syn3 == [] and reason3, (syn3, reason3)

    # Same-day RESOLVED match (date==today, not a draw) must be caught by closed flag,
    # not leak as a 100% "prediction".
    resolved = [
        {"question": f"Will Spain win on {today_iso}?", "_event_title": "Spain vs. Cabo Verde",
         "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]', "volumeNum": 100, "closed": True},
        {"question": "Will Spain vs. Cabo Verde end in a draw?", "_event_title": "Spain vs. Cabo Verde",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]', "volumeNum": 100, "closed": True},
        {"question": f"Will Cabo Verde win on {today_iso}?", "_event_title": "Spain vs. Cabo Verde",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]', "volumeNum": 100, "closed": True},
    ]
    syn4, _, reason4 = synthesize_match_markets(resolved, ta, tb, "Spain", "Cape Verde")
    assert syn4 == [] and reason4 and "结算" in reason4, (syn4, reason4)

    # --- proxy resolution (isolate env + config so the test is deterministic) ---
    _saved_env = {k: os.environ.pop(k, None) for k in (
        "POLYMARKET_PROXY", "HTTPS_PROXY", "HTTP_PROXY",
        "POLYMARKET_GAMMA_BASE", "POLYMARKET_CLOB_BASE",
    )}
    global _load_config_proxy, _load_config
    _orig_cfg, _orig_load = _load_config_proxy, _load_config
    try:
        _load_config_proxy = lambda: None  # noqa: E731 - no config file
        assert resolve_proxy_chain(None, False) == [DEFAULT_PROXY, None]
        assert resolve_proxy_chain("http://x:1", False) == ["http://x:1", None]
        assert resolve_proxy_chain("direct", False) == [None]
        assert resolve_proxy_chain(None, True) == [None]
        _load_config_proxy = lambda: "http://cfg:9"  # noqa: E731 - config present
        assert resolve_proxy_chain(None, False) == ["http://cfg:9", None]
        _load_config_proxy = lambda: "direct"  # noqa: E731 - config says direct
        assert resolve_proxy_chain(None, False) == [None]

        # base URL resolution: CLI > env > config > default
        _load_config = lambda: {}  # noqa: E731
        assert _resolve_base(None, ("POLYMARKET_GAMMA_BASE",), "gamma_base", GAMMA_BASE) == GAMMA_BASE
        assert _resolve_base("https://relay/gamma/", ("POLYMARKET_GAMMA_BASE",), "gamma_base", GAMMA_BASE) == "https://relay/gamma"
        _load_config = lambda: {"gamma_base": "https://cfg-relay/gamma"}  # noqa: E731
        assert _resolve_base(None, ("POLYMARKET_GAMMA_BASE",), "gamma_base", GAMMA_BASE) == "https://cfg-relay/gamma"
        os.environ["POLYMARKET_GAMMA_BASE"] = "https://env-relay/gamma"
        assert _resolve_base(None, ("POLYMARKET_GAMMA_BASE",), "gamma_base", GAMMA_BASE) == "https://env-relay/gamma"
        os.environ.pop("POLYMARKET_GAMMA_BASE", None)
    finally:
        _load_config_proxy, _load_config = _orig_cfg, _orig_load
        for k, v in _saved_env.items():
            if v is not None:
                os.environ[k] = v

    # --- search short-circuits after the first query that yields both teams ---
    global http_get_json
    _orig = http_get_json
    calls = {"n": 0}
    def _fake(url, proxy_chain=None, timeout=TIMEOUT):
        calls["n"] += 1
        return {"events": [{"title": "France vs. Senegal", "markets": [
            {"question": "Will France win on 2026-06-25?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.6","0.4"]'},
            {"question": "Will Senegal win on 2026-06-25?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.2","0.8"]'},
        ]}]}
    http_get_json = _fake
    try:
        res = search_polymarket(["France Senegal", "France vs Senegal", "France Senegal WC"],
                                [None], _tokens("France"), _tokens("Senegal"))
        assert calls["n"] == 1, f"expected short-circuit after 1 query, got {calls['n']}"
        assert _has_both_teams(res, _tokens("France"), _tokens("Senegal"))
    finally:
        http_get_json = _orig

    print("self-test OK ✅  (futures rejected · draw dropped · synthesized · stale/closed skipped · proxy/short-circuit)")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Polymarket probabilities for the `market` signal")
    parser.add_argument("--query", help='Search text, e.g. "Spain Cape Verde World Cup"')
    parser.add_argument("--outcome-a", help="Skill outcome key for team A, e.g. ESP_win")
    parser.add_argument("--outcome-b", help="Skill outcome key for team B, e.g. CPV_win")
    parser.add_argument("--label-a", help="Polymarket-side name for team A, e.g. Spain")
    parser.add_argument("--label-b", help="Polymarket-side name for team B, e.g. Cape Verde")
    parser.add_argument(
        "--yes-is",
        choices=["a", "b"],
        help="For Yes/No markets: which team the Yes side represents",
    )
    parser.add_argument("--use-clob", action="store_true", help="Refine with live CLOB midpoint")
    parser.add_argument(
        "--proxy",
        help=f"Proxy URL, or 'direct' for no proxy (default: {DEFAULT_PROXY} or polymarket.config.json)",
    )
    parser.add_argument("--no-proxy", action="store_true", help="Force a direct connection (no proxy)")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help=f"Per-request timeout seconds (default {TIMEOUT})")
    parser.add_argument("--gamma-base", help="Override Gamma API base URL (e.g. a relay for China users)")
    parser.add_argument("--clob-base", help="Override CLOB API base URL (e.g. a relay for China users)")
    parser.add_argument(
        "--from-file",
        help="Parse a saved Gamma API response (JSON) instead of hitting the network",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print candidate markets (question/outcomes/prices/volume) to stderr",
    )
    parser.add_argument("--output", help="Write Signal JSON to file (default: stdout)")
    parser.add_argument("--self-test", action="store_true", help="Run offline logic check and exit")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    if not (args.query and args.outcome_a and args.outcome_b):
        parser.error("--query, --outcome-a and --outcome-b are required (or use --self-test)")

    ta = _tokens(args.label_a) if args.label_a else None
    tb = _tokens(args.label_b) if args.label_b else None
    if not (ta and tb):
        print(
            "[warn] --label-a/--label-b not both given; cannot enforce both-teams filter, "
            "result may match a futures/single-team market. Pass team names to be safe.",
            file=sys.stderr,
        )

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as f:
            markets = collect_markets(json.load(f))
    else:
        proxy_chain = resolve_proxy_chain(args.proxy, args.no_proxy)
        gamma_base = _resolve_base(
            args.gamma_base, ("POLYMARKET_GAMMA_BASE",), "gamma_base", GAMMA_BASE
        )
        clob_base = _resolve_base(
            args.clob_base, ("POLYMARKET_CLOB_BASE",), "clob_base", CLOB_BASE
        )
        if args.debug:
            print(f"[debug] proxy chain: {[p or 'direct' for p in proxy_chain]}", file=sys.stderr)
            if gamma_base != GAMMA_BASE or clob_base != CLOB_BASE:
                print(f"[debug] relay bases: gamma={gamma_base} clob={clob_base}", file=sys.stderr)
        # Most-targeted queries first so the short-circuit usually needs just one
        # round-trip; bare team names hit the match event better than "... World Cup".
        queries: List[str] = []
        if args.label_a and args.label_b:
            queries.append(f"{args.label_a} {args.label_b}")
            queries.append(f"{args.label_a} vs {args.label_b}")
        queries.append(args.query)
        markets = search_polymarket(queries, proxy_chain, ta, tb, args.timeout, gamma_base)

    if not markets:
        result = {
            "source": "market",
            "found": False,
            "reason": (
                "no markets returned from Polymarket API "
                "(网络/代理不通？国内服务器请在 scripts/polymarket.config.json 配置可用代理，"
                "或设为 \"direct\"；连不上则本源自动跳过，其余源照常融合)"
            ),
        }
        _emit(result, args.output)
        return 0

    if args.debug:
        dump_raw_markets(markets, ta, tb)

    # Soccer matches are 3 separate Yes/No markets under one event — fuse them
    # into a single market and remove the raw sub-markets from the pool.
    skip_reason = None
    pool = markets
    if ta and tb and args.label_a and args.label_b:
        synthetic, drop_titles, skip_reason = synthesize_match_markets(
            markets, ta, tb, args.label_a, args.label_b
        )
        pool = synthetic + [
            m for m in markets if str(m.get("_event_title", "") or "") not in drop_titles
        ]
        if args.debug and synthetic:
            for s in synthetic:
                print(
                    f"[debug] synthesized match market: {s['question']} "
                    f"outcomes={_as_list(s['outcomes'])} prices={_as_list(s['outcomePrices'])}",
                    file=sys.stderr,
                )
        if args.debug and skip_reason:
            print(f"[debug] match event skipped: {skip_reason}", file=sys.stderr)

    best, pairs, dropped_draw = pick_best(pool, args.query, ta, tb, debug=args.debug)
    if best is None:
        result = {
            "source": "market",
            "found": False,
            "reason": skip_reason or (
                "no head-to-head market reducible to both teams found "
                "(Polymarket often has no per-match market for group-stage games; "
                "re-run with --debug to see the raw markets it did return)"
            ),
        }
        _emit(result, args.output)
        return 0

    # Resolve Yes/No markets safely: need to know which team 'Yes' is.
    yes_is = args.yes_is
    labels_lower = {l.lower() for l, _ in pairs}
    if labels_lower == {"yes", "no"} and not yes_is:
        yes_is = infer_yes_is(best.get("question", ""), ta or set(), tb or set())
        if not yes_is:
            result = {
                "source": "market",
                "found": False,
                "reason": (
                    "only a Yes/No market matched and the 'Yes' team is ambiguous; "
                    f"re-run with --yes-is a|b for: \"{best.get('question', '')}\""
                ),
            }
            _emit(result, args.output)
            return 0

    used_clob = False
    if args.use_clob and not args.from_file:
        refined = refine_with_clob(best, pairs, proxy_chain, args.timeout, clob_base)
        used_clob = refined != pairs
        pairs = refined

    signal = build_signal(
        best, pairs, args.outcome_a, args.outcome_b,
        args.label_a, args.label_b, yes_is, used_clob, dropped_draw,
    )
    signal["found"] = True
    _emit(signal, args.output)
    return 0


def _emit(obj: dict, output: Optional[str]) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    sys.exit(main())
