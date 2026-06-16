#!/usr/bin/env python3
"""Fuse multi-source prediction signals with fixed sports weights."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Set


DEFAULT_WEIGHTS = {
    "odds": 0.30,
    "market": 0.10,
    "elo": 0.25,
    "historical": 0.20,
    "sentiment": 0.15,
}


def fuse_signals(signals: List[dict], weights: Dict[str, float]) -> dict:
    if not signals:
        raise ValueError("at least one signal is required")

    outcomes: Set[str] = set()
    for signal in signals:
        outcomes.update(signal["probabilities"].keys())

    if not outcomes:
        raise ValueError("signals must include probabilities")

    used_sources = []
    active_weights: Dict[str, float] = {}
    for signal in signals:
        source = signal["source"]
        if source not in weights:
            raise ValueError(f"unknown source: {source}")
        used_sources.append(source)
        active_weights[source] = weights[source]

    weight_sum = sum(active_weights.values())
    if weight_sum <= 0:
        raise ValueError("active weights must sum to a positive value")

    fused: Dict[str, float] = {outcome: 0.0 for outcome in outcomes}
    for signal in signals:
        source = signal["source"]
        w = active_weights[source] / weight_sum
        for outcome, prob in signal["probabilities"].items():
            fused[outcome] += w * float(prob)

    total = sum(fused.values())
    if total <= 0:
        raise ValueError("fused probabilities must sum to a positive value")

    normalized = {k: round(v / total, 4) for k, v in fused.items()}
    missing = [s for s in weights if s not in used_sources]

    return {
        "probabilities": normalized,
        "sources_used": used_sources,
        "sources_missing": missing,
        "method": "weighted_ensemble_v1",
        "weights_applied": {s: round(active_weights[s] / weight_sum, 4) for s in used_sources},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fuse prediction signals")
    parser.add_argument("--input", required=True, help="Path to signals.json")
    parser.add_argument("--output", required=True, help="Path to result.json")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        payload = json.load(f)

    signals = payload.get("signals", payload)
    if not isinstance(signals, list):
        parser.error("input must contain a signals list")

    result = fuse_signals(signals, DEFAULT_WEIGHTS)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
