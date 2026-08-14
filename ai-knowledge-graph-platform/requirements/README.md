# requirements/

Per-service dependency subsets, used by the multi-stage Docker builds so each
image installs only what it runs. They are **not** the source of truth for a
local install.

Which file to use:

| File | Purpose |
|------|---------|
| `../requirements.txt` | **Source of truth** for direct dependencies. Install this locally. |
| `../requirements-dev.txt` | Adds pytest, pytest-asyncio, ruff, pip-tools. Install this to run the tests. |
| `../requirements.lock` | Fully-pinned, `pip-compile`-generated from `requirements.txt`. Use for reproducible builds; regenerate with `make lock`. |
| `base.txt`, `api.txt`, `workers.txt`, … | Per-image subsets for Docker. Keep in sync with `../requirements.txt` by hand when adding a dependency. |

There is no lock file per subset — if a build needs reproducibility guarantees,
install from `../requirements.lock` instead.
