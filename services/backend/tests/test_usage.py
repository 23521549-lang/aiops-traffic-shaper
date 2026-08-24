from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import _today, get_usage_report, record_invocation


def test_record_invocation_increments_counter(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_invocation(dynamo_resource, estimated_gb_seconds=0.05)
    record_invocation(dynamo_resource, estimated_gb_seconds=0.05)
    report = get_usage_report(dynamo_resource, date=None)  # None = today
    assert report.total_requests == 2
    assert round(report.estimated_gb_seconds, 2) == 0.10


def test_ceiling_warning_flips_true_near_daily_share(dynamo_resource):
    # The plan's original version of this test looped ~28,000 individual
    # record_invocation() calls to reach 85% of the daily ceiling — each
    # one a real moto UpdateItem call, which took long enough to time out
    # a 60s test run. Seeding the counter directly in one write tests the
    # exact same get_usage_report() threshold logic without paying that
    # cost — found while actually running this stage's tests.
    create_all_tables(dynamo_resource)
    daily_share = 1_000_000 / 30
    UsageCountersTable(dynamo_resource).put(
        date=_today(), total_requests=int(daily_share * 0.85), estimated_gb_seconds=0.0,
    )
    report = get_usage_report(dynamo_resource, date=None)
    assert report.ceiling_warning is True


def test_ceiling_warning_false_when_low_usage(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_invocation(dynamo_resource, estimated_gb_seconds=0.0)
    report = get_usage_report(dynamo_resource, date=None)
    assert report.ceiling_warning is False


def test_get_usage_report_for_empty_day_returns_zeros(dynamo_resource):
    create_all_tables(dynamo_resource)
    report = get_usage_report(dynamo_resource, date="2020-01-01")
    assert report.total_requests == 0
    assert report.estimated_gb_seconds == 0.0
    assert report.ceiling_warning is False
