import logging
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from prometheus_client import Counter, Gauge, Histogram

from services.ai_engine.ml.feature_engineering import FeatureVector
from services.ai_engine.ml.model import FEATURE_NAMES
from services.ai_engine.ml.registry import load_metadata

logger = logging.getLogger(__name__)

# ── Prometheus metrics ────────────────────────────────────────────

ai_anomalies_detected_total = Counter(
    "ai_anomalies_detected_total",
    "Total anomalies detected by the ML model",
    ["reason", "tier"],
)

shadow_mode_active = Gauge(
    "shadow_mode_active",
    "1 if system is in shadow mode (no mitigation), 0 if live",
)

nginx_blocked_ips_total = Gauge(
    "nginx_blocked_ips_total",
    "Number of IPs currently under hard block (Tier 2)",
)

nginx_rate_limited_ips_total = Gauge(
    "nginx_rate_limited_ips_total",
    "Number of IPs currently under rate limiting (Tier 1)",
)

estimated_cloud_cost_saved_usd = Counter(
    "estimated_cloud_cost_saved_usd",
    "Estimated cloud cost saved in USD by blocking malicious requests",
)

model_anomaly_score_mean = Gauge(
    "model_anomaly_score_mean",
    "Rolling mean of anomaly scores from recent inference batch",
    ["version"],
)

model_anomaly_score_std = Gauge(
    "model_anomaly_score_std",
    "Rolling std dev of anomaly scores from recent inference batch",
    ["version"],
)

feature_drift_score = Gauge(
    "feature_drift_score",
    "Feature drift score measured in standard deviations from baseline",
    ["feature"],
)

model_version_active = Gauge(
    "model_version_active",
    "Currently active model version indicator",
    ["version", "stage"],
)

training_samples_total = Gauge(
    "training_samples_total",
    "Number of samples used in the last model training run",
)

model_retrain_total = Counter(
    "model_retrain_total",
    "Total number of model retraining runs",
    ["trigger"],
)

inference_duration_seconds = Histogram(
    "inference_duration_seconds",
    "Time spent performing ML inference per batch",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

# ── Cost saving constants ─────────────────────────────────────────

COST_PER_BLOCKED_REQUEST_USD = 0.00005
BLOCKED_REQUESTS_ESTIMATE_PER_MITIGATION = 100

# ── Score history for distribution monitoring ─────────────────────

MAX_SCORE_HISTORY = 1000


@dataclass
class ModelMonitor:
    score_history: deque = field(
        default_factory=lambda: deque(maxlen=MAX_SCORE_HISTORY)
    )
    baseline_feature_means: dict[str, float] = field(default_factory=dict)
    baseline_feature_stds: dict[str, float] = field(default_factory=dict)

    def record_scores(self, scores: list[float], version: str) -> None:
        self.score_history.extend(scores)

        if len(self.score_history) >= 10:
            arr = np.array(list(self.score_history))
            model_anomaly_score_mean.labels(version=version).set(
                float(np.mean(arr))
            )
            model_anomaly_score_std.labels(version=version).set(
                float(np.std(arr))
            )

    def record_mitigation(self, tier: int) -> None:
        estimated_cost_saved = (
            BLOCKED_REQUESTS_ESTIMATE_PER_MITIGATION
            * COST_PER_BLOCKED_REQUEST_USD
        )
        estimated_cloud_cost_saved_usd.inc(estimated_cost_saved)
        logger.debug(
            "Cost saving recorded: tier=%d amount=%.5f",
            tier,
            estimated_cost_saved,
        )

    def set_baseline(self, vectors: list[FeatureVector]) -> None:
        if not vectors:
            return

        for i, name in enumerate(FEATURE_NAMES):
            values = [v.to_list()[i] for v in vectors]
            self.baseline_feature_means[name] = float(np.mean(values))
            self.baseline_feature_stds[name] = float(np.std(values)) or 1.0

        logger.info("Feature baseline set from %d vectors", len(vectors))

    def check_drift(self, vectors: list[FeatureVector]) -> None:
        if not vectors or not self.baseline_feature_means:
            return

        for i, name in enumerate(FEATURE_NAMES):
            current_values = [v.to_list()[i] for v in vectors]
            current_mean = float(np.mean(current_values))

            baseline_mean = self.baseline_feature_means.get(name, 0.0)
            baseline_std = self.baseline_feature_stds.get(name, 1.0)

            drift = abs(current_mean - baseline_mean) / baseline_std
            feature_drift_score.labels(feature=name).set(round(drift, 6))

            if drift > 2.0:
                logger.warning(
                    "Feature drift detected: feature=%s drift=%.3f "
                    "baseline_mean=%.4f current_mean=%.4f",
                    name,
                    drift,
                    baseline_mean,
                    current_mean,
                )

    def update_model_version_metric(self) -> None:
        meta = load_metadata("production")
        if meta:
            model_version_active.labels(
                version=meta.version,
                stage="production",
            ).set(1)
            training_samples_total.set(meta.training_samples)
            logger.info(
                "Model version metric updated: version=%s samples=%d",
                meta.version,
                meta.training_samples,
            )

    def update_mitigation_gauges(
        self,
        blocked_count: int,
        rate_limited_count: int,
    ) -> None:
        nginx_blocked_ips_total.set(blocked_count)
        nginx_rate_limited_ips_total.set(rate_limited_count)


model_monitor = ModelMonitor()
