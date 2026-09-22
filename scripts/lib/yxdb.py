"""Pure-Python reader and fixture writer for Alteryx's `.yxdb` binary file format.

Only the standard library is used. The byte layout implemented here (header, metadata XML,
raw/LZF record blocks, block index, fixed and variable record encoding) was reconstructed
from public knowledge of the yxdb format, not from Alteryx Designer -- no instance of Designer
was available while writing this module. Files produced by `write_yxdb` are verified only
against `read_header`/`read_records` in this module (round-trip tests), never against a real
Alteryx-written `.yxdb` file or against Alteryx Designer itself.

Record values follow plan contract C1: Int*/Byte -> int, Float/Double -> float, FixedDecimal ->
decimal.Decimal, Bool -> bool, everything else -> str (Date/Time/DateTime stay ISO strings),
NULL -> None.
"""
from __future__ import annotations

import os
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator

HEADER_SIZE = 512
FILE_ID = 0x00440205
MAX_BLOCK_SIZE = 262144  # a compressed block inflates to at most this many bytes; the writer
                          # also caps its raw blocks at this size.
SMALL_PAYLOAD_LIMIT = 128  # writer uses the 1-byte "small" length prefix below this size

# Fixed-size types: byte count of the value itself (excluding the trailing NULL-flag byte,
# except Bool which has no flag byte at all). The metadata XML omits `size` for these.
FIXED_TYPE_SIZES: dict[str, int] = {
    "Bool": 1,
    "Byte": 1,
    "Int16": 2,
    "Int32": 4,
    "Int64": 8,
    "Float": 4,
    "Double": 8,
    "Date": 10,
    "Time": 8,
    "DateTime": 19,
}

_INT_STRUCT = {"Byte": "<B", "Int16": "<h", "Int32": "<i", "Int64": "<q"}
_FLOAT_STRUCT = {"Float": "<f", "Double": "<d"}
_TEXT_FIXED_TYPES = {"Date", "Time", "DateTime"}

# Variable-length types: a 4-byte slot in the fixed part, payload lives in the record's blob.
VARIABLE_TYPES: frozenset[str] = frozenset({"V_String", "V_WString", "Blob", "SpatialObj"})


class YxdbError(Exception):
    """Raised for anything that isn't a well-formed .yxdb file this module can read."""


@dataclass
class YxdbHeader:
    file_type: str
    num_records: int
    fields: list[dict]  # each: {"name", "type", "size", "scale"}
    meta_xml: str


# --------------------------------------------------------------------------------------------
# LZF decompression (records are read via this when a block is LZF-compressed rather than raw)
# --------------------------------------------------------------------------------------------

