"""T5 — @monitor cron 死人开关四路。

底层用 GlitchTip 自带 Cron Monitor(sentry_sdk.capture_checkin),不自建上报。
契约:
  成功    → check-in OK
  抛异常  → check-in error,且异常仍向上抛(不吞)
  漏启动  → 注册 schedule 让 GlitchTip server 端标记
  端点宕  → 任务仍正常跑(fail-open)
"""
import logging

import pytest

import zlx_ops_sdk


class _Recorder:
    """记录每次 capture_checkin 调用。"""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, *, monitor_slug, status, check_in_id=None, duration=None, monitor_config=None):
        if self.fail:
            raise ConnectionError("checkin endpoint down")
        self.calls.append(
            dict(
                monitor_slug=monitor_slug,
                status=status,
                check_in_id=check_in_id,
                monitor_config=monitor_config,
            )
        )
        return check_in_id or "checkin-id-123"


@pytest.fixture
def recorder(monkeypatch):
    import sentry_sdk.crons

    rec = _Recorder()
    monkeypatch.setattr(sentry_sdk.crons, "capture_checkin", rec)
    return rec


# --- 成功路 ----------------------------------------------------------------

def test_success_pings_ok(recorder):
    @zlx_ops_sdk.monitor(monitor_slug="nightly-job")
    def job():
        return 42

    assert job() == 42

    statuses = [c["status"] for c in recorder.calls]
    assert statuses == ["in_progress", "ok"]
    assert all(c["monitor_slug"] == "nightly-job" for c in recorder.calls)
    # ok 复用 in_progress 的 check_in_id
    assert recorder.calls[1]["check_in_id"] == "checkin-id-123"


# --- 失败路 ----------------------------------------------------------------

def test_failure_pings_error_and_reraises(recorder):
    @zlx_ops_sdk.monitor(monitor_slug="flaky-job")
    def job():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        job()

    statuses = [c["status"] for c in recorder.calls]
    assert statuses == ["in_progress", "error"]


# --- 漏启动路:注册 schedule ----------------------------------------------

def test_schedule_registered_for_missed_detection(recorder):
    @zlx_ops_sdk.monitor(monitor_slug="hourly-job", schedule="0 * * * *")
    def job():
        return "ok"

    job()

    first = recorder.calls[0]
    assert first["status"] == "in_progress"
    assert first["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "0 * * * *"}
    }


def test_schedule_config_extras_passed(recorder):
    @zlx_ops_sdk.monitor(
        monitor_slug="job",
        schedule="*/5 * * * *",
        checkin_margin=2,
        max_runtime=10,
        timezone="UTC",
    )
    def job():
        return None

    job()
    cfg = recorder.calls[0]["monitor_config"]
    assert cfg["schedule"] == {"type": "crontab", "value": "*/5 * * * *"}
    assert cfg["checkin_margin"] == 2
    assert cfg["max_runtime"] == 10
    assert cfg["timezone"] == "UTC"


# --- 端点宕路:fail-open ---------------------------------------------------

def test_checkin_endpoint_down_task_still_runs(monkeypatch, caplog):
    import sentry_sdk.crons

    monkeypatch.setattr(sentry_sdk.crons, "capture_checkin", _Recorder(fail=True))

    @zlx_ops_sdk.monitor(monitor_slug="job")
    def job():
        return "result"

    with caplog.at_level(logging.WARNING, logger="zlx_ops_sdk"):
        assert job() == "result"  # check-in 宕,任务照常完成

    assert any("checkin" in r.message.lower() or "check-in" in r.message.lower()
               for r in caplog.records)


def test_checkin_down_still_reraises_task_error(monkeypatch):
    import sentry_sdk.crons

    monkeypatch.setattr(sentry_sdk.crons, "capture_checkin", _Recorder(fail=True))

    @zlx_ops_sdk.monitor(monitor_slug="job")
    def job():
        raise KeyError("real-error")

    # check-in 宕不能掩盖任务真实异常
    with pytest.raises(KeyError, match="real-error"):
        job()


def test_preserves_function_metadata(recorder):
    @zlx_ops_sdk.monitor(monitor_slug="job")
    def my_job():
        """doc string."""
        return 1

    assert my_job.__name__ == "my_job"
    assert my_job.__doc__ == "doc string."
