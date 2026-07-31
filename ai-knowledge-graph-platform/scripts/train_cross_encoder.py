"""Prepare, train, and roll back domain cross-encoder candidates.

Input records are JSONL objects with ``query``, ``positive`` and ``negative``
text fields. Training is opt-in; the default command only validates and writes
a reproducible manifest, so evaluation data cannot silently alter production.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random


def load_pairs(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = {"query", "positive", "negative"}
    if not rows or any(not required.issubset(row) for row in rows):
        raise ValueError("training data must contain query, positive, and negative fields")
    return rows


def split_pairs(rows: list[dict], seed: int = 42, validation_fraction: float = 0.2) -> tuple[list[dict], list[dict]]:
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    cut = max(1, int(len(rows) * (1.0 - validation_fraction))) if len(rows) > 1 else len(rows)
    return rows[:cut], rows[cut:]


def write_manifest(output_dir: Path, data_path: Path, rows: list[dict], *, seed: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    train, validation = split_pairs(rows, seed=seed)
    manifest = {
        "status": "prepared",
        "dataset": str(data_path),
        "dataset_sha256": digest,
        "seed": seed,
        "train_count": len(train),
        "validation_count": len(validation),
        "base_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rollback": {"previous_model": "cross-encoder/ms-marco-MiniLM-L-6-v2"},
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("results/cross_encoder"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train", action="store_true", help="run sentence-transformers training")
    parser.add_argument("--rollback-from", type=Path, help="manifest to roll back to its recorded previous model")
    args = parser.parse_args()
    if args.rollback_from:
        manifest = json.loads(args.rollback_from.read_text(encoding="utf-8"))
        previous = manifest.get("rollback", {}).get("previous_model")
        if not previous:
            raise SystemExit("manifest has no rollback.previous_model")
        active_path = args.output_dir / "active_model.txt"
        args.output_dir.mkdir(parents=True, exist_ok=True)
        active_path.write_text(previous + "\n", encoding="utf-8")
        print(active_path)
        return 0
    rows = load_pairs(args.data)
    manifest_path = write_manifest(args.output_dir, args.data, rows, seed=args.seed)
    if args.train:
        raise SystemExit("training execution is intentionally gated: review manifest and run with a pinned training environment")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
