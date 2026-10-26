"""Deterministic Monte Carlo estimate for all country/rarity price curves.

Run with: python tests/economy_simulation.py [samples-per-country-rarity]
Prices are sampled as local currency, then converted into common balance units.
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot import game


def main(samples: int = 1200) -> int:
    random.seed(20261026)
    weights = {r["id"]: r["w"] for r in game.RARITIES}
    weight_total = sum(weights.values())
    failures = []

    print(f"Simulation: {samples:,} samples per country × rarity; seed=20261026")
    print("country  normal-spin EV/cost  max-category EV/cost  secret chance")
    for country in game.COUNTRIES:
        means = {}
        for rarity in game.RARITIES:
            values = []
            for _ in range(samples):
                digits = game.gen_digits(country["L"], rarity["id"])
                price_native = game.price_for(digits, rarity, country)
                values.append(game.to_units(price_native, country["k"]))
            means[rarity["id"]] = statistics.fmean(values)

        normal_ev = sum(weights[k] * means[k] for k in weights) / weight_total
        normal_ratio = normal_ev / game.spin_cost(None, 1)
        category_ratios = []
        for idx, rarity in enumerate(game.RARITIES):
            allowed = game.RARITIES[idx:]
            denominator = sum(weights[r["id"]] for r in allowed)
            category_ev = sum(weights[r["id"]] * means[r["id"]] for r in allowed) / denominator
            category_ratios.append(category_ev / game.spin_cost(rarity["id"], 1))
        max_ratio = max(category_ratios)
        secret_probability = weights["sec"] / weight_total * 100
        print(f"{country['k']:<8} {normal_ratio:>8.3f}             {max_ratio:>8.3f}             {secret_probability:.3f}%")

        # Guard against economic regressions; tolerance accounts for Monte Carlo noise.
        if not (0.72 <= normal_ratio <= 1.02):
            failures.append(f"{country['k']}: normal EV/cost={normal_ratio:.3f}")
        if max_ratio > 1.08:
            failures.append(f"{country['k']}: category EV/cost={max_ratio:.3f}")

    if failures:
        print("FAIL: " + "; ".join(failures))
        return 1
    print("OK: no country/category is materially positive-EV under the sampled model.")
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    raise SystemExit(main(max(100, min(n, 100_000))))
