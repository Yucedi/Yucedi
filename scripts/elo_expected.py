#!/usr/bin/env python3
"""Compute win probabilities from Elo ratings."""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Dict, Optional


def expected_score(rating_a: float, rating_b: float, home_advantage: float = 0.0) -> float:
    exponent = (rating_b - rating_a + home_advantage) / 400.0
    return 1.0 / (1.0 + math.pow(10.0, exponent))


def compute_probabilities(
    outcome_a: str,
    outcome_b: str,
    rating_a: float,
    rating_b: float,
    home_outcome: Optional[str] = None,
    home_advantage: float = 100.0,
) -> Dict[str, float]:
    # Home advantage reduces the effective rating gap for the home side.
    adjustment = 0.0
    if home_outcome == outcome_a:
        adjustment = -home_advantage
    elif home_outcome == outcome_b:
        adjustment = home_advantage

    prob_a = expected_score(rating_a, rating_b, adjustment)
    return {outcome_a: round(prob_a, 4), outcome_b: round(1.0 - prob_a, 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Elo ratings to win probabilities")
    parser.add_argument(
        "--input",
        help=(
            'JSON string: {"outcome_a":"LAL_win","outcome_b":"BOS_win",'
            '"rating_a":1580,"rating_b":1620,"home_outcome":"LAL_win","home_advantage":100}'
        ),
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
    required = ["outcome_a", "outcome_b", "rating_a", "rating_b"]
    missing = [k for k in required if k not in payload]
    if missing:
        parser.error(f"missing fields: {', '.join(missing)}")

    probs = compute_probabilities(
        outcome_a=str(payload["outcome_a"]),
        outcome_b=str(payload["outcome_b"]),
        rating_a=float(payload["rating_a"]),
        rating_b=float(payload["rating_b"]),
        home_outcome=payload.get("home_outcome"),
        home_advantage=float(payload.get("home_advantage", 100.0)),
    )

    result = {
        "probabilities": probs,
        "method": "elo_expected_score",
        "inputs": {
            "rating_a": payload["rating_a"],
            "rating_b": payload["rating_b"],
            "home_outcome": payload.get("home_outcome"),
            "home_advantage": payload.get("home_advantage", 100.0),
        },
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
