"""The published report counter that Newsflasharr reads.

The contract is one file holding one key:

    /data/lineuparr/report_count.json   ->   {"reports_built": 42}

Newsflasharr's reader shows nothing at all, with no error anywhere, when any of
its conditions is unmet. Every one of those silent conditions is pinned below,
by re-implementing the reader's own checks in check_reader_would_accept and
asserting our written file passes them, so a change here that breaks the display
fails the suite instead of failing invisibly on the box.

The failed-build case is pinned too: a report whose file was not written must
not move the number, because write_report degrades instead of raising and a
counter that incremented anyway would make a failed publish look successful.
"""
import json
import os
import stat

import pytest

from Lineuparr import notify_bridge, report_count


# A re-implementation of newsflasharr/report_count.py's accept rules. It is a
# copy on purpose: that module lives in another repository, is deployed, and is
# not ours to import or to change. If it ever moves, this is the one place here
# that has to move with it.
def check_reader_would_accept(path):
    """Return (accepted, value_or_reason), mirroring the deployed reader."""
    if not os.path.isfile(path):
        return False, "not a regular file"
    if os.stat(path).st_size > 4096:
        return False, "over 4096 bytes"
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError) as error:
        return False, f"unreadable: {error}"
    if not isinstance(payload, dict):
        return False, "top level is not a JSON object"
    value = payload.get("reports_built")
    if isinstance(value, bool):
        return False, "value is a JSON boolean"
    if not isinstance(value, int):
        return False, "value is not a JSON integer"
    if value < 0:
        return False, "value is negative"
    return True, value


@pytest.fixture
def counter(tmp_path):
    """The counter path inside a temporary data root, directory not created."""
    return str(tmp_path / notify_bridge.SOURCE / report_count.FILENAME)


# --------------------------------------------------------------------------- #
# The path, which must match what the reader joins
# --------------------------------------------------------------------------- #

def test_directory_is_named_for_the_notify_source():
    """The reader joins its data root with the `source` on the event, so the
    directory name has to be that string and not the plugin's installed name."""
    assert report_count.counter_dir("/data") == "/data/" + notify_bridge.SOURCE
    assert notify_bridge.SOURCE == "lineuparr"


def test_counter_path_is_the_documented_one():
    assert report_count.counter_path("/data") == "/data/lineuparr/report_count.json"


def test_default_data_root_is_the_container_one():
    assert report_count.counter_path() == "/data/lineuparr/report_count.json"


# --------------------------------------------------------------------------- #
# Every condition the reader enforces silently
# --------------------------------------------------------------------------- #

def test_written_file_satisfies_every_reader_condition(counter):
    assert report_count.write(counter, 42) is True
    accepted, value = check_reader_would_accept(counter)
    assert accepted, value
    assert value == 42


def test_value_is_a_json_integer_not_a_float(counter):
    report_count.write(counter, 3)
    raw = json.loads(open(counter, encoding="utf-8").read())
    assert type(raw["reports_built"]) is int
    # The literal must not carry a decimal point either: json.load turns 3.0
    # into a float, which the reader refuses rather than truncating.
    assert "3.0" not in open(counter, encoding="utf-8").read()


def test_a_float_is_refused_rather_than_rounded(counter):
    assert report_count.write(counter, 2.9) is False
    assert not os.path.exists(counter)


def test_a_boolean_is_refused(counter):
    """bool subclasses int, so an unguarded write would store true and the
    reader would display it as "1 built"."""
    assert report_count.write(counter, True) is False
    assert report_count.write(counter, False) is False
    assert not os.path.exists(counter)


def test_a_negative_value_is_refused(counter):
    assert report_count.write(counter, -1) is False
    assert not os.path.exists(counter)


def test_zero_is_legal_and_is_read_back_as_zero(counter):
    assert report_count.write(counter, 0) is True
    accepted, value = check_reader_would_accept(counter)
    assert accepted and value == 0
    assert report_count.read(counter) == 0


