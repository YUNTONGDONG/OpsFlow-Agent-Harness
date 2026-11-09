"""Train a small pairwise linear RankSVM from precomputed feature rows."""

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np


FEATURES = ("dense_score", "token_overlap", "exact_phrase", "length_penalty")


def train(rows, *, epochs: int, learning_rate: float, regularization: float):
    matrix = np.asarray(
        [[float(row[name]) for name in FEATURES] for row in rows],
        dtype="float64",
    )
    means = matrix.mean(axis=0)
    stds = matrix.std(axis=0)
    stds[stds == 0] = 1.0

    grouped = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    pairs = []
    for group in grouped.values():
        for positive in group:
            for negative in group:
                if positive["relevance"] > negative["relevance"]:
                    pairs.append(
                        np.asarray(
                            [positive[name] - negative[name] for name in FEATURES]
                        )
                        / stds
                    )
    if not pairs:
        raise ValueError("training data has no ordered relevance pairs")

    weights = np.zeros(len(FEATURES), dtype="float64")
    for _ in range(epochs):
        for difference in pairs:
            weights *= 1 - learning_rate * regularization
            if float(weights @ difference) < 1:
                weights += learning_rate * difference
    model = {name: float(value) for name, value in zip(FEATURES, weights)}
    model["feature_means"] = {
        name: float(value) for name, value in zip(FEATURES, means)
    }
    model["feature_stds"] = {
        name: float(value) for name, value in zip(FEATURES, stds)
    }
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--regularization", type=float, default=0.001)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line]
    weights = train(
        rows,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        regularization=args.regularization,
    )
    weights["intercept"] = 0.0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(weights, indent=2) + "\n", encoding="utf-8")
    print(f"trained on {len(rows)} rows")


if __name__ == "__main__":
    main()
