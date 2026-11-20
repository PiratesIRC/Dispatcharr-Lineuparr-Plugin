"""Settings form layout for Dispatcharr 0.34 and later.

Dispatcharr 0.34.0 added collapsible ``section`` fields, ``multiselect`` pick
lists, ``table`` fields and a ``rows`` height for ``text`` fields. All of them
need ``manifest_version`` 1 in ``plugin.json``, and Dispatcharr 0.33 and earlier
drop a field of a type they do not know, with only a log warning. So the plugin
keeps its flat form in ``Plugin.fields``, which every version reads, and builds
the 0.34 copy here for ``Plugin.get_fields``, which only 0.34 and later call.

Two text settings are drawn differently in the 0.34 form, each under a NEW id,
because a multiselect or table needs a list value and a stored text value under
the same id would break the form:

- ``channel_profiles`` (comma-separated names) as the pick list
  ``channel_profile_list``;
- ``custom_aliases`` (a JSON object) as the table ``custom_alias_rows``, but only
  when the saved JSON can be shown as rows without losing anything.

``legacy_settings`` writes the new values back into the old text settings at the
start of every run, so every parser and validator reads one text form.

``epg_sources`` stays a text field on purpose: it accepts shell wildcards and the
order of the names sets priority, and the 0.34 pick list can neither accept a
typed value nor show an order (measured in the 0.34.1 compiled frontend: the
multiselect is searchable, not creatable).
"""

import copy
import json

# The ``info`` fields that head each group of settings in the flat form.
SECTION_PREFIX = "_sec_"

# Groups a typical user rarely opens start closed.
COLLAPSED_SECTIONS = frozenset((
    "_sec_notify",
    "_sec_advanced",
))

# Starting height, in lines, of text fields. custom_aliases keeps its text box
# only when the saved JSON cannot be shown as a table.
TEXT_ROWS = {
    "custom_aliases": 6,
}

# Id suffix of the info field that keeps a section header's explanation.
ABOUT_SUFFIX = "_about"
HELP_SUFFIX = "_help_"

PROFILE_LIST_ID = "channel_profile_list"
PROFILE_TEXT_ID = "channel_profiles"
ALIAS_ROWS_ID = "custom_alias_rows"
ALIAS_TEXT_ID = "custom_aliases"

NEW_FIELDS = {
    PROFILE_LIST_ID: {
        "label": "Channel Profiles",
        "help_text": "The channel profiles to enable the synced channels in. Pick one or more, or leave empty to leave profiles alone. The list is read when the plugin loads: after creating a profile, press the refresh control on the Plugins page to see it here.",
    },
    ALIAS_ROWS_ID: {
        "label": "Custom Channel Aliases (advanced)",
        "help_text": "Extra names a lineup channel can match under, added to the built-in aliases. Add one row per lineup channel: type the channel name exactly as the lineup spells it, and its aliases separated by commas. Leave the table empty to use the built-in aliases only.",
        "columns": {"channel": "Lineup channel", "aliases": "Aliases (comma-separated)"},
    },
}

# Dispatcharr draws help text as one plain paragraph. In the 0.34 form a long
# help text is cut to its first sentences and the rest follows as titled info
# fields right below the field. Keyed by field id: (short help, [(title, text)]).
# The flat form keeps the full text.
HELP_PARAGRAPHS = {
    "csv_retention_days": (
        "Housekeeping for the CSV files this plugin writes to /data/exports/. After each export, its own exports older than this many days are deleted. 0 keeps every file, which is the default. The file just written is never deleted, and at least one file always survives.",
        [
            ("Which files", "Only files named lineuparr_*.csv are touched, because /data/exports is shared with other plugins. A whole number of days is required; anything else keeps every file and says so in the container log. Clear CSV Exports still clears everything and ignores this setting."),
            ("Reports that name a CSV", "An HTML report which had to drop rows names its full CSV by filename, so setting this shorter than the age of the reports people still open leaves those reports pointing at a deleted file."),
        ],
    ),
}


def is_section_header(field):
    """True for an ``info`` field whose id marks the start of a settings group."""
    return (isinstance(field, dict) and field.get("type") == "info"
            and str(field.get("id", "")).startswith(SECTION_PREFIX))


def split_list(text):
    """A comma-separated setting as a list of trimmed, non-empty entries."""
    if isinstance(text, (list, tuple)):
        return [str(v).strip() for v in text if str(v).strip()]
    if text is None:
        return []
    return [part.strip() for part in str(text).split(",") if part.strip()]


