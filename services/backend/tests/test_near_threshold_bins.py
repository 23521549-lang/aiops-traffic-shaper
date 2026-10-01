"""The gate can only be previewed in one direction without these.

`agent.py` does `if tier == AnomalyTier.NORMAL: continue`, so a source below
4 sigma leaves no trace anywhere. Raising a gate is backtestable from
MitigationEpisode.last_z; lowering it is not, because the sources it would
newly catch were never written down - and lowering is the direction an
operator fears, because it is the direction that starts blocking real
customers.

Thirteen counters folded into the ADD that record_traffic already issues:
zero extra operations, zero extra WCU, ~104 B on an item with 905 B spare.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables

HOUR = 1758700800


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


def test_the_bins_cover_three_to_six_sigma_in_quarters():
    """Twelve bins of 0.25 plus one overflow. The floor is 3.0 on product
    grounds: ADR-006 measured 0.82% false positives at 3.5 sigma and the rate
    climbs steeply below it, so the control must not offer a gate it cannot
    honestly recommend. The ceiling matches SIGMA_CEILING in charts.py."""
    assert len(TenantHistoryTable.NEAR_BINS) == 13
    assert TenantHistoryTable.NEAR_BINS[0] == 3.0
    assert TenantHistoryTable.NEAR_BINS[-1] == 6.0


@pytest.mark.parametrize("sigma,expected", [
    (2.9, None),
    (3.0, "n300"),
    (3.2, "n300"),
    (3.25, "n325"),
    (4.0, "n400"),
    (5.99, "n575"),
    (6.0, "n600"),
    (9.9, "n600"),
])
def test_a_magnitude_lands_in_the_bin_below_it(sigma, expected):
    assert TenantHistoryTable.bin_name(sigma) == expected


def test_a_negative_magnitude_is_rejected_rather_than_binned():
    """z is negative by convention and the caller passes its magnitude. A
    sign slip would silently file every source in the lowest bin."""
    with pytest.raises(ValueError):
        TenantHistoryTable.bin_name(-4.0)


def test_only_the_bins_that_were_hit_are_written(history):
    """ADD creates a missing numeric attribute, so a bin never hit costs zero
    bytes and zero expression length."""
    history.record_traffic("t-1", HOUR, requests=10, bins={"n400": 2})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert int(row["n400"]) == 2
    assert "n325" not in row


def test_counts_accumulate_across_batches(history):
    history.record_traffic("t-1", HOUR, requests=10, bins={"n400": 2})
    history.record_traffic("t-1", HOUR, requests=10, bins={"n400": 3, "n325": 1})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert int(row["n400"]) == 5
    assert int(row["n325"]) == 1


def test_the_existing_counters_are_untouched(history):
    """"What the gate did" is a different fact from "what it would have
    done"; both are kept."""
    history.record_traffic("t-1", HOUR, requests=10, tier1=1, tier2=2,
                           bins={"n400": 1})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert int(row["tier1_decisions"]) == 1
    assert int(row["tier2_decisions"]) == 2


def test_a_batch_with_no_near_misses_writes_no_bin_attributes(history):
    """The common case. Emitting thirteen zeroes every batch would add bytes
    to the hottest write in the product for no information at all."""
    history.record_traffic("t-1", HOUR, requests=10)

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]

    assert not [k for k in row if k.startswith("n3") or k.startswith("n4")]


def test_the_row_with_every_bin_present_stays_one_write_unit(history):
    """13 bins x 8 B = 104 B on an item of ~119 B. The cap is 1024."""
    history.record_traffic("t-1", HOUR, requests=10, tier1=1, tier2=1,
                           bins={"n%d" % round(b * 100): 3
                                 for b in TenantHistoryTable.NEAR_BINS})

    row = history.query_series("t-1", HOUR, HOUR + 3600, fill=False)[0]
    size = sum(len(str(k)) + len(str(v)) for k, v in row.items())

    assert size < 1024, f"series row is {size} B; the cap is 1024"
