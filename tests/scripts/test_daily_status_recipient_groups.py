from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scheduler" / "daily_status.py"


def test_daily_status_recipients_are_ops_only_and_xyz_pilot_not_xyz_ops() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'NOTIFY_INCLUDE_GROUPS = ("OPS_ONLY", "XYZ_PILOT")' in source
    assert 'NOTIFY_EXCLUDE_GROUPS = ("XYZ_BIZ",)' in source
    assert 'OPS_ONLY ∪ XYZ_PILOT, minus XYZ_BIZ' in source
    assert 'NOTIFY_INCLUDE_GROUPS = ("OPS_ONLY", "XYZ_PILOT", "XYZ_OPS")' not in source
