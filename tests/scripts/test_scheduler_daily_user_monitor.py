from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_SOURCE = REPO_ROOT / "scheduler" / "lambda_function.py"


def test_daily_user_monitor_is_scheduled_stop_only_before_ec2_stop() -> None:
    source = LAMBDA_SOURCE.read_text(encoding="utf-8")
    assert "if notify and not is_ad_hoc:" in source
    assert "daily_monitor = run_daily_user_monitor()" in source

    monitor_pos = source.index("daily_monitor = run_daily_user_monitor()")
    stop_pos = source.index("ec2.stop_instances(InstanceIds=[EC2_INSTANCE_ID])")
    assert monitor_pos < stop_pos


def test_daily_user_monitor_uses_private_evidence_path_and_ops_only_mail() -> None:
    source = LAMBDA_SOURCE.read_text(encoding="utf-8")
    assert "docs/60-evidence-inventory/daily-user-monitor" in source
    assert "_send_daily_user_monitor_email" in source
    assert "recipients = _warmup_warning_recipients()" in source
    assert "ad-hoc Operator Dashboard STOPs never generate daily evidence" in source


def test_daily_user_monitor_subject_uses_reviewed_activity_day() -> None:
    source = LAMBDA_SOURCE.read_text(encoding="utf-8")
    assert "executive_summary['reviewed_date'].replace('-', '.')" in source
    assert 'dry_run_label = "DRY-RUN " if dry_run else ""' in source
    assert "if now_cest.hour < 6:" in source
    assert "reviewed_date = reviewed_date - dt.timedelta(days=1)" in source


def test_daily_user_monitor_dry_run_does_not_archive_or_stop() -> None:
    source = LAMBDA_SOURCE.read_text(encoding="utf-8")
    assert 'event.get("daily_user_monitor_dry_run") is True' in source
    assert "run_daily_user_monitor(dry_run=True" in source
    assert '"status": "dry_run_not_archived"' in source
    assert "markdown_preview=markdown if dry_run else None" in source
