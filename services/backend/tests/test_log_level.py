"""Log level is set per function, from LOG_LEVEL, defaulting to WARNING.

Found on production 2026-09-21: the retrain Lambda wrote NO application log
lines at all. The Lambda Python runtime leaves the root logger at WARNING, so
every logger.info(...) was dropped - including "Retrained and promoted", the
success line docs/runbook.md tells an operator to look for. The failure paths
(WARNING, ERROR) did appear, which made the asymmetry worse: a failed night was
visible and a successful one was indistinguishable from one that never ran.

The fix is NOT "log INFO everywhere". ml/model.py logs at INFO on the telemetry
path, so INFO on the API would give anyone who can send traffic a lever on the
5GB/month CloudWatch Logs allowance - the budget ADR-002 said must be spent
deliberately. So the default stays WARNING and only the scheduled, low-volume
functions (retrain, probe) are raised to INFO, in Terraform.
"""
import logging

import pytest

from services.backend.core.log_level import apply_log_level


@pytest.fixture(autouse=True)
def _restore_root_level():
    root = logging.getLogger()
    saved = root.level
    yield
    root.setLevel(saved)


def test_default_is_warning_so_the_hot_path_stays_quiet(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    apply_log_level()
    assert logging.getLogger().level == logging.WARNING


def test_log_level_env_var_raises_it(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    apply_log_level()
    assert logging.getLogger().level == logging.INFO


def test_lowercase_is_accepted(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "info")
    apply_log_level()
    assert logging.getLogger().level == logging.INFO


def test_a_nonsense_level_falls_back_to_warning_instead_of_crashing_the_function(monkeypatch):
    """A typo in a Terraform env var must not take the function down at import
    time - that would be an outage caused by a logging setting."""
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    apply_log_level()
    assert logging.getLogger().level == logging.WARNING


def test_retrain_worker_applies_the_level_so_its_success_line_is_emitted(
        dynamo_resource, monkeypatch):
    from services.backend import retrain_handler
    from services.backend.core.tables import create_all_tables

    create_all_tables(dynamo_resource)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)
    retrain_handler.handler({"tenant_id": "t-none"}, None)

    assert retrain_handler.logger.isEnabledFor(logging.INFO)


def test_probe_applies_the_level_too(dynamo_resource, monkeypatch):
    from services.backend import probe_handler
    from services.backend.core.tables import create_all_tables

    create_all_tables(dynamo_resource)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("PROBE_TARGET_URL", "https://edge.example")
    monkeypatch.setattr(probe_handler, "get_dynamo_resource", lambda: dynamo_resource)
    monkeypatch.setattr(probe_handler, "_default_opener",
                        lambda req, timeout=None: (_ for _ in ()).throw(OSError("offline")))
    probe_handler.handler({}, None)

    assert logging.getLogger().level == logging.INFO
