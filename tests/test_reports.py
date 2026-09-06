"""The HTML and CSV report renderer.

Two properties matter more than the markup, and both are pinned here:

The model is built by copying a named allow-list of columns out of rows the
action already holds. It is never built by re-reading a CSV export, because the
exports open with a settings header naming the configured M3U sources, which on
a real installation is the provider's hostname.

The M3U account-name scrub is a primary redaction input, not a backstop. A
stream name frequently carries its account label, so build_model refuses when the
account list is None, which is how a caller reports that the lookup failed. An
empty list is a different thing and is allowed: an installation can have no
active M3U accounts.
"""
import html
import os
import pathlib
import re

import pytest
from Lineuparr import reports

ROWS = [
    {"Channel": "CNN", "Number": 202, "Score": 100, "Best Match": "GO: CNN [provider.tv]",
     "Secret": "should never appear"},
    {"Channel": "BBC News", "Number": 501, "Score": 95, "Best Match": "UK: BBC NEWS HD",
     "Secret": "should never appear"},
]
COLUMNS = [("Channel", "Channel"), ("Number", "Number"), ("Score", "Score"),
           ("Best Match", "Best match")]


def build(rows=None, accounts=("provider.tv",), **kw):
    kw.setdefault("settings", {"match_sensitivity": "normal", "m3u_source": "provider.tv"})
    kw.setdefault("lineup", "US_Test_lineup.json")
    kw.setdefault("version", "1.26.0000000")
    kw.setdefault("now", 1782696010.0)
    return reports.build_model("Preview stream match", COLUMNS,
                               ROWS if rows is None else rows,
                               account_names=list(accounts) if accounts is not None else None,
                               **kw)


class TestAllowList:
    def test_only_allow_listed_columns_are_copied(self):
        model = build()
        assert model["headers"] == ["Channel", "Number", "Score", "Best match"]
        assert not any("should never appear" in str(cell)
                       for row in model["entries"] for cell in row)

    def test_a_missing_key_becomes_an_empty_cell_rather_than_raising(self):
        model = build(rows=[{"Channel": "CNN"}])
        assert model["entries"] == [["CNN", "", "", ""]]

    def test_m3u_source_setting_never_reaches_the_summary(self):
        model = build()
        rendered = " ".join(f"{k} {v}" for k, v in model["summary"])
        assert "provider.tv" not in rendered

    def test_safe_settings_do_reach_the_summary(self):
        model = build()
        keys = [k for k, _ in model["summary"]]
        assert "Match sensitivity" in keys
        assert "Lineup" in keys


class TestScrub:
    def test_account_name_is_removed_from_a_stream_name(self):
        model = build()
        assert "provider.tv" not in str(model["entries"])
        assert "GO: CNN" in str(model["entries"])

    def test_scrub_is_case_insensitive(self):
        model = build(rows=[{"Channel": "CNN", "Best Match": "CNN [PROVIDER.TV]"}])
        assert "PROVIDER" not in str(model["entries"])

    def test_longest_account_name_is_matched_first(self):
        """Matching the shorter name first would leave a "-alt1" fragment."""
        model = build(rows=[{"Channel": "CNN", "Best Match": "CNN provider.tv-alt1"}],
                      accounts=("provider.tv", "provider.tv-alt1"))
        assert "alt1" not in str(model["entries"])

    @pytest.mark.parametrize("value,gone", [
        ("CNN 192.168.1.50", "192.168.1.50"),
        ("CNN fe80::1ff:fe23:4567:890a", "fe80::1ff"),
    ])
    def test_addresses_are_removed(self, value, gone):
        model = build(rows=[{"Channel": "CNN", "Best Match": value}])
        assert gone not in str(model["entries"])

    def test_a_clock_time_is_not_mistaken_for_an_address(self):
        model = build(rows=[{"Channel": "News at 20:30", "Best Match": "x"}])
        assert "20:30" in str(model["entries"])

    def test_none_account_list_refuses_to_build(self):
        with pytest.raises(ValueError, match="lookup failed"):
            build(accounts=None)

    def test_empty_account_list_is_allowed(self):
        model = build(accounts=())
        assert model["shown_rows"] == 2


class TestRowCap:
    def test_rows_are_capped_and_the_cap_is_reported(self):
        rows = [{"Channel": f"Ch {i}"} for i in range(reports.MAX_REPORT_ROWS + 25)]
        model = build(rows=rows)
        assert model["shown_rows"] == reports.MAX_REPORT_ROWS
        assert model["total_rows"] == reports.MAX_REPORT_ROWS + 25
        assert model["truncated"] is True
        notice = reports.truncation_notice(model)
        assert str(reports.MAX_REPORT_ROWS) in notice
        assert reports.EXPORTS_LOCATION in notice

    def test_no_notice_when_nothing_was_dropped(self):
        assert reports.truncation_notice(build()) is None