def lzf_decompress(data: bytes, max_out: int = MAX_BLOCK_SIZE) -> bytes:
    """Decompresses one LZF-compressed block.

    Control byte `c`: `c < 32` copies `c + 1` literal bytes that follow. Otherwise it is a back
    reference: `len = c >> 5` (if 7, add the next byte), `len += 2`, `offset = ((c & 0x1F) << 8 |
    next byte) + 1`; copy `len` bytes one at a time from `out[-offset]` (overlap allowed, so this
    also encodes runs).
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        ctrl = data[i]
        i += 1
        if ctrl < 32:
            length = ctrl + 1
            out += data[i:i + length]
            i += length
        else:
            length = ctrl >> 5
            if length == 7:
                length += data[i]
                i += 1
            length += 2
            offset = (((ctrl & 0x1F) << 8) | data[i]) + 1
            i += 1
            start = len(out) - offset
            if start < 0:
                raise YxdbError("LZF back reference points before the start of the output")
            for k in range(length):
                out.append(out[start + k])
        if len(out) > max_out:
            raise YxdbError(f"LZF output exceeds max_out ({max_out} bytes)")
    return bytes(out)


# --------------------------------------------------------------------------------------------
# Metadata XML
# --------------------------------------------------------------------------------------------

def _build_metadata_xml(fields: list[dict]) -> str:
    root = ET.Element("RecordInfo")
    for f in fields:
        attrib = {"name": f["name"], "type": f["type"]}
        if f["type"] not in FIXED_TYPE_SIZES:
            attrib["size"] = str(f["size"])
        if f["type"] == "FixedDecimal":
            attrib["scale"] = str(f["scale"])
        attrib["source"] = ""
        ET.SubElement(root, "Field", attrib)
    return ET.tostring(root, encoding="unicode")


def _parse_fields_xml(meta_xml: str) -> list[dict]:
    try:
        root = ET.fromstring(meta_xml)
    except ET.ParseError as e:
        raise YxdbError(f"malformed RecordInfo XML: {e}") from e
    fields = []
    for el in root.findall("Field"):
        t = el.get("type")
        size_attr = el.get("size")
        size = int(size_attr) if size_attr is not None else FIXED_TYPE_SIZES.get(t)
        if size is None:
            raise YxdbError(f"field {el.get('name')!r} of type {t!r} has no size")
        scale_attr = el.get("scale")
        scale = int(scale_attr) if scale_attr is not None else None
        fields.append({"name": el.get("name"), "type": t, "size": size, "scale": scale})
    return fields


# --------------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------------

def _parse_header(f) -> dict:
    """Reads the 512-byte header plus the metadata XML. Leaves `f` positioned right after it."""
    raw = f.read(HEADER_SIZE)
    if len(raw) < HEADER_SIZE or raw[:21] != b"Alteryx Database File":
        raise YxdbError("not a .yxdb file: bad signature")
    try:
        file_type = raw[:64].split(b"\x00", 1)[0].decode("ascii")
        meta_units = struct.unpack_from("<I", raw, 80)[0]
        block_index_pos = struct.unpack_from("<q", raw, 96)[0]
        num_records = struct.unpack_from("<q", raw, 104)[0]
    except (struct.error, UnicodeDecodeError) as e:
        raise YxdbError(f"malformed .yxdb header: {e}") from e

    meta_bytes = f.read(meta_units * 2)
    if len(meta_bytes) != meta_units * 2:
        raise YxdbError("truncated .yxdb metadata")
    try:
        meta_xml = meta_bytes[:-2].decode("utf-16-le")
    except UnicodeDecodeError as e:
        raise YxdbError(f"malformed .yxdb metadata: {e}") from e

    fields = _parse_fields_xml(meta_xml)
    return {
        "file_type": file_type,
        "num_records": num_records,
        "fields": fields,
        "meta_xml": meta_xml,
        "block_index_pos": block_index_pos,
        "meta_end": HEADER_SIZE + meta_units * 2,
    }


def read_header(path: str | os.PathLike) -> YxdbHeader:
    """Reads only the 512-byte header and the metadata XML that follows it -- never the records."""
    with open(path, "rb") as f:
        info = _parse_header(f)
    return YxdbHeader(
        file_type=info["file_type"],
        num_records=info["num_records"],
        fields=info["fields"],
        meta_xml=info["meta_xml"],
    )


# --------------------------------------------------------------------------------------------
# Fixed-part field encode/decode
# --------------------------------------------------------------------------------------------

def _encode_fixed_field(buf: bytearray, field: dict, value: Any) -> None:
    t = field["type"]
    if t == "Bool":
        buf.append(2 if value is None else (1 if value else 0))
        return
    if t in _INT_STRUCT:
        size = FIXED_TYPE_SIZES[t]
        if value is None:
            buf += b"\x00" * size
            buf.append(1)
        else:
            buf += struct.pack(_INT_STRUCT[t], value)
            buf.append(0)
        return
    if t in _FLOAT_STRUCT:
        size = FIXED_TYPE_SIZES[t]
        if value is None:
            buf += b"\x00" * size
            buf.append(1)
        else:
            buf += struct.pack(_FLOAT_STRUCT[t], value)
            buf.append(0)
        return
    if t == "FixedDecimal":
        size = field["size"]
        if value is None:
            buf += b"\x00" * size
            buf.append(1)
        else:
            text = format(value, "f").encode("ascii")
            if len(text) > size:
                raise YxdbError(f"FixedDecimal value {value!r} does not fit in size {size}")
            buf += text + b"\x00" * (size - len(text))
            buf.append(0)
        return
    if t == "String":
        size = field["size"]
        if value is None:
            buf += b"\x00" * size
            buf.append(1)
        else:
            data = value.encode("latin-1")
            if len(data) > size:
                raise YxdbError(f"String value too long for size {size}")
            buf += data + b"\x00" * (size - len(data))
            buf.append(0)
        return
    if t == "WString":
        size = field["size"]
        if value is None:
            buf += b"\x00" * (2 * size)
            buf.append(1)
        else:
            data = value.encode("utf-16-le")
            if len(data) > 2 * size:
                raise YxdbError(f"WString value too long for size {size}")
            buf += data + b"\x00" * (2 * size - len(data))
            buf.append(0)
        return
    if t in _TEXT_FIXED_TYPES:  # Date, Time, DateTime
        size = FIXED_TYPE_SIZES[t]
        if value is None:
            buf += b"\x00" * size
            buf.append(1)
        else:
            data = value.encode("ascii")
            if len(data) > size:
                raise YxdbError(f"{t} value too long for size {size}")
            buf += data + b"\x00" * (size - len(data))
            buf.append(0)
        return
    raise YxdbError(f"unsupported fixed field type {t!r}")


def _rstrip_utf16_nul(raw: bytes) -> bytes:
    """Strips trailing UTF-16LE NUL *code units* (2-byte b"\\x00\\x00" pairs), not raw NUL bytes:
    a byte-level rstrip would also eat the high byte of a preceding character whenever that
    character's code point is below U+0100 (i.e. most Latin-1 text), corrupting it.
    """
    end = len(raw)
    while end >= 2 and raw[end - 2:end] == b"\x00\x00":
        end -= 2
    return raw[:end]


def _decode_fixed_field(buf, pos: int, field: dict) -> tuple[Any, int]:
    t = field["type"]
    if t == "Bool":
        b = buf[pos]
        pos += 1
        return (None if b == 2 else bool(b)), pos
    if t in _INT_STRUCT:
        size = FIXED_TYPE_SIZES[t]
        raw = buf[pos:pos + size]
        flag = buf[pos + size]
        pos += size + 1
        return (None if flag == 1 else struct.unpack(_INT_STRUCT[t], bytes(raw))[0]), pos
    if t in _FLOAT_STRUCT:
        size = FIXED_TYPE_SIZES[t]
        raw = buf[pos:pos + size]
        flag = buf[pos + size]
        pos += size + 1
        return (None if flag == 1 else struct.unpack(_FLOAT_STRUCT[t], bytes(raw))[0]), pos
    if t == "FixedDecimal":
        size = field["size"]
        raw = bytes(buf[pos:pos + size])
        flag = buf[pos + size]
        pos += size + 1
        if flag == 1:
            return None, pos
        return Decimal(raw.rstrip(b"\x00").decode("ascii")), pos
    if t == "String":
        size = field["size"]
        raw = bytes(buf[pos:pos + size])
        flag = buf[pos + size]
        pos += size + 1
        if flag == 1:
            return None, pos
        return raw.rstrip(b"\x00").decode("latin-1"), pos
    if t == "WString":
        size = field["size"]
        raw = bytes(buf[pos:pos + 2 * size])
        flag = buf[pos + 2 * size]
        pos += 2 * size + 1
        if flag == 1:
            return None, pos
        return _rstrip_utf16_nul(raw).decode("utf-16-le"), pos
    if t in _TEXT_FIXED_TYPES:
        size = FIXED_TYPE_SIZES[t]
        raw = bytes(buf[pos:pos + size])
        flag = buf[pos + size]
        pos += size + 1
        if flag == 1:
            return None, pos
        return raw.rstrip(b"\x00").decode("ascii"), pos
    raise YxdbError(f"unsupported fixed field type {t!r}")


# --------------------------------------------------------------------------------------------
# Variable-length field encode/decode (V_String, V_WString, Blob, SpatialObj)
# --------------------------------------------------------------------------------------------

def _encode_variable_bytes(field_type: str, value: Any) -> bytes:
    if field_type == "V_WString":
        return value.encode("utf-16-le")
    return value.encode("latin-1") if isinstance(value, str) else bytes(value)


def _append_variable_payload(blob: bytearray, data: bytes) -> None:
    """Writer forms only: 1-byte "small" length prefix (<128 bytes) or a 4-byte "normal" one."""
    n = len(data)
    if n < SMALL_PAYLOAD_LIMIT:
        blob.append((n << 1) | 1)
        blob += data
    else:
        blob += struct.pack("<I", n << 1)
        blob += data


def _resolve_variable_bytes(buf, slot_pos: int, v: int) -> bytes | None:
    """Returns the raw payload bytes for a variable slot, or None for NULL. Empty -> b""."""
    if v == 0:
        return b""
    if v == 1:
        return None
    if (v & 0x80000000) == 0 and (v & 0x30000000) != 0:
        length = v >> 28
        slot_bytes = struct.pack("<I", v)
        return slot_bytes[:length]
    offset = v & 0x7FFFFFFF
    payload_pos = slot_pos + offset
    first = buf[payload_pos]
    if first & 1:
        length = first >> 1
        data_start = payload_pos + 1
    else:
        length = struct.unpack_from("<I", buf, payload_pos)[0] >> 1
        data_start = payload_pos + 4
    return bytes(buf[data_start:data_start + length])


def _decode_variable_value(field_type: str, raw: bytes | None) -> Any:
    if raw is None:
        return None
    if field_type == "V_WString":
        return raw.decode("utf-16-le")
    return raw.decode("latin-1")


# --------------------------------------------------------------------------------------------
# Record encode/decode
# --------------------------------------------------------------------------------------------

def _encode_record(fields: list[dict], row: list, has_var: bool) -> bytes:
    fixed_buf = bytearray()
    blob = bytearray()
    pending: list[tuple[int, str, int | None]] = []  # (slot_pos, kind, blob_offset)

    for field, value in zip(fields, row):
        t = field["type"]
        if t in VARIABLE_TYPES:
            slot_pos = len(fixed_buf)
            fixed_buf += b"\x00\x00\x00\x00"  # placeholder, patched below
            if value is None:
                pending.append((slot_pos, "null", None))
            else:
                data = _encode_variable_bytes(t, value)
                if len(data) == 0:
                    pending.append((slot_pos, "empty", None))
                else:
                    blob_offset = len(blob)
                    _append_variable_payload(blob, data)
                    pending.append((slot_pos, "data", blob_offset))
        else:
            _encode_fixed_field(fixed_buf, field, value)

    fixed_part_size = len(fixed_buf)
    for slot_pos, kind, blob_offset in pending:
        if kind == "null":
            v = 1
        elif kind == "empty":
            v = 0
        else:
            payload_stream_pos = fixed_part_size + 4 + blob_offset
            v = payload_stream_pos - slot_pos
        struct.pack_into("<I", fixed_buf, slot_pos, v)

    if has_var:
        return bytes(fixed_buf) + struct.pack("<I", len(blob)) + bytes(blob)
    return bytes(fixed_buf)


def _decode_record(buf, pos: int, fields: list[dict], has_var: bool) -> tuple[list, int]:
    row = []
    for field in fields:
        t = field["type"]
        if t in VARIABLE_TYPES:
            slot_pos = pos
            v = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
            raw = _resolve_variable_bytes(buf, slot_pos, v)
            row.append(_decode_variable_value(t, raw))
        else:
            value, pos = _decode_fixed_field(buf, pos, field)
            row.append(value)
    if has_var:
        var_len = struct.unpack_from("<I", buf, pos)[0]
        pos += 4 + var_len
    return row, pos


# --------------------------------------------------------------------------------------------
# Public read/write
# --------------------------------------------------------------------------------------------

def read_records(path: str | os.PathLike) -> Iterator[list]:
    """Yields each record as a list of values (plan contract C1)."""
    with open(path, "rb") as f:
        info = _parse_header(f)
        f.seek(info["meta_end"])
        stream = bytearray()
        while f.tell() < info["block_index_pos"]:
            word_bytes = f.read(4)
            if len(word_bytes) < 4:
                # `block_index_pos` is a hint, not a hard bound: a file whose trailing block
                # index was stripped or shifted (e.g. a hand-edited fixture) still has all the
                # bytes `num_records` needs decoded below, so stop reading blocks at EOF instead
                # of treating a short final read as an error.
                break
            word = struct.unpack("<I", word_bytes)[0]
            if word & 0x80000000:
                chunk = f.read(word & 0x7FFFFFFF)
                stream += chunk
            else:
                compressed = f.read(word)
                stream += lzf_decompress(compressed, max_out=MAX_BLOCK_SIZE)

        fields = info["fields"]
        has_var = any(fld["type"] in VARIABLE_TYPES for fld in fields)
        pos = 0
        for _ in range(info["num_records"]):
            row, pos = _decode_record(stream, pos, fields, has_var)
            yield row


def write_yxdb(path: str | os.PathLike, fields: list[dict], rows: Iterable[list]) -> None:
    """Writes a .yxdb file: header, metadata XML, then rows as raw (uncompressed) record blocks
    of at most MAX_BLOCK_SIZE bytes each, followed by a block index. Deterministic: the creation
    date field is always written as 0.
    """
    fields = list(fields)
    has_var = any(f["type"] in VARIABLE_TYPES for f in fields)

    stream = bytearray()
    num_records = 0
    for row in rows:
        stream += _encode_record(fields, row, has_var)
        num_records += 1

    meta_xml = _build_metadata_xml(fields)
    meta_bytes = meta_xml.encode("utf-16-le") + b"\x00\x00"
    meta_units = len(meta_bytes) // 2

    header = bytearray(HEADER_SIZE)
    struct.pack_into("<64s", header, 0, b"Alteryx Database File")
    struct.pack_into("<I", header, 64, FILE_ID)
    struct.pack_into("<I", header, 68, 0)   # creation date: 0 so output is reproducible
    struct.pack_into("<I", header, 72, 0)   # flag word 1
    struct.pack_into("<I", header, 76, 0)   # flag word 2
    struct.pack_into("<I", header, 80, meta_units)
    struct.pack_into("<I", header, 84, 0)
    struct.pack_into("<q", header, 88, 0)   # spatial index position
    struct.pack_into("<q", header, 96, 0)   # record block index position: patched below
    struct.pack_into("<q", header, 104, 0)  # record count: patched below
    struct.pack_into("<I", header, 112, 1)  # compression version

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(meta_bytes)

        block_offsets = []
        for i in range(0, len(stream), MAX_BLOCK_SIZE):
            chunk = stream[i:i + MAX_BLOCK_SIZE]
            block_offsets.append(f.tell())
            f.write(struct.pack("<I", len(chunk) | 0x80000000))
            f.write(chunk)

        block_index_pos = f.tell()
        f.write(struct.pack("<I", len(block_offsets)))
        for offset in block_offsets:
            f.write(struct.pack("<q", offset))

        f.seek(96)
        f.write(struct.pack("<q", block_index_pos))
        f.seek(104)
        f.write(struct.pack("<q", num_records))
