"""Training-data poisoning: an attacker must not be able to teach the model that
their attack is normal.

Found on production 2026-09-21 by watching one attacker's anomaly score drift
across the day's retrains: -0.204 on first sight, then -0.105, one hair above
the -0.1 threshold. The nightly retrain trains on every telemetry bucket in a
25-hour window, including the buckets that had just been judged attacks.

A controlled experiment (same settings as production: 50 estimators,
contamination 0.01, random_state 42, decision_function) measured how sharp it
is. With k copies of the attacker's vector in the training data, across 20
different normal baselines:

    k=1   RATE_LIMIT 20/20
    k=2   RATE_LIMIT 13/20, NORMAL 7/20
    k>=3  NORMAL 20/20

IsolationForest flags what is easy to isolate. One attack bucket is isolated
at once; three identical ones form a small cluster and stop looking unusual.
Production held exactly three attacker buckets when this was found, so the
next nightly retrain would have let the attacker through - by attacking three
times, the attacker trains the model to accept them.

The defence: a bucket the system judged hostile is flagged when the decision
is made, and training skips flagged buckets. And whitelisted IPs are excluded
from training too - docs/architecture.md always said they were, the retrain
role was even granted read access to the whitelist for it, and the code never
did it.
"""
import random
import time

import numpy as np

from services.backend.core.tables import (
    TelemetryEventsTable,
    TenantsTable,
    WhitelistTable,
    create_all_tables,
)
from services.backend.ml import registry
from services.backend.ml.feature_engineering import (
    _vector_from_counts,
    collect_training_vectors,
    record_batch,
)
from services.backend.ml.model import AnomalyTier, classify_score
from services.backend.retrain_handler import retrain_tenant

TENANT = "t-1"
ATTACKER = "198.51.100.66"


class _Log:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _normal_baseline(resource, start: float, buckets: int = 8, tenant: str = TENANT) -> None:
    """Normal traffic with a few 404s and a few POSTs, like production's.

    An earlier version of this helper generated only 200s and only GETs, which
    made error_ratio and post_ratio CONSTANT across the training set.
    IsolationForest cannot split on a constant feature, so the attacker's two
    loudest signals - every request a 4xx, every request a POST - were
    invisible to the model and it caught nothing. That was a flaw in the test
    data, not in the product; production traffic varies on both."""
    rnd = random.Random(7)
    for b in range(buckets):
        logs = []
        for ip in {f"192.0.2.{rnd.randint(1, 250)}" for _ in range(25)}:
            for _ in range(rnd.randint(3, 9)):
                logs.append(_Log(remote_addr=ip,
                                 request_method="POST" if rnd.random() < 0.08 else "GET",
                                 request_uri=rnd.choice(["/", "/a", "/b", "/cart"]),
                                 status="404" if rnd.random() < 0.03 else "200",
                                 body_bytes_sent=str(rnd.randint(600, 6000)),
                                 request_time=f"{rnd.uniform(0.02, 0.35):.3f}",
                                 http_user_agent=rnd.choice(["Mozilla/5.0", "Safari/17"])))
        record_batch(resource, tenant, logs, now=start + b * 6)


