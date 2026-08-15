"""PostToolUse hook: after an Edit/Write to graphrag/**.py, run the unit
tests and print the tail of the result so a regression surfaces immediately
instead of at the end of the session.
"""
import json
import subprocess
import sys
from pathlib import Path

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

if "graphrag" in file_path.replace("\\", "/") and file_path.endswith(".py"):
    repo_root = Path(__file__).parents[2]
    # Run only the matching test file (e.g. chunker.py -> test_chunker.py) --
    # fast, targeted feedback per edit. The full suite still runs once at the
    # end of the session, not on every keystroke; see CLAUDE.md Token Efficiency.
    stem = Path(file_path).stem
    test_file = repo_root / "tests" / "unit" / f"test_{stem}.py"
    if test_file.exists():
        result = subprocess.run(
            ["python", "-m", "pytest", str(test_file), "-q"],
            cwd=repo_root, capture_output=True, text=True,
        )
        print(result.stdout[-1500:] or result.stderr[-1500:])
