"""Run the test suite with a deterministic pytest plugin set.

The desktop environment has several auto-discovered plugins (Dash, LangSmith,
coverage, and typeguard) that add significant import and collection overhead.
The project only needs pytest-asyncio for its normal test entry points, so this
launcher disables third-party auto-loading and enables that plugin explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import pytest

    args = ["-p", "pytest_asyncio.plugin", *sys.argv[1:]]
    if len(args) == 2:
        args.append("tests")
    return pytest.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