def _options(names, extra=(), forbidden=","):
    """Sorted pick-list options from ``names`` plus every ``extra`` value.

    Names are deduplicated case-insensitively, keeping the first spelling seen.
    A name containing a ``forbidden`` character is left out, because the text
    form it is written back into could not hold it. An ``extra`` value is never
    left out: Dispatcharr rejects a save holding a value that is not an option.
    """
    seen = {}
    for name in names or ():
        name = str(name or "").strip()
        if name and not any(c in name for c in forbidden):
            seen.setdefault(name.casefold(), name)
    for name in extra or ():
        name = str(name or "").strip()
        if name:
            seen.setdefault(name.casefold(), name)
    return [{"label": n, "value": n} for n in sorted(seen.values(), key=str.casefold)]


def _canonical(values, names):
    """Each value spelled as the matching name (case-insensitive), else as typed."""
    by_key = {str(n).casefold(): str(n) for n in names or ()}
    return [by_key.get(v.casefold(), v) for v in values]


def rows_from_alias_json(text, clean=lambda s: (s or "").strip()):
    """The custom_aliases JSON as table rows, or None when it cannot be shown as
    rows without losing something.

    Empty text is an empty table. None means: the text is not valid JSON, is not
    an object, holds a value that is neither a string nor a list of strings, or
    holds an alias containing a comma (the table's aliases cell is
    comma-separated). The caller then keeps the text field, so the parser goes
    on reporting the problem exactly as it does today.
    """
    text = clean(text if isinstance(text, str) else "")
    if not text:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    rows = []
    for name, value in data.items():
        if isinstance(value, str):
            aliases = [value]
        elif isinstance(value, list) and all(isinstance(a, str) for a in value):
            aliases = value
        else:
            return None
        if any("," in a for a in aliases) or not str(name).strip():
            return None
        rows.append({"channel": str(name).strip(),
                     "aliases": ", ".join(a.strip() for a in aliases if a.strip())})
    return rows


def alias_json_from_rows(rows):
    """Table rows as the custom_aliases JSON text. A row with no channel name is
    dropped. A row with a name and no aliases is kept with an empty list, so the
    parser logs that it was ignored. Two rows naming the same channel are
    merged. No rows gives empty text (built-in aliases only)."""
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("channel") or "").strip()
        if not name:
            continue
        aliases = split_list(row.get("aliases"))
        merged = out.setdefault(name, [])
        merged.extend(a for a in aliases if a not in merged)
    return json.dumps(out, ensure_ascii=False) if out else ""


def legacy_settings(settings, built_ids):
    """Return a copy of ``settings`` with every new list or table value written
    back into its old text setting, so the rest of the plugin reads one form.

    Only ids in ``built_ids`` (the new fields ``get_fields`` actually drew) are
    written back. Dispatcharr keeps a stored key that is no longer in the form,
    so a table saved once and later replaced by the text fallback still has rows
    stored; they must not overwrite the text the operator now edits. Only a new
    id holding a list counts; anything else leaves the old setting alone.
    """
    out = dict(settings or {})
    built_ids = set(built_ids or ())
    if PROFILE_LIST_ID in built_ids and isinstance(out.get(PROFILE_LIST_ID), list):
        out[PROFILE_TEXT_ID] = ", ".join(split_list(out[PROFILE_LIST_ID]))
    if ALIAS_ROWS_ID in built_ids and isinstance(out.get(ALIAS_ROWS_ID), list):
        out[ALIAS_TEXT_ID] = alias_json_from_rows(out[ALIAS_ROWS_ID])
    return out


def _stored_list(settings, new_id):
    value = (settings or {}).get(new_id)
    return split_list(value) if isinstance(value, list) else []