class TestHtml:
    def test_headers_are_sortable_and_keyboard_reachable(self):
        page = reports.render_html(build())
        for header in ["Channel", "Number", "Score", "Best match"]:
            assert f'title="Sort by {header}"' in page
        assert page.count('class="sortable"') == 4
        assert page.count('tabindex="0"') == 4
        assert page.count('aria-sort="none"') == 4

    def test_the_sort_script_is_embedded_not_linked(self):
        page = reports.render_html(build())
        assert "<script>" in page
        assert "addEventListener" in page
        assert "<script src=" not in page, (
            "an external request would not resolve from a file or an email")

    def test_every_cell_carries_a_sort_value(self):
        page = reports.render_html(build())
        assert page.count("<td data-v=") == 8

    def test_numbers_sort_as_numbers(self):
        """The script compares data-v numerically when both sides parse, so 10
        does not sort before 2."""
        assert "Number(x)" in reports._SORT_SCRIPT
        assert "isNaN" in reports._SORT_SCRIPT

    def test_every_row_is_present_in_the_markup(self):
        """A mail client that strips scripts must still show the whole table."""
        page = reports.render_html(build())
        assert page.count("<tr>") == 3  # one header row, two body rows

    def test_markup_is_escaped(self):
        model = build(rows=[{"Channel": "<script>alert(1)</script>", "Score": 1}])
        page = reports.render_html(model)
        assert "<script>alert(1)</script>" not in page
        assert html.escape("<script>alert(1)</script>", quote=True) in page

    def test_empty_result_says_so_instead_of_rendering_an_empty_table(self):
        page = reports.render_html(build(rows=[]))
        assert "no rows" in page.lower()
        assert "<tbody>" not in page

    def test_the_page_is_self_contained(self):
        """No external ASSET, which is not the same as no external address.

        The footer links to the project's own pages, which a reader clicks
        deliberately. What must never appear is anything the page FETCHES on
        its own: a stylesheet, a script, a font or an image from another host.
        This file is opened from a file path, mailed as an attachment and read
        on a television browser with no route to the internet, so a fetch would
        fail, and an image fetch would additionally disclose that the report
        had been opened.
        """
        page = reports.render_html(build())
        assert "<style>" in page
        assert "<link" not in page
        assert "url(" not in page
        assert "@import" not in page
        assert "<script src=" not in page
        for scheme in ("http://", "https://"):
            for attribute in (f'src="{scheme}', f"src='{scheme}"):
                assert attribute not in page, f"the page fetches {scheme}"

    def test_any_image_is_embedded_rather_than_fetched(self):
        """A relative path resolves against nothing when the file is opened off
        disk, and a remote address is blocked by default in most mail
        clients."""
        page = reports.render_html(build())
        for chunk in page.split("<img")[1:]:
            attrs = chunk.split(">")[0]
            assert 'src="data:image/' in attrs, f"image is not embedded: {attrs}"

    def test_a_logo_that_cannot_be_read_costs_the_image_and_nothing_else(self,
                                                                        monkeypatch):
        monkeypatch.setattr(reports, "_logo_cache", [])
        monkeypatch.setattr(reports, "LOGO_FILE", "there_is_no_such_file.png")
        assert reports.logo_data_uri() == ""
        page = reports.render_html(build())
        assert "<img" not in page
        assert "<h1>" in page, "the page still renders without its logo"

    def test_an_oversized_logo_is_not_embedded(self, monkeypatch):
        """Every byte of the image is carried in every emailed report."""
        monkeypatch.setattr(reports, "_logo_cache", [])
        monkeypatch.setattr(reports, "MAX_LOGO_BYTES", 1)
        assert reports.logo_data_uri() == ""

    def test_the_report_uses_a_report_sized_logo_file(self):
        """The report embeds its own small copy, not the full size plugin icon.

        The full size icon is 440 by 440 and about 181 KB, which is carried in
        full in every emailed report. The report displays the image at 48 by 48,
        so a copy at twice that is all the page can use.
        """
        assert reports.LOGO_FILE == "logo_report.png"
        path = (pathlib.Path(reports.__file__).resolve().parent
                / reports.LOGO_FILE)
        assert path.is_file(), f"{reports.LOGO_FILE} is missing from the package"
        assert path.stat().st_size <= 32768, (
            f"the report logo is {path.stat().st_size} bytes, which is larger "
            f"than a 48 by 48 image at twice density needs")

    def test_the_embedded_logo_stays_small_enough_to_mail(self, monkeypatch):
        """Base64 costs about a third on top, and this rides on every report."""
        monkeypatch.setattr(reports, "_logo_cache", [])
        uri = reports.logo_data_uri()
        assert uri, "the report logo did not embed at all"
        assert len(uri) <= 45000, (
            f"the embedded logo is {len(uri)} characters, so every emailed "
            f"report carries that much image")

    def test_the_timestamp_is_labelled_utc(self):
        page = reports.render_html(build())
        assert "UTC" in page