def test_top_level_is_a_json_object_with_exactly_one_key(counter):
    report_count.write(counter, 7)
    payload = json.loads(open(counter, encoding="utf-8").read())
    assert isinstance(payload, dict)
    assert list(payload) == ["reports_built"]


def test_file_is_far_under_the_four_kilobyte_cap(counter):
    report_count.write(counter, 10 ** 9)
    assert os.stat(counter).st_size < 100


def test_file_is_a_regular_file_not_a_directory_or_link(counter):
    report_count.write(counter, 1)
    info = os.lstat(counter)
    assert stat.S_ISREG(info.st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_file_mode_is_0600_on_disk(counter):
    """Writer and reader are the same user. Newsflasharr shipped a defect where
    an action output file was widened to 0644 and exposed derived provider
    hostnames to every other user on the host.

    This one measures the real bits and therefore does not run on Windows,
    which is where this suite is developed. The test below asserts the same
    thing by a route that runs everywhere, because an assertion that executes
    nowhere pins nothing.
    """
    report_count.write(counter, 1)
    assert stat.S_IMODE(os.stat(counter).st_mode) == 0o600


def test_the_file_is_created_with_mode_0600_and_not_chmodded_afterwards(counter,
                                                                       monkeypatch):
    """Runs on every platform, unlike the permission-bit test above.

    Two things are asserted. The mode is 0600, so widening the constant fails
    here rather than only on a POSIX machine. And it is passed to the creating
    os.open call, so there is no moment at which the file exists with a wider
    mode. umask can only narrow what os.open is given, never widen it.
    """
    seen = []
    real_open = os.open

    def spy(path, flags, mode=0o777, **kwargs):
        seen.append((path, mode))
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(report_count.os, "open", spy)
    assert report_count.write(counter, 1) is True
    creations = [entry for entry in seen if entry[0].startswith(os.path.dirname(counter))]
    assert creations, "write did not create its file through os.open"
    assert all(mode == 0o600 for _, mode in creations), creations


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #

def test_an_absent_file_reads_as_none_and_is_not_an_error(counter):
    assert report_count.read(counter) is None


def test_a_malformed_file_reads_as_none(counter, tmp_path):
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write("{not json")
    assert report_count.read(counter) is None


def test_a_json_list_reads_as_none(counter):
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write("[42]")
    assert report_count.read(counter) is None


def test_a_boolean_on_disk_reads_as_none_not_as_one(counter):
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write('{"reports_built": true}')
    assert report_count.read(counter) is None


def test_a_float_on_disk_reads_as_none(counter):
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write('{"reports_built": 2.9}')
    assert report_count.read(counter) is None


def test_an_oversized_file_reads_as_none_without_being_parsed(counter):
    os.makedirs(os.path.dirname(counter))
    padding = " " * 5000
    open(counter, "w", encoding="utf-8").write('{"reports_built": 1}' + padding)
    assert report_count.read(counter) is None


def test_a_directory_at_the_path_reads_as_none(counter):
    os.makedirs(counter)
    assert report_count.read(counter) is None


def test_a_negative_value_on_disk_reads_as_none(counter):
    """The reader refuses a negative value, so read must refuse it too. Its
    docstring claims it applies the same rules, and nothing else checks that."""
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write('{"reports_built": -1}')
    assert report_count.read(counter) is None


def test_a_missing_key_reads_as_none_not_as_zero(counter):
    """Zero is a real value meaning the counter exists and has never moved. A
    file with no reports_built key is a different fact and must not be shown as
    a count at all."""
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write('{"something_else": 3}')
    assert report_count.read(counter) is None


def test_a_string_value_reads_as_none(counter):
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write('{"reports_built": "42"}')
    assert report_count.read(counter) is None


# --------------------------------------------------------------------------- #
# increment
# --------------------------------------------------------------------------- #

def test_increment_creates_the_directory_and_starts_at_one(counter):
    assert not os.path.isdir(os.path.dirname(counter))
    assert report_count.increment(counter) == 1
    accepted, value = check_reader_would_accept(counter)
    assert accepted and value == 1


def test_increment_adds_exactly_one_each_time(counter):
    assert [report_count.increment(counter) for _ in range(4)] == [1, 2, 3, 4]
    assert report_count.read(counter) == 4


def test_increment_recovers_from_a_corrupt_file_rather_than_refusing_forever(counter):
    os.makedirs(os.path.dirname(counter))
    open(counter, "w", encoding="utf-8").write("{garbage")
    assert report_count.increment(counter) == 1
    assert report_count.read(counter) == 1


def test_increment_returns_none_when_the_write_cannot_happen(tmp_path):
    """The destination directory cannot be created because a file sits where it
    would go. Nothing raises: a cosmetic number must not fail a report."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    assert report_count.increment(str(blocker / "report_count.json")) is None


def test_write_leaves_no_temporary_file_behind_on_failure(counter, monkeypatch):
    os.makedirs(os.path.dirname(counter))
    monkeypatch.setattr(report_count.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert report_count.write(counter, 5) is False
    assert os.listdir(os.path.dirname(counter)) == []


def test_write_replaces_in_place_so_a_reader_never_sees_a_partial_file(counter,
                                                                      monkeypatch):
    """os.replace is the only way the destination path is ever created, so the
    file at that path is always complete.

    Patched through monkeypatch rather than by assignment. report_count.os IS
    the os module, so a raw assignment rebinds os.replace for the whole
    interpreter and is not restored if an assertion fails first.
    """
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(report_count.os, "replace", spy)
    report_count.write(counter, 9)
    assert len(seen) == 1
    assert seen[0][1] == counter
    assert os.path.dirname(seen[0][0]) == os.path.dirname(counter)


def test_two_threads_writing_at_once_do_not_share_a_temporary_file(counter,
                                                                   monkeypatch):
    """Dispatcharr's dvr Celery worker runs a thread pool, so two threads there
    share one process id. A temporary name built from the process id alone
    would have them truncating each other's file, and the rename could then
    publish padding bytes followed by valid JSON.

    The barrier holds both threads inside write at the same time. Without it
    one thread could finish before the other starts, and a thread identifier is
    reused once its thread has ended, so the names could coincide for a reason
    that is not the defect being tested.
    """
    import threading

    os.makedirs(os.path.dirname(counter))
    names = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)
    real_open = os.open

    def spy(path, flags, mode=0o777, **kwargs):
        with lock:
            names.append(path)
        barrier.wait()
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(report_count.os, "open", spy)
    threads = [threading.Thread(target=report_count.write, args=(counter, n))
               for n in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(names) == 2
    assert len(set(names)) == 2, f"two live threads shared a temporary name: {names}"


# --------------------------------------------------------------------------- #
# The failed-build case, which is the whole point of the counter
# --------------------------------------------------------------------------- #

class _Logger:
    """Collects what the plugin logged, so a test can assert on it.

    Warnings are kept apart from the rest because the level is the point in one
    case: a counter that cannot be written is invisible in every other health
    signal, so it must not be logged at debug.
    """

    def __init__(self):
        self.lines = []
        self.warnings = []

    def debug(self, message):
        self.lines.append(message)

    def warning(self, message):
        self.lines.append(message)
        self.warnings.append(message)

    info = error = debug


def _publish(written, path, monkeypatch, logger=None):
    """Run the plugin's publish step against a temporary counter path."""
    from Lineuparr.plugin import Plugin
    monkeypatch.setattr(report_count, "counter_path", lambda *a, **k: path)
    return Plugin._publish_report_count(written, logger or _Logger())


def test_a_successful_build_increments_the_counter(counter, tmp_path, monkeypatch):
    report = tmp_path / "lineuparr_report_20260101_000000.html"
    report.write_text("<html></html>")
    assert _publish({"html_path": str(report), "error": None}, counter, monkeypatch) == 1
    assert report_count.read(counter) == 1


def test_a_failed_build_does_not_increment_the_counter(counter, tmp_path, monkeypatch):
    """write_report reports a failure by returning an error and no paths. It
    never raises, so a returned dict is the only signal there is.

    This case is the common one and it is caught by the absent path alone. The
    test below covers the harder case where a path IS present alongside an
    error, which is what makes the error check load bearing.
    """
    report_count.write(counter, 5)
    failed = {"html_path": None, "csv_path": None,
              "error": "could not write the report: disk full"}
    assert _publish(failed, counter, monkeypatch) is None
    assert report_count.read(counter) == 5


def test_an_error_is_honoured_even_when_a_path_and_a_file_are_present(counter,
                                                                     tmp_path,
                                                                     monkeypatch):
    """The two failure signals can disagree, so both are checked.

    write_report assigns its paths BEFORE it prunes old reports, so a failure
    inside pruning returns a populated html_path together with an error. A check
    on the path alone would count that as a clean build. This is the case that
    makes the error check non-vacuous: the file below really does exist.
    """
    report = tmp_path / "lineuparr_report_20260101_000000.html"
    report.write_text("<html></html>")
    report_count.write(counter, 5)
    both = {"html_path": str(report), "csv_path": None,
            "error": "could not write the report: pruning failed"}
    assert _publish(both, counter, monkeypatch) is None
    assert report_count.read(counter) == 5


def test_a_build_whose_file_vanished_does_not_increment_the_counter(counter,
                                                                    tmp_path,
                                                                    monkeypatch):
    """A green result proves the call returned, not that the artifact exists."""
    report_count.write(counter, 5)
    missing = {"html_path": str(tmp_path / "never_written.html"), "error": None}
    assert _publish(missing, counter, monkeypatch) is None
    assert report_count.read(counter) == 5


def test_the_counter_is_incremented_from_exactly_one_place(counter):
    """A delivery path must never increment it.

    The obvious future mistake is adding this call to the Email Report Now
    action, which sends a report that already exists and was already counted
    when it was built. Nothing else would catch that, so it is pinned
    structurally: one call site, and it sits inside _write_html_report.
    """
    import ast
    import inspect

    from Lineuparr import plugin as plugin_module

    tree = ast.parse(inspect.getsource(plugin_module))
    sites = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "_publish_report_count"]
    assert len(sites) == 1, f"expected 1 call site, found {len(sites)}"

    enclosing = [node.name for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and any(inner is sites[0] for inner in ast.walk(node))]
    assert "_write_html_report" in enclosing, (
        f"the counter is incremented from {enclosing}, not from the report "
        f"build path. Only a successful build may increment it.")