def pick_list_fields(flat_fields, settings=None, profile_names=(), clean=None):
    """Return ``(fields, built_ids)``: the flat form with channel_profiles as a
    pick list and custom_aliases as a table (when its JSON fits), each under its
    new id, and the set of new ids built. The input is not modified.

    ``settings`` are the saved settings; a new field's default is built from the
    old text setting, so an install that never saved the new field keeps its
    values. ``clean`` strips paste artifacts from the JSON text (the plugin's
    ``_clean_json_text``).
    """
    settings = settings or {}
    clean = clean or (lambda s: (s or "").strip())
    out = []
    built = set()
    for field in flat_fields or []:
        if not isinstance(field, dict):
            out.append(field)
            continue
        fid = field.get("id")
        if fid == PROFILE_TEXT_ID:
            meta = NEW_FIELDS[PROFILE_LIST_ID]
            old_value = settings.get(PROFILE_TEXT_ID, field.get("default", ""))
            if str(old_value or "").strip() == "_none":
                old_value = ""
            default = _canonical(split_list(old_value), profile_names)
            stored = _stored_list(settings, PROFILE_LIST_ID)
            out.append({
                "id": PROFILE_LIST_ID, "label": meta["label"], "type": "multiselect",
                "default": default,
                "options": _options(profile_names, default + stored),
                "help_text": meta["help_text"],
            })
            built.add(PROFILE_LIST_ID)
            continue
        if fid == ALIAS_TEXT_ID:
            old_value = settings.get(ALIAS_TEXT_ID, field.get("default", ""))
            default = rows_from_alias_json(old_value, clean)
            if default is None:
                # The saved JSON cannot be shown as rows: keep the text box.
                out.append(copy.deepcopy(field))
                continue
            meta = NEW_FIELDS[ALIAS_ROWS_ID]
            columns = [{"id": cid, "label": label, "type": "string"}
                       for cid, label in meta["columns"].items()]
            # A table draws only its label, not its help text, so the
            # explanation goes in an info field above it and the table itself
            # has no label.
            out.append({"id": ALIAS_ROWS_ID + ABOUT_SUFFIX, "label": meta["label"],
                        "type": "info", "help_text": meta["help_text"]})
            out.append({"id": ALIAS_ROWS_ID, "label": "", "type": "table",
                        "columns": columns, "default": default})
            built.add(ALIAS_ROWS_ID)
            continue
        out.append(copy.deepcopy(field))
    return out, built


def sectioned_fields(flat_fields):
    """Return the form rearranged into collapsible sections.

    Each section header becomes a ``section`` marker with the same id, and every
    field after it, up to the next header, gets ``"section": <that id>``. A
    header's explanation moves to an ``info`` field at the top of its section,
    because a marker shows only its label. Fields before the first header stay
    ungrouped. The input is not modified.
    """
    out = []
    current = None
    for field in flat_fields or []:
        if not isinstance(field, dict):
            continue
        if is_section_header(field):
            current = field["id"]
            out.append({
                "id": current,
                "label": field.get("label") or current,
                "type": "section",
                "collapsed": current in COLLAPSED_SECTIONS,
            })
            text = field.get("help_text") or field.get("description")
            if text:
                out.append({"id": current + ABOUT_SUFFIX, "label": "", "type": "info",
                            "help_text": text, "section": current})
            continue
        item = copy.deepcopy(field)
        if current is not None:
            item["section"] = current
        if item.get("type") == "text" and item.get("id") in TEXT_ROWS:
            item["rows"] = TEXT_ROWS[item["id"]]
        out.append(item)
        out.extend(_split_help(item, current))
    return out


def _split_help(item, section):
    """Cut a long help text to its short form (in place) and return the rest as
    titled info fields that follow the field."""
    spec = HELP_PARAGRAPHS.get(item.get("id"))
    if not spec:
        return []
    short, paragraphs = spec
    # The 0.34 frontend shows help_text before description, so write the key
    # the field already uses.
    key = "help_text" if "help_text" in item or "description" not in item else "description"
    item[key] = short
    extra = []
    for number, (title, text) in enumerate(paragraphs, start=1):
        info = {"id": f"{item['id']}{HELP_SUFFIX}{number}", "label": title,
                "type": "info", "help_text": text}
        if section is not None:
            info["section"] = section
        extra.append(info)
    return extra


def form_034(flat_fields, settings=None, profile_names=(), clean=None):
    """The whole 0.34 form and the set of new ids it holds."""
    fields, built = pick_list_fields(flat_fields, settings, profile_names, clean)
    return sectioned_fields(fields), built


def status_for_dispatcharr(result):
    """A result as Dispatcharr 0.34 expects it: this plugin's internal
    ``warning`` status becomes ``partial`` (an orange toast and a yellow
    task-history badge). Called only where a result leaves the plugin, because
    internal code tests for ``warning``. Any other result is returned as is."""
    if isinstance(result, dict) and result.get("status") == "warning":
        result = dict(result)
        result["status"] = "partial"
    return result