class TestCsv:
    def test_csv_has_a_header_and_one_line_per_row(self):
        text = reports.render_csv(build())
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0] == "Channel,Number,Score,Best match"
        assert len(lines) == 3

    def test_csv_carries_no_settings_header_naming_the_source(self):
        assert "provider.tv" not in reports.render_csv(build())

    @pytest.mark.parametrize("cell", ["=1+1", "+1", "-1", "@SUM(A1)"])
    def test_formula_shaped_cells_are_neutralised(self, cell):
        text = reports.render_csv(build(rows=[{"Channel": cell}]))
        assert "'" + cell in text


class TestWriteReport:
    def test_both_files_are_written_and_paths_returned(self, tmp_path):
        result = reports.write_report(build(), str(tmp_path), now=1782696010.0)
        assert result["error"] is None
        assert os.path.exists(result["html_path"])
        assert os.path.exists(result["csv_path"])
        assert reports.FILENAME_PREFIX in os.path.basename(result["html_path"])

    def test_the_filename_carries_the_run_timestamp(self, tmp_path):
        result = reports.write_report(build(), str(tmp_path), now=1782696010.0)
        assert re.search(r"\d{8}_\d{6}\.html$", result["html_path"])

    def test_a_write_failure_is_reported_not_raised(self, tmp_path):
        target = tmp_path / "blocked"
        target.write_text("not a directory")
        result = reports.write_report(build(), str(target), now=1782696010.0)
        assert result["error"]
        assert result["html_path"] is None

    def test_old_reports_are_pruned_but_recent_ones_survive(self, tmp_path):
        now = 1782696010.0
        for i in range(reports.KEEP_REPORTS + 4):
            path = tmp_path / f"{reports.FILENAME_PREFIX}old{i}.html"
            path.write_text("x")
            os.utime(path, (now - 100000 - i, now - 100000 - i))
        recent = tmp_path / f"{reports.FILENAME_PREFIX}recent.html"
        recent.write_text("x")
        os.utime(recent, (now - 60, now - 60))

        reports._prune(str(tmp_path), ".html", now=now)
        left = sorted(p.name for p in tmp_path.iterdir())
        assert recent.name in left, "a file young enough for a delivery retry must survive"
        assert len(left) <= reports.KEEP_REPORTS + 1

    def test_the_report_directory_is_not_the_web_served_logo_tree(self):
        """Dispatcharr's nginx serves /data/logos unauthenticated to the whole
        local network."""
        assert "logos" not in reports.REPORT_DIR


class TestWiring:
    """The producer, not just the renderer. An action that exports a CSV and
    forgets the report would pass every other test in this file."""

    def _tree(self):
        import ast
        import inspect

        import Lineuparr.plugin as plugin_module
        return ast, ast.parse(inspect.getsource(plugin_module))

    def test_every_csv_export_also_writes_a_report(self):
        ast, tree = self._tree()

        def calls(name):
            return [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == name]

        exports = calls("_export_csv")
        writes = calls("_write_html_report")
        assert len(exports) == 4, f"expected 4 CSV export sites, found {len(exports)}"
        assert len(writes) == len(exports), (
            f"{len(exports)} CSV exports but {len(writes)} report writes: an action "
            f"exports a file without producing the shareable report"
        )

    def test_report_columns_are_written_out_as_an_allow_list(self):
        """Each call passes literal (key, header) pairs. Deriving them from the
        CSV field list would mean a column added there starts being shared."""
        ast, tree = self._tree()
        for call in [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "_write_html_report"]:
            columns = call.args[1]
            assert isinstance(columns, ast.List), f"line {call.lineno}: not a literal list"
            assert columns.elts, f"line {call.lineno}: empty allow list"
            for pair in columns.elts:
                assert isinstance(pair, ast.Tuple) and len(pair.elts) == 2, (
                    f"line {call.lineno}: each entry must be a (row key, header) pair"
                )

    def test_the_scrub_refuses_when_the_account_lookup_fails(self):
        """_m3u_account_names returns None on failure, and the report is skipped
        rather than written unscrubbed."""
        import inspect

        import Lineuparr.plugin as plugin_module
        source = inspect.getsource(plugin_module.Plugin._write_html_report)
        assert "accounts is None" in source
        assert "return None" in source
