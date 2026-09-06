"""Validate Settings must say when a guide source it will match against is off.

Measured on a live box on 2026-08-15. `_get_filtered_epg_data` reads
`EPGData.objects.all()` and `EPGSource.objects.all()` with no filter on whether
a source is enabled, so a disabled source is still matched against. Its rows are
frozen at whatever was last downloaded, and Dispatcharr will never refresh them,
so channels get a guide that silently stops being true.

Nothing about the plugin looks unhealthy when this happens, which is why the
warning belongs in Validate Settings rather than in a log line.

`Plugin._disabled_selected_sources` is deliberately a pure function over rows so
it can be tested without a database.
"""
import pytest


@pytest.fixture
def fn():
    import Lineuparr.plugin as plugin_module
    return plugin_module.Plugin._disabled_selected_sources


def rows(*specs):
    """specs: (name, is_active, entry_count)"""
    return [
        {"name": n, "is_active": a, "entry_count": c}
        for n, a, c in specs
    ]


class TestAnExplicitSourceFilter:
    def test_a_disabled_source_that_the_filter_names_is_reported(self, fn):
        srcs = rows(("epgshare-AU", False, 354), ("guru-UK", True, 1577))
        assert fn(srcs, ["epgshare-AU"], False) == ["epgshare-AU"]

    def test_an_enabled_source_is_not_reported(self, fn):
        srcs = rows(("guru-UK", True, 1577))
        assert fn(srcs, ["guru-UK"], False) == []

    def test_a_disabled_source_the_filter_does_not_name_is_not_reported(self, fn):
        srcs = rows(("epgshare-AU", False, 354), ("guru-UK", True, 1577))
        assert fn(srcs, ["guru-UK"], False) == []

    def test_a_wildcard_token_reaches_the_disabled_source(self, fn):
        srcs = rows(("epgshare-AU", False, 354))
        assert fn(srcs, ["*-AU"], False) == ["epgshare-AU"]

    def test_matching_ignores_letter_case(self, fn):
        srcs = rows(("epgshare-AU", False, 354))
        assert fn(srcs, ["EPGSHARE-au"], False) == ["epgshare-AU"]


class TestTheAllSourcesSetting:
    def test_a_disabled_source_holding_entries_is_reported(self, fn):
        """With no filter every source is matched against, including the off ones."""
        srcs = rows(("epgshare-AU", False, 354), ("guru-UK", True, 1577))
        assert fn(srcs, [], True) == ["epgshare-AU"]

    def test_a_disabled_source_holding_nothing_is_not_worth_reporting(self, fn):
        """It contributes no candidates, so it cannot mislead anyone."""
        srcs = rows(("dead-source", False, 0))
        assert fn(srcs, [], True) == []


class TestOrderingAndShape:
    def test_several_disabled_sources_come_back_in_name_order(self, fn):
        srcs = rows(("zeta", False, 5), ("alpha", False, 5), ("mid", True, 5))
        assert fn(srcs, [], True) == ["alpha", "zeta"]

    def test_no_sources_at_all_is_not_an_error(self, fn):
        assert fn([], [], True) == []