def _attack(resource, at: float, tenant: str = TENANT) -> int:
    logs = [_Log(remote_addr=ATTACKER, request_method="POST",
                 request_uri=f"/wp-login.php?try={i}", status="401",
                 body_bytes_sent="90", request_time="0.003",
                 http_user_agent=f"python-requests/2.{i % 9}") for i in range(150)]
    record_batch(resource, tenant, logs, now=at)
    return int(at // 5) * 5


def _attacker_vector():
    return _vector_from_counts(ATTACKER, 5, 3, request_count=150, error_count=150,
                               post_count=150, total_bytes=150 * 90, total_time=150 * 0.003,
                               distinct_uri_count=150, distinct_ua_count=9).to_list()


def _score_attacker(resource, tenant: str = TENANT) -> float:
    model = registry.load_model(resource, tenant, stage="production")
    return float(model.decision_function(np.array([_attacker_vector()]))[0])


def _setup(resource, *tenants):
    create_all_tables(resource)
    for t in (tenants or (TENANT,)):
        TenantsTable(resource).put(tenant_id=t, name=t, status="active",
                                   created_at="2026-09-21T00:00:00Z")


def test_mark_flagged_sets_the_flag_on_that_bucket_only(dynamo_resource):
    _setup(dynamo_resource)
    start = time.time() - 3600
    b1 = _attack(dynamo_resource, start)
    b2 = _attack(dynamo_resource, start + 60)

    TelemetryEventsTable(dynamo_resource).mark_flagged(TENANT, ATTACKER, b1)

    table = TelemetryEventsTable(dynamo_resource)
    assert table.get_bucket(f"{TENANT}#{ATTACKER}", b1)["flagged"] is True
    assert "flagged" not in table.get_bucket(f"{TENANT}#{ATTACKER}", b2)


def test_mark_flagged_never_creates_a_bucket_out_of_nothing(dynamo_resource):
    """UpdateItem creates missing items. A flag on a bucket that does not exist
    must not conjure a half-empty telemetry row into the training set."""
    _setup(dynamo_resource)
    TelemetryEventsTable(dynamo_resource).mark_flagged(TENANT, ATTACKER, 12345)
    assert TelemetryEventsTable(dynamo_resource).get_bucket(f"{TENANT}#{ATTACKER}", 12345) is None


def test_training_skips_buckets_that_were_flagged_as_attacks(dynamo_resource):
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    before = len(collect_training_vectors(dynamo_resource, TENANT))
    for k in range(3):
        bucket = _attack(dynamo_resource, start + 100 + k * 60)
        TelemetryEventsTable(dynamo_resource).mark_flagged(TENANT, ATTACKER, bucket)

    assert len(collect_training_vectors(dynamo_resource, TENANT)) == before


def test_training_skips_whitelisted_ips(dynamo_resource):
    """What docs/architecture.md always claimed and the code never did."""
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    _attack(dynamo_resource, start + 100)
    with_it = len(collect_training_vectors(dynamo_resource, TENANT))

    without = collect_training_vectors(dynamo_resource, TENANT, exclude_ips={ATTACKER})

    assert len(without) == with_it - 1


def test_retrain_reads_the_whitelist_itself(dynamo_resource):
    """The exclusion has to happen in the retrain path, not only be possible."""
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    for k in range(3):
        _attack(dynamo_resource, start + 100 + k * 60)
    WhitelistTable(dynamo_resource).put(tenant_id=TENANT, ip=ATTACKER, added_at="x")

    meta = retrain_tenant(dynamo_resource, TENANT)
    total = len(collect_training_vectors(dynamo_resource, TENANT))
    assert meta.training_samples == total - 3


def _three_attacks(resource, start: float, flag: bool, tenant: str = TENANT) -> None:
    for k in range(3):
        bucket = _attack(resource, start + 100 + k * 60, tenant=tenant)
        if flag:
            TelemetryEventsTable(resource).mark_flagged(tenant, ATTACKER, bucket)


def test_three_flagged_attacks_do_not_teach_the_model_to_accept_the_attacker(dynamo_resource):
    """The production scenario, end to end through the real retrain path.
    Three attack buckets, each flagged when it was judged hostile - exactly
    what the telemetry route now does - then the nightly retrain. The attacker
    must still be caught."""
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    _three_attacks(dynamo_resource, start, flag=True)

    assert retrain_tenant(dynamo_resource, TENANT) is not None
    assert classify_score(_score_attacker(dynamo_resource)) != AnomalyTier.NORMAL


def test_flagging_is_what_makes_the_difference(dynamo_resource):
    """The causal claim, asserted as a comparison rather than a threshold: the
    same traffic, the same retrain, flags the only difference.

    Two TENANTS in one moto backend, never two nested mock_aws contexts - moto
    keeps one global backend, so a nested mock does not give a clean database
    and the second run silently trains on the first run's data too. That is
    exactly what an earlier version of this test did, and it produced numbers
    that disagreed with the counterfactual test below for no visible reason."""
    a, b = "t-flagged", "t-unflagged"
    _setup(dynamo_resource, a, b)
    start = time.time() - 3600
    for tenant, flag in ((a, True), (b, False)):
        _normal_baseline(dynamo_resource, start, tenant=tenant)
        _three_attacks(dynamo_resource, start, flag=flag, tenant=tenant)
        assert retrain_tenant(dynamo_resource, tenant) is not None

    flagged = _score_attacker(dynamo_resource, a)
    unflagged = _score_attacker(dynamo_resource, b)

    assert flagged < unflagged - 0.05, (
        f"flagging the attack buckets barely changed the score "
        f"(flagged {flagged:.3f} vs unflagged {unflagged:.3f})")
    assert classify_score(flagged) != AnomalyTier.NORMAL
    assert classify_score(unflagged) == AnomalyTier.NORMAL


def test_without_the_flags_the_same_three_attacks_are_learned_as_normal(dynamo_resource):
    """The counterfactual, kept as a test so that removing the defence is
    visible as the vulnerability it reopens rather than as a harmless
    simplification. Identical data, identical retrain, no flags."""
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    _three_attacks(dynamo_resource, start, flag=False)

    assert retrain_tenant(dynamo_resource, TENANT) is not None
    assert classify_score(_score_attacker(dynamo_resource)) == AnomalyTier.NORMAL


# --- Recovery: the defence prevents poisoning, it does not cure it ---------

def test_flagging_an_ip_excludes_every_bucket_it_has(dynamo_resource):
    """Once a model is poisoned it stops detecting the attack, so it stops
    flagging it, so the next retrain learns it again - the defence cannot
    recover on its own. Observed on production: an attack that scored -0.204
    on 2026-09-21 returned no decision at all two days later.

    This is the operator's lever out of that state, and it is the exact
    inverse of the whitelist: the whitelist exempts an IP from MITIGATION,
    this exempts one from TRAINING."""
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    _three_attacks(dynamo_resource, start, flag=False)
    before = len(collect_training_vectors(dynamo_resource, TENANT))

    flagged = TelemetryEventsTable(dynamo_resource).flag_all_for_ip(TENANT, ATTACKER)

    assert flagged == 3
    assert len(collect_training_vectors(dynamo_resource, TENANT)) == before - 3


def test_flagging_an_ip_leaves_other_ips_alone(dynamo_resource):
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    before = len(collect_training_vectors(dynamo_resource, TENANT))

    assert TelemetryEventsTable(dynamo_resource).flag_all_for_ip(TENANT, ATTACKER) == 0
    assert len(collect_training_vectors(dynamo_resource, TENANT)) == before


def test_a_poisoned_model_detects_again_after_the_ip_is_excluded(dynamo_resource):
    """End to end: poison the model exactly as production was poisoned, watch
    it stop detecting, exclude the IP, retrain, and see detection return."""
    _setup(dynamo_resource)
    start = time.time() - 3600
    _normal_baseline(dynamo_resource, start)
    _three_attacks(dynamo_resource, start, flag=False)
    assert retrain_tenant(dynamo_resource, TENANT) is not None
    assert classify_score(_score_attacker(dynamo_resource)) == AnomalyTier.NORMAL

    TelemetryEventsTable(dynamo_resource).flag_all_for_ip(TENANT, ATTACKER)
    assert retrain_tenant(dynamo_resource, TENANT) is not None

    assert classify_score(_score_attacker(dynamo_resource)) != AnomalyTier.NORMAL
