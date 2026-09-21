import logging
import os

_DEFAULT = "WARNING"


def apply_log_level() -> None:
    """Set the root logger from LOG_LEVEL, defaulting to WARNING.

    The Lambda Python runtime leaves the root logger at WARNING, so without
    this every logger.info(...) in a function is silently dropped - found on
    production, where the retrain Lambda's "Retrained and promoted" line, the
    success signal the runbook points operators at, had never once been
    written. Its failure lines (WARNING, ERROR) always had been.

    WARNING stays the default on purpose. ml/model.py logs at INFO on the
    telemetry path, so raising the API's level would hand anyone able to send
    traffic a lever on the 5GB/month CloudWatch Logs allowance. Terraform sets
    LOG_LEVEL=INFO only on the scheduled, low-volume functions.

    An unrecognised value falls back to the default rather than raising: a
    typo in an environment variable must not become an outage at import time.
    """
    wanted = os.environ.get("LOG_LEVEL", _DEFAULT).upper()
    level = logging.getLevelName(wanted)
    if not isinstance(level, int):
        level = logging.getLevelName(_DEFAULT)
    logging.getLogger().setLevel(level)