def test_publishing_never_raises_into_the_report_path(counter, tmp_path, monkeypatch):
    report = tmp_path / "lineuparr_report_20260101_000000.html"
    report.write_text("<html></html>")
    monkeypatch.setattr(report_count, "increment",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _publish({"html_path": str(report), "error": None}, counter, monkeypatch) is None


def test_a_counter_that_cannot_be_written_is_logged_at_warning(counter, tmp_path,
                                                               monkeypatch):
    """The level is the assertion, not the wording.

    A counter directory created root-owned inside the container cannot be
    written by the plugin ever again, and nothing else about the plugin looks
    unhealthy when that happens: reports still build, the action still returns
    its usual green toast. Dispatcharr logs at info, so a debug line would make
    the condition permanently invisible. The message must also name the
    directory, because the operator cannot act on it otherwise.
    """
    report = tmp_path / "lineuparr_report_20260101_000000.html"
    report.write_text("<html></html>")
    monkeypatch.setattr(report_count, "increment", lambda *a: None)
    logger = _Logger()
    assert _publish({"html_path": str(report), "error": None}, counter,
                    monkeypatch, logger) is None
    assert logger.warnings, "a counter that could not be written was not logged at warning"
    assert "lineuparr" in " ".join(logger.warnings)


def test_a_successful_build_logs_no_warning(counter, tmp_path, monkeypatch):
    """The guard above must not fire on the ordinary path."""
    report = tmp_path / "lineuparr_report_20260101_000000.html"
    report.write_text("<html></html>")
    logger = _Logger()
    assert _publish({"html_path": str(report), "error": None}, counter,
                    monkeypatch, logger) == 1
    assert logger.warnings == []
