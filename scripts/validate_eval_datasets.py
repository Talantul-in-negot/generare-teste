"""Validate all domain golden sets before an evaluation run."""

from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from graphrag.evaluation.domain_eval import load_and_validate  # noqa: E402


def main() -> int:
    paths = sorted((ROOT / "data" / "eval_golden").glob("*.json"))
    failed = False
    for path in paths:
        report = load_and_validate(path)
        status = "OK" if report["valid"] else "FAIL"
        print(f"{status} {path.name}: {report['count']} cases")
        if not report["valid"]:
            failed = True
            for error in report["errors"]:
                print(f"  - {error}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
