"""One `<Configuration>` element → the `config` dict dag-contract §4 fixes, and the credential
scrubbing of §6.

Every parser here is total: a missing element or attribute becomes `null`, never an exception and
never a guess. Attribute spellings are Alteryx's, including its typos (`Delimeter`,
`CaseInsensitve`), because the file is what it is.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Callable

# What a connection string is replaced by. `scrub` writes the plain form (it is read by people and
# by `config`); `scrub_xml` writes the escaped form so a scrubbed XML document stays well-formed
# and parses back to exactly the plain token.
TOKEN = "<scrubbed:{alias}>"
XML_TOKEN = "&lt;scrubbed:{alias}&gt;"

# `odbc:DSN=PROD_FIN;UID=svc;PWD=…`, `oledb:…` or `aka:PROD_ORACLE`, up to the `|||` separator.
_CONN_RE = re.compile(r"(?i)\b(odbc|oledb|aka):([^|<>\"\n]*)")
_SCRUBBED_RE = re.compile(r"(?i)(?:<|&lt;)scrubbed:([a-z0-9_]+)(?:>|&gt;)")
_PASSWORDS_RE = re.compile(r"(?is)<Passwords>.*?</Passwords>")
_CRED_KV_RE = re.compile(r"(?i)\b(?:PWD|Password|UID|User\s*ID)\s*=[^;|<>\"\n]*;?")
_DSN_RE = re.compile(r"(?i)\b(?:DSN|Data\s*Source|Server)\s*=\s*([^;]*)")

# `<File FileFormat="…">` codes (dag-contract §4).
FILE_FORMATS: dict[str, str] = {"19": "yxdb", "0": "csv", "25": "xlsx", "23": "db"}

# `<OutputOption>` text → write mode. Anything else stays `None`: inventing a write mode is how a
# migration turns an update into a truncate.
WRITE_MODES: dict[str, str] = {
    "overwrite": "overwrite",
    "create new table": "overwrite",
    "overwrite table (drop)": "overwrite",
    "append existing": "append",
    "update; insert if new": "update_insert",
    "delete data & append": "truncate_append",
}


# --- scrubbing (dag-contract §6) ---

def scrub(text: str) -> tuple[str, list[str]]:
    """Remove credentials from `text`; return it and the aliases whose connection string went.

    A connection string becomes `<scrubbed:alias>`, a `<Passwords>` block is emptied, and any
    stray `PWD=`, `Password=`, `UID=` or `User ID=` pair is dropped.
    """
    return _scrub(text, TOKEN)


def scrub_xml(xml_text: str) -> tuple[str, list[str]]:
    """`scrub` for text that must stay well-formed XML (a `raw_config`, a whole source file).

    The token is written escaped, so re-parsing the result yields the plain `<scrubbed:alias>`
    that `config["source"]` already holds and a second parse is a no-op.
    """
    return _scrub(xml_text, XML_TOKEN)


def _scrub(text: str, token: str) -> tuple[str, list[str]]:
    aliases: list[str] = []

    def replace(match: re.Match) -> str:
        alias = alias_of(match.group(1), match.group(2))
        if alias not in aliases:
            aliases.append(alias)
        return token.format(alias=alias)

    out = _PASSWORDS_RE.sub("<Passwords />", text)
    out = _CONN_RE.sub(replace, out)
    out = _CRED_KV_RE.sub("", out)
    for alias in _SCRUBBED_RE.findall(out):  # already scrubbed on an earlier run
        if alias.lower() not in aliases:
            aliases.append(alias.lower())
    return out, aliases


def alias_of(scheme: str, body: str) -> str:
    """The lower-cased DSN or aka name a connection string points at."""
    if scheme.lower() == "aka":
        name = body.strip().split(";")[0]
    else:
        found = _DSN_RE.search(body)
        name = found.group(1) if found else body.split(";")[0]
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")


def split_source(raw: str) -> tuple[str | None, str | None]:
    """A `<File>` value → (alias, tail), or (None, None) for a plain file path.

    The tail is the query (inputs) or table (outputs) after the `|||` separator. A value that was
    already scrubbed reads back the same way, so parsing a scrubbed source is idempotent.
    """
    for pattern in (_CONN_RE, _SCRUBBED_RE):
        match = pattern.match(raw)
        if match:
            alias = alias_of(*match.groups()) if pattern is _CONN_RE else match.group(1).lower()
            tail = raw[match.end():].lstrip()
            if tail.startswith("|||"):
                tail = tail[3:]
            return alias, tail.strip() or None
    return None, None


# --- small XML readers ---

def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _find(config: ET.Element, path: str) -> str | None:
    return _text(config.find(path))


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes")


def _flag(config: ET.Element, path: str, default: bool = False) -> bool:
    """`<UpdateField value="False"/>` → False."""
    element = config.find(path)
    return default if element is None else _bool(element.get("value"), default)


def _switch(config: ET.Element, path: str) -> bool:
    """True for either spelling of a boolean: `<HeaderRow>True</HeaderRow>` or `value="True"`."""
    return _bool(_find(config, path)) or _flag(config, path)


def _field_names(config: ET.Element, path: str) -> list[str]:
    """`<Field field="ACCT"/>` or `<Field name="ACCT"/>` under `path`."""
    return [f.get("field") or f.get("name") for f in config.findall(path)
            if (f.get("field") or f.get("name"))]


def _select_fields(select_fields: ET.Element | None) -> dict:
    """`<SelectFields>` → {"fields": [...], "unknown_selected": bool} (dag-contract §4 select)."""
    fields, unknown_selected = [], False
    for field in (select_fields.findall("SelectField") if select_fields is not None else []):
        name = field.get("field")
        if name == "*Unknown":
            unknown_selected = _bool(field.get("selected"))
            continue
        fields.append({"name": name, "selected": _bool(field.get("selected")),
                       "rename": field.get("rename") or None, "type": field.get("type") or None,
                       "size": _int(field.get("size"))})
    return {"fields": fields, "unknown_selected": unknown_selected}


# --- per-type configuration parsers ---

def _input(config: ET.Element) -> dict:
    file_element = config.find("File")
    raw = (file_element.text or "").strip() if file_element is not None else ""
    parsed = {"source": raw or None,
              "format": FILE_FORMATS.get(file_element.get("FileFormat")) if file_element is not None else None,
              "query": None, "alias": None, "csv": None,
              "record_limit": _int(file_element.get("RecordLimit")) if file_element is not None else None}
    alias, tail = split_source(raw)
    if alias:
        parsed.update(source=TOKEN.format(alias=alias), format="db", alias=alias,
                      query=scrub(tail)[0] if tail else None)
    elif parsed["format"] == "csv":
        parsed["csv"] = {"delimiter": _find(config, ".//Delimeter"),
                         "header": _switch(config, ".//HeaderRow"),
                         "codepage": _find(config, ".//CodePage")}
    return parsed


def _output(config: ET.Element) -> dict:
    file_element = config.find("File")
    raw = (file_element.text or "").strip() if file_element is not None else ""
    option = _find(config, ".//OutputOption")
    parsed = {"source": raw or None,
              "format": FILE_FORMATS.get(file_element.get("FileFormat")) if file_element is not None else None,
              "alias": None, "table": None,
              "write_mode": "overwrite" if option is None else WRITE_MODES.get(option.lower()),
              "keys": _field_names(config, ".//UpdateKeys/Field"),
              "pre_sql": scrub(_find(config, ".//PreSQL") or "")[0] or None,
              "post_sql": scrub(_find(config, ".//PostSQL") or "")[0] or None}
    alias, tail = split_source(raw)
    if alias:
        parsed.update(source=TOKEN.format(alias=alias), format="db", alias=alias, table=tail)
    return parsed


def _select(config: ET.Element) -> dict:
    return _select_fields(config.find("SelectFields"))


def _filter(config: ET.Element) -> dict:
    return {"expression": _find(config, "Expression")}


def _formula(config: ET.Element) -> dict:
    return {"formulas": [{"field": f.get("field"), "expression": f.get("expression"),
                          "type": f.get("type") or None, "size": _int(f.get("size"))}
                         for f in config.findall("FormulaFields/FormulaField")]}


def _join(config: ET.Element) -> dict:
    sides = {}
    for info in config.findall("JoinInfo"):
        sides[(info.get("connection") or "").lower()] = _field_names(info, "Field")
    left, right = sides.get("left", []), sides.get("right", [])
    keys = [{"left": l, "right": r} for l, r in zip(left, right)]
    select = config.find("SelectConfiguration/Configuration[@outputConnection='Join']/SelectFields")
    return {"keys": keys, "select": _select_fields(select)}


def _union(config: ET.Element) -> dict:
    mode = (_find(config, "Mode") or "").lower()
    return {"mode": {"byname": "name", "bypos": "position"}.get(mode)}


def _summarize(config: ET.Element) -> dict:
    return {"fields": [{"field": f.get("field"), "action": f.get("action"),
                        "rename": f.get("rename") or None, "separator": f.get("sep")}
                       for f in config.findall("SummarizeFields/SummarizeField")]}


def _sort(config: ET.Element) -> dict:
    orders = {"ascending": "asc", "descending": "desc"}
    return {"fields": [{"field": f.get("field"), "order": orders.get((f.get("order") or "").lower())}
                       for f in config.findall("SortInfo/Field")]}


def _unique(config: ET.Element) -> dict:
    return {"fields": _field_names(config, "UniqueFields/Field")}


def _sample(config: ET.Element) -> dict:
    modes = {"first": "first", "last": "last", "skip": "skip", "oneinn": "one_in_n", "one_in_n": "one_in_n"}
    mode = (_find(config, "Mode") or "").lower().replace(" ", "")
    return {"mode": modes.get(mode), "n": _int(_find(config, "N")),
            "group_by": _field_names(config, "GroupFields/Field")}


def _record_id(config: ET.Element) -> dict:
    position = _find(config, "Position")
    return {"field": _find(config, "FieldName"), "start": _int(_find(config, "StartValue")),
            "type": _find(config, "FieldType"),
            "position": {None: "first", "0": "first", "1": "last"}.get(position)}


def _multi_row_formula(config: ET.Element) -> dict:
    update_existing = _flag(config, "UpdateField")
    other_rows = (_find(config, "OtherRows") or "").lower()
    num_rows = config.find("NumRows")
    return {"field": _find(config, "UpdateField_Name") if update_existing else _find(config, "CreateField_Name"),
            "update_existing": update_existing,
            "type": _find(config, "CreateField_Type"), "size": _int(_find(config, "CreateField_Size")),
            "num_rows": _int(num_rows.get("value")) if num_rows is not None else None,
            "expression": _find(config, "Expression"),
            "group_by": _field_names(config, "GroupByFields/Field"),
            "unknown_rows": {"null": "null", "0": "zero", "nearest": "nearest"}.get(other_rows)}


def _cross_tab(config: ET.Element) -> dict:
    header = config.find("HeaderField")
    data = config.find("DataField")
    return {"group_by": _field_names(config, "GroupFields/Field"),
            "header_field": header.get("field") if header is not None else None,
            "data_field": data.get("field") if data is not None else None,
            "methods": [m.get("method") for m in config.findall("Methods/Method")]}


def _transpose(config: ET.Element) -> dict:
    return {"key_fields": _field_names(config, "KeyFields/Field"),
            "data_fields": [f.get("field") for f in config.findall("DataFields/Field")
                            if _bool(f.get("selected"), True)]}


def _regex(config: ET.Element) -> dict:
    expression = config.find("RegExExpression")
    replace = config.find("Replace")
    return {"field": _find(config, "Field"),
            "expression": expression.get("value") if expression is not None else None,
            "case_insensitive": _flag(config, "CaseInsensitve"),  # Alteryx really spells it this way
            "method": (_find(config, "Method") or "").lower() or None,
            "replace": replace.get("expression") if replace is not None else None,
            "copy_unmatched": _flag(replace, "CopyUnmatched") if replace is not None else False,
            "output_fields": [{"name": f.get("field"), "type": f.get("type") or None,
                               "size": _int(f.get("size"))}
                              for f in config.findall("ParseFields/Field")],
            "match_field": _find(config, "Match/Field")}


def _datetime(config: ET.Element) -> dict:
    return {"direction": "to_string" if _flag(config, "IsFrom") else "to_datetime",
            "field": _find(config, "InputFieldName"), "format": _find(config, "Format"),
            "out_field": _find(config, "OutputFieldName")}


def _macro_values(config: ET.Element) -> dict[str, str]:
    return {v.get("name"): (v.text or "").strip() for v in config.findall("Value") if v.get("name")}


# Data Cleansing is the `Cleanse.yxmc` macro; its checkboxes are named by control id.
_CLEANSE_FLAGS = {
    "Check Box (84)": "replace_null_strings_blank",
    "Check Box (117)": "replace_null_numeric_zero",
    "Check Box (15)": "trim_whitespace",
    "Check Box (109)": "remove_tabs_linebreaks_dupspaces",
    "Check Box (122)": "remove_all_whitespace",
    "Check Box (53)": "remove_letters",
    "Check Box (58)": "remove_numbers",
    "Check Box (70)": "remove_punctuation",
}


def _data_cleansing(config: ET.Element) -> dict:
    values = _macro_values(config)
    listed = values.get("List Box (11)", "")
    fields = re.findall(r'"([^"]*)"', listed) or [f.strip() for f in listed.split(",") if f.strip()]
    parsed = {"fields": fields}
    parsed.update({key: _bool(values.get(name)) for name, key in _CLEANSE_FLAGS.items()})
    parsed["modify_case"] = (values.get("Drop Down (81)") or "").lower() or None \
        if _bool(values.get("Check Box (77)")) else None
    return parsed


def _macro(config: ET.Element) -> dict:
    return {"values": _macro_values(config)}


def _interface(config: ET.Element) -> dict:
    """A question tool: what a macro exposes as a parameter (dag-contract §4 macro)."""
    return {"name": _find(config, "Question/Name"), "type": _find(config, "Question/Type"),
            "default": _find(config, "Default")}


def _run_command(config: ET.Element) -> dict:
    return {"command": _find(config, "Command"), "args": _find(config, "CommandArguments")}


def _python(config: ET.Element) -> dict:
    """This repo's Python tool keeps its code in <Script>; real Alteryx stores a notebook JSON
    (dag-contract §4 says how to extend this when migrating such workflows)."""
    return {"script": (_find(config, "Script") or "").strip()}


PARSERS: dict[str, Callable[[ET.Element], dict]] = {
    "input": _input, "output": _output, "select": _select, "filter": _filter, "formula": _formula,
    "join": _join, "union": _union, "summarize": _summarize, "sort": _sort, "unique": _unique,
    "sample": _sample, "record_id": _record_id, "multi_row_formula": _multi_row_formula,
    "cross_tab": _cross_tab, "transpose": _transpose, "regex": _regex, "datetime": _datetime,
    "data_cleansing": _data_cleansing, "macro": _macro, "interface": _interface,
    "run_command": _run_command, "python": _python,
}


def parse_config(tool_type: str, configuration: ET.Element | None) -> dict:
    """The `config` dict for one tool. Types with no config of their own (and `unknown`) get `{}`;
    their XML survives in the node's `raw_config`."""
    parser = PARSERS.get(tool_type)
    if parser is None:
        return {}
    return parser(configuration if configuration is not None else ET.Element("Configuration"))
