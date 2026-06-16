#!/usr/bin/env python3
"""Convert betting odds to normalized implied probabilities."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List


def decimal_to_prob(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def american_to_prob(american: float) -> float:
    if american == 0:
        raise ValueError("american odds cannot be 0")
    if american > 0:
        return 100.0 / (american + 100.0)
    return (-american) / (-american + 100.0)


def fractional_to_prob(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return denominator / (numerator + denominator)


def parse_odds_entry(entry: dict) -> float:
    fmt = entry.get("format", "decimal").lower()
    if fmt == "decimal":
        return decimal_to_prob(float(entry["odds"]))
    if fmt == "american":
        return american_to_prob(float(entry["odds"]))
    if fmt == "fractional":
        parts = str(entry["odds"]).split("/")
        if len(parts) != 2:
            raise ValueError(f"invalid fractional odds: {entry['odds']}")
        return fractional_to_prob(float(parts[0]), float(parts[1]))
    raise ValueError(f"unsupported format: {fmt}")


def remove_overround(probs: Dict[str, float]) -> Dict[str, float]:
    total = sum(probs.values())
    if total <= 0:
        raise ValueError("probabilities must sum to a positive value")
    return {k: round(v / total, 4) for k, v in probs.items()}


def normalize_odds(outcomes: List[dict]) -> Dict[str, float]:
    raw = {item["outcome"]: parse_odds_entry(item) for item in outcomes}
    return remove_overround(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize betting odds to probabilities")
    parser.add_argument(
        "--input",
        help='JSON string: {"outcomes":[{"outcome":"A_win","format":"decimal","odds":2.1},...]}',
    )
    parser.add_argument("--input-file", help="Path to JSON file (alternative to --input)")
    parser.add_argument("--output", help="Write result JSON to file (default: stdout)")
    args = parser.parse_args()

    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            payload = json.load(f)
    elif args.input:
        payload = json.loads(args.input)
    else:
        parser.error("--input or --input-file is required")
    outcomes = payload.get("outcomes", payload)
    if not isinstance(outcomes, list):
        parser.error("input must contain an outcomes list")

    result = {
        "probabilities": normalize_odds(outcomes),
        "method": "proportional_overround_removal",
    }

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
