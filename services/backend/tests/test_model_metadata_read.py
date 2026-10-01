"""Printing a version string should not cost thirty read units.

`model_status` called ModelsTable.get() with no ProjectionExpression, which
pulls the ~238 KB model blob to read `version` and `trained_at` - about 30
RCU against a table provisioned at 2. It is the worst read-to-value ratio in
the product, and it is on a page a customer is invited to open.

The projection pattern already exists in registry.model_exists.
"""
import pytest

from services.backend.core.tables import ModelsTable, create_all_tables


@pytest.fixture
def models(dynamo_resource):
    create_all_tables(dynamo_resource)
    table = ModelsTable(dynamo_resource)
    table.put(tenant_id="t-1", stage_version="production", version="v7",
              trained_at="2026-09-23T02:00:00Z", training_samples=4096,
              contamination=0.02, score_mean=-0.05, score_std=0.011,
              model_blob="x" * 200_000)
    return table


def test_the_metadata_comes_back_without_the_blob(models):
    item = models.get_metadata("t-1")

    assert item["version"] == "v7"
    assert int(item["training_samples"]) == 4096
    assert "model_blob" not in item


def test_the_statistics_needed_to_draw_a_baseline_are_included(models):
    """The model page is meant to show the tenant the shape of their own
    normal, which needs the per-feature figures, not only the version."""
    item = models.get_metadata("t-1")

    assert "score_mean" in item
    assert "score_std" in item


def test_a_missing_model_is_none_not_an_exception(models):
    assert models.get_metadata("t-2") is None


def test_reserved_words_do_not_break_the_projection(models):
    """`stage` is a DynamoDB reserved word. Every field is aliased rather
    than guessing which ones need it - a wrong guess is a 400 at request
    time on a page a customer opened, not a failure at deploy."""
    item = models.get_metadata("t-1")

    assert item is not None
