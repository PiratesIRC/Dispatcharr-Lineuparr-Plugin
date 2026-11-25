"""Publishes the count of reports this plugin has built, for Newsflasharr to show.

THE CONTRACT, which is Newsflasharr's to read and ours to write:

    /data/lineuparr/report_count.json   ->   {"reports_built": 42}

One key, one non-negative JSON integer. That is the whole interface. The reader
is newsflasharr/report_count.py in the Newsflasharr repository, it degrades to
showing nothing on every unexpected condition, and it reports no error when it
does. So every rule below is load bearing even though breaking one looks like
nothing at all:

  - the value must be a JSON integer, not a float, because int(2.9) is 2 and a
    silent under-report looks deliberate;
  - it must not be a JSON boolean, because bool subclasses int in Python and
    true would display as "1 built";
  - it must be zero or greater, and zero is legal;
  - the top level must be a JSON object;
  - the whole file must stay under 4096 bytes, which the reader checks before
    it reads, because it runs on an operator click inside a uWSGI worker where
    a blocking read freezes every greenlet in that process;
  - the file must be a regular file readable by user dispatch.

An absent file is not an error. It means no count is shown, which is the right
display for a plugin that publishes no counter.

WHAT THE NUMBER COUNTS. Reports whose files were confirmed written to disk, one
per successful build, whether an operator pressed the button or a scheduled job
ran it. A build that failed to write must not increment it. That distinction is
the point: write_report degrades instead of raising, so a failed publish looks
identical to a good one from the outside, and a counter that incremented anyway
would turn that failure into apparent success.

NO LOCKING, DELIBERATELY. The read, add one, write sequence here is unlocked
across several uWSGI and Celery workers, so two reports finishing in the same
instant can lose an increment, and an unlucky interleaving can even publish a
value one lower than the one already on disk: a worker that read N while two
others advanced the file to N+2 goes on to write N+1. The next increment heals
it. That is accepted. The number is a floor, not an audit, and a file lock on a
request path is a worse trade for a cosmetic number. Do not "fix" this by
adding one.

NO FSYNC, ALSO DELIBERATELY. Measured on this installation inside the
container: the write sequence below costs 0.26 ms without an fsync and 5.16 ms
with one, rising to a 29.5 ms ninetieth percentile and a 39 ms maximum while
another process is writing to the same filesystem. An fsync pays for a journal
commit, not for the twenty bytes, and /data carries the PostgreSQL data
directory on the same ext4 filesystem, so it waits behind whatever else is in
the current transaction. uWSGI here runs gevent with 400 async cores across two
workers, so that wait freezes an entire worker and every stream it is proxying,
on the path of an operator button click. The report this counts is not fsynced
either. Losing a cosmetic increment to a host crash is the cheaper failure.

NOTHING HERE EVER RAISES INTO THE REPORT PATH. Failing to publish the counter
must not fail the report that was just written.
"""
import json
import os
import posixpath
import threading

# The directory is named for the `source` string this plugin passes to
# notify_client.notify(), because that is what the reader joins onto its data
# root. It is imported rather than repeated so the two cannot drift apart: the
# plugin's installed directory name and its own runtime directory are neither
# of them guaranteed to be this string.
try:
    from .notify_bridge import SOURCE
except ImportError:  # standalone import, as the rest of this package allows
    from notify_bridge import SOURCE

DATA_ROOT = "/data"

# The single key of the single-key file.
KEY = "reports_built"

FILENAME = "report_count.json"

# The reader refuses anything larger before it parses. The documented file is
# around twenty bytes, so this only ever trips on a corrupt or hostile file,
# which is exactly when we want to refuse to build on top of it.
MAX_BYTES = 4096

# Writer and reader are the same user, so the file needs no wider access than
# this. Newsflasharr shipped a defect where an action output file was widened
# to 0644 and exposed derived provider hostnames to every other user on the
# host; its own tests, its linter and its publish audit all passed on that code.
FILE_MODE = 0o600

# The directory mode applies only on the run that creates it, and this
# directory is new: reports themselves live in the shared Newsflasharr folder, so
# nothing has created /data/lineuparr before. Whichever process gets there
# first owns it. If that is anything run as root inside the container, and the
# documented way of probing a plugin is exactly that, the dispatch workers can
# never write here again and the count silently stops moving. The deploy
# runbook in this repository's CLAUDE.md chowns this path for that reason, and
# _publish_report_count logs a failed write at warning level rather than debug
# so the condition is visible rather than merely present.
DIR_MODE = 0o700


def counter_dir(data_root=DATA_ROOT):
    """The directory the reader looks in for this plugin's counter.

    Joined with posixpath rather than os.path because this names a location
    inside the Linux container, always, whatever machine the code is being
    read or tested on. os.path.join on a Windows checkout returns a backslash
    form that is not the path the reader opens.
    """
    return posixpath.join(data_root, SOURCE)


def counter_path(data_root=DATA_ROOT):
    """The full path of the counter file."""
    return posixpath.join(counter_dir(data_root), FILENAME)


def read(path):
    """The stored count, or None when there is no usable one.

    Applies the same rules the reader applies, so a value this function returns
    is a value Newsflasharr would display. None means the file is absent,
    unreadable, oversized, malformed, or holds something that is not a
    non-negative integer. Never raises.
    """
    try:
        if not os.path.isfile(path) or os.stat(path).st_size > MAX_BYTES:
            return None
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(KEY)
    # bool first: it subclasses int, so True passes an isinstance int check.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def write(path, value):
    """Write the counter atomically. Returns True on success, False otherwise.

    The temporary file is created in the destination directory, so os.replace
    is a rename within one filesystem and a reader only ever opens a complete
    file. It is created BY os.open with the final mode rather than chmodded
    afterwards, so there is no moment at which it is more readable than
    intended, and os.replace carries that mode to the destination, narrowing an
    existing file that somehow had a wider one.

    The temporary name carries this process's id AND this thread's id rather
    than random characters. A worker killed between the create and the rename
    would otherwise leave a uniquely named file that nothing ever cleans up; a
    per-thread name is reused by that thread's next write instead.

    The thread id is not decoration. Dispatcharr's dvr Celery worker runs a
    thread pool, so two of its threads share one process id, and a name built
    from the process id alone would have them truncating each other's temporary
    file. The later close would then flush at its own offset and the rename
    could publish padding bytes followed by valid JSON.

    Never raises. A caller that cannot publish a cosmetic number must carry on.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return False
    directory = os.path.dirname(path) or "."
    tmp = os.path.join(
        directory, f"{FILENAME}.tmp-{os.getpid()}-{threading.get_ident()}")
    handle = None
    try:
        # exist_ok means the mode is applied only when this call is the one
        # that creates the directory. On an existing directory makedirs does
        # nothing at all, including nothing about its permissions.
        os.makedirs(directory, mode=DIR_MODE, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump({KEY: value}, handle)
        handle.write("\n")
        handle.close()
        handle = None
        os.replace(tmp, path)
        return True
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def increment(path):
    """Add one to the published count. Returns the new value, or None.

    An absent, unreadable or malformed file starts the count again from one
    rather than refusing forever: the number is a floor, and a counter that
    stayed permanently silent because of one bad write would be worse than one
    that under-reports. Never raises.
    """
    try:
        current = read(path)
        new_value = (current if isinstance(current, int) and current >= 0 else 0) + 1
        return new_value if write(path, new_value) else None
    except Exception:
        return None
