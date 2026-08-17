# Local Manual-vs-Agent Investigation Study

This protocol produces a real local comparison without presenting a synthetic
fixture as a business outcome.

1. Use the three matched prompts in `data/evidence/investigation-tasks.json`.
2. Have the same operator complete each task manually and with the platform,
   randomising condition order. Do not reuse answers between conditions.
3. Start timing when the prompt is revealed; stop when the written answer and
   cited evidence are complete.
4. Score each answer against its task rubric, then append a row to
   `data/evidence/investigation-study-template.csv`.
5. Run `python scripts/analyze_investigation_study.py <completed-csv> --output artifacts/investigation-study-measured.json`.

The supplied `investigation-study-local.csv` is a **format example only**.
It is not a measured study and must never be cited as time saved.
