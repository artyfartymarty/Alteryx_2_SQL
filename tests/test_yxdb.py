import re
import struct
from decimal import Decimal
import pytest
from lib import yxdb

FIELDS = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
          {"name": "NAME", "type": "V_WString", "size": 60, "scale": None},
          {"name": "CODE", "type": "String", "size": 5, "scale": None},
          {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2},
          {"name": "RATE", "type": "Double", "size": 8, "scale": None},
          {"name": "OK", "type": "Bool", "size": 1, "scale": None},
          {"name": "DAY", "type": "Date", "size": 10, "scale": None},
          {"name": "NOTE", "type": "V_String", "size": 200, "scale": None}]
ROWS = [[1, "Zoë Ünal", "AB", Decimal("10.50"), 0.25, True, "2024-02-29", "x" * 150],
        [2, "", "", Decimal("-0.01"), -0.0, False, "2026-08-31", ""],
        [None, None, None, None, None, None, None, None]]

def test_round_trip(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    assert list(yxdb.read_records(p)) == ROWS

def test_header_without_reading_records(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    h = yxdb.read_header(p)
    assert h.file_type == "Alteryx Database File" and h.num_records == 3
    assert [f["name"] for f in h.fields] == [f["name"] for f in FIELDS]
    assert h.fields[3] == {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2}

def test_header_bytes_follow_the_documented_layout(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    raw = p.read_bytes()
    assert raw[:21] == b"Alteryx Database File" and struct.unpack_from("<q", raw, 104)[0] == 3
    units = struct.unpack_from("<I", raw, 80)[0]
    meta = raw[512:512 + units * 2]
    assert meta.endswith(b"\x00\x00") and meta[:-2].decode("utf-16-le").startswith("<RecordInfo>")

def test_many_rows_span_blocks(tmp_path):
    p = tmp_path / "big.yxdb"
    rows = [[i, f"name{i}", "C", Decimal("1.00"), 1.0, True, "2026-01-01", "n" * 190] for i in range(3000)]
    yxdb.write_yxdb(p, FIELDS, rows)
    assert yxdb.read_header(p).num_records == 3000 and sum(1 for _ in yxdb.read_records(p)) == 3000

def test_lzf_back_reference_with_overlap():
    # Brief correction (see task-2-report.md): control byte 2 is 0x40, not 0x60. Canonical LZF
    # decoding is ctrl>>5, +1 more byte if 7, then +2, giving the copy length; 0x60>>5 == 3, so
    # 0x60 decodes to a copy of 3+2=5 bytes (6 'a's total), not the 4 copies (5 'a's total) the
    # original comment and expected value describe. 0x40>>5 == 2, so 0x40 decodes to a copy of
    # 2+2=4 bytes, matching both the comment and b"aaaaa" below.
    assert yxdb.lzf_decompress(bytes([0x00, 0x61, 0x40, 0x00])) == b"aaaaa"   # literal 'a', then copy 4 from offset 1

def test_tiny_variable_value_is_readable(tmp_path):
    fields = [{"name": "S", "type": "V_String", "size": 10, "scale": None}]
    p = tmp_path / "tiny.yxdb"
    yxdb.write_yxdb(p, fields, [["zz"]])
    raw = bytearray(p.read_bytes())
    start = 512 + struct.unpack_from("<I", raw, 80)[0] * 2
    body_len = struct.unpack_from("<I", raw, start)[0] & 0x7FFFFFFF
    record = b"ab\x00\x20" + struct.pack("<I", 0)             # tiny: length 2 in the top nibble, no var data
    raw[start:start + 4 + body_len] = struct.pack("<I", len(record) | 0x80000000) + record
    p.write_bytes(bytes(raw[:start + 4 + len(record)]))
    assert list(yxdb.read_records(p)) == [["ab"]]

def test_not_a_yxdb(tmp_path):
    p = tmp_path / "x.yxdb"; p.write_bytes(b"nope" * 200)
    with pytest.raises(yxdb.YxdbError):
        yxdb.read_header(p)


# --- additional tests: behaviour the brief describes in prose but does not test ---

def test_wstring_fixed_round_trip(tmp_path):
    # WString (fixed-width, unlike V_WString) is described in the layout but not in FIELDS/ROWS.
    fields = [{"name": "W", "type": "WString", "size": 8, "scale": None}]
    rows = [["héllo"], [None]]
    p = tmp_path / "w.yxdb"
    yxdb.write_yxdb(p, fields, rows)
    assert list(yxdb.read_records(p)) == rows


def test_all_numeric_and_time_types_round_trip(tmp_path):
    # FIELDS/ROWS only exercise Int32 and Double; the brief's layout also covers Byte, Int16,
    # Int64, Float, Time and DateTime.
    fields = [{"name": "B", "type": "Byte", "size": 1, "scale": None},
              {"name": "I16", "type": "Int16", "size": 2, "scale": None},
              {"name": "I64", "type": "Int64", "size": 8, "scale": None},
              {"name": "F", "type": "Float", "size": 4, "scale": None},
              {"name": "T", "type": "Time", "size": 8, "scale": None},
              {"name": "DT", "type": "DateTime", "size": 19, "scale": None}]
    rows = [[255, -32768, 9223372036854775807, 1.5, "23:59:59", "2024-01-31 12:00:00"],
            [0, 0, 0, 0.0, "00:00:00", "2000-01-01 00:00:00"],
            [None, None, None, None, None, None]]
    p = tmp_path / "nums.yxdb"
    yxdb.write_yxdb(p, fields, rows)
    assert list(yxdb.read_records(p)) == rows


def test_variable_length_small_normal_boundary(tmp_path):
    # The writer picks the 1-byte "small" length prefix under 128 bytes and the 4-byte "normal"
    # one at/above it; check both sides of that boundary round-trip.
    fields = [{"name": "S", "type": "V_String", "size": 200, "scale": None}]
    rows = [["x" * 127], ["x" * 128]]
    p = tmp_path / "boundary.yxdb"
    yxdb.write_yxdb(p, fields, rows)
    assert list(yxdb.read_records(p)) == rows


def test_header_omits_size_attribute_for_fixed_size_types(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    h = yxdb.read_header(p)
    # read_header still reports the implied size for these types (contract: every field is
    # exactly {"name", "type", "size", "scale"})...
    assert h.fields[0] == {"name": "ID", "type": "Int32", "size": 4, "scale": None}
    assert h.fields[5] == {"name": "OK", "type": "Bool", "size": 1, "scale": None}
    assert h.fields[6] == {"name": "DAY", "type": "Date", "size": 10, "scale": None}
    # ...even though the XML on disk has no size= attribute for that field.
    tag = re.search(r'<Field\b[^>]*name="ID"[^>]*/>', h.meta_xml).group()
    assert "size=" not in tag


def test_lzf_literal_run():
    assert yxdb.lzf_decompress(bytes([0x02, 0x61, 0x62, 0x63])) == b"abc"  # c=2 -> 3 literals


def test_lzf_extended_length_back_reference():
    # ctrl=0xE0: top 3 bits are 7 (extended), so an extra byte is added to the length before the
    # usual +2; 7+3+2 = 12 bytes copied from offset 1 (self-reference => a run).
    data = bytes([0x00, 0x61, 0xE0, 0x03, 0x00])
    assert yxdb.lzf_decompress(data) == b"a" * 13


def test_lzf_respects_max_out():
    with pytest.raises(yxdb.YxdbError):
        yxdb.lzf_decompress(bytes([0x03, 0x61, 0x62, 0x63, 0x64]), max_out=3)
