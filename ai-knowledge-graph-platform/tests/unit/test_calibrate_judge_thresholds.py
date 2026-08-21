import json

from scripts.calibrate_judge_thresholds import load_records, main


def test_load_records_accepts_faithfulness_runner_shape(tmp_path):
    path = tmp_path / "eval.json"
    path.write_text(json.dumps({"questions": [
        {"judge_confidence": 0.9, "golden_contract_pass": True},
        {"judge_confidence": 0.4, "golden_contract_pass": False},
    ]}), encoding="utf-8")
    assert load_records(path) == [
        {"confidence": 0.9, "correct": True},
        {"confidence": 0.4, "correct": False},
    ]
