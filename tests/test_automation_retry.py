"""Retry classification and backoff, without any real sleeping."""
import pytest

from pyatpg.automation.retry import Classification, RetryPolicy, classify_error, run_with_retries


def test_value_error_is_permanent():
    assert classify_error(ValueError("bad netlist")) is Classification.PERMANENT


def test_runtime_error_is_transient():
    # This is exactly what every LLMProvider raises once its own inner
    # retry budget is exhausted (see pyatpg.ranking.providers).
    assert classify_error(RuntimeError("Gemini failure: 503 UNAVAILABLE")) is Classification.TRANSIENT


def test_os_error_is_transient():
    assert classify_error(OSError("connection reset")) is Classification.TRANSIENT


def test_atalanta_not_available_is_fatal():
    from pyatpg.atpg.binary import AtalantaNotAvailable

    assert classify_error(AtalantaNotAvailable("binary missing")) is Classification.FATAL


class TestRunWithRetries:
    def _policy(self, **kwargs):
        return RetryPolicy(max_attempts=kwargs.pop("max_attempts", 4), base_delay=1.0, max_delay=10.0, **kwargs)

    def test_succeeds_first_try_without_sleeping(self):
        sleeps = []
        outcome = run_with_retries(lambda: 42, self._policy(), sleep=sleeps.append)
        assert outcome.ok
        assert outcome.value == 42
        assert outcome.attempts == 1
        assert sleeps == []

    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return "ok"

        sleeps = []
        outcome = run_with_retries(flaky, self._policy(), sleep=sleeps.append)
        assert outcome.ok
        assert outcome.value == "ok"
        assert outcome.attempts == 3
        assert len(sleeps) == 2  # slept before attempt 2 and attempt 3

    def test_permanent_error_does_not_retry(self):
        calls = {"n": 0}

        def always_bad():
            calls["n"] += 1
            raise ValueError("malformed netlist")

        sleeps = []
        outcome = run_with_retries(always_bad, self._policy(), sleep=sleeps.append)
        assert not outcome.ok
        assert outcome.classification is Classification.PERMANENT
        assert outcome.attempts == 1
        assert calls["n"] == 1
        assert sleeps == []

    def test_fatal_error_does_not_retry(self):
        from pyatpg.atpg.binary import AtalantaNotAvailable

        calls = {"n": 0}

        def engine_missing():
            calls["n"] += 1
            raise AtalantaNotAvailable("binary missing")

        outcome = run_with_retries(
            engine_missing, self._policy(),
            sleep=lambda s: pytest.fail("should not sleep on a fatal error"),
        )
        assert not outcome.ok
        assert outcome.classification is Classification.FATAL
        assert outcome.attempts == 1
        assert calls["n"] == 1

    def test_exhausts_transient_retries_and_reports_last_error(self):
        def always_flaky():
            raise RuntimeError("rate limited")

        sleeps = []
        outcome = run_with_retries(always_flaky, self._policy(max_attempts=3), sleep=sleeps.append)
        assert not outcome.ok
        assert outcome.attempts == 3
        assert outcome.classification is Classification.TRANSIENT
        assert "rate limited" in outcome.error
        assert len(sleeps) == 2  # never sleeps after the final attempt

    def test_on_attempt_callback_fires_each_try(self):
        seen = []

        def flaky():
            if len(seen) < 2:
                raise RuntimeError("boom")
            return "done"

        run_with_retries(
            flaky, self._policy(),
            on_attempt=lambda n, err: seen.append((n, err is not None)),
            sleep=lambda s: None,
        )
        assert seen == [(1, True), (2, True), (3, False)]


class TestRetryPolicy:
    def test_delay_grows_and_is_capped(self):
        policy = RetryPolicy(max_attempts=10, base_delay=10.0, max_delay=40.0, jitter=0.0)
        assert policy.delay_for(1) == 10.0
        assert policy.delay_for(2) == 20.0
        assert policy.delay_for(3) == 40.0
        assert policy.delay_for(4) == 40.0  # capped, not 80

    def test_jitter_stays_non_negative(self):
        policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=100.0, jitter=0.9)
        for attempt in range(1, 6):
            assert policy.delay_for(attempt) >= 0.0
