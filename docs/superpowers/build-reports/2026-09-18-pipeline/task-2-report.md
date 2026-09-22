# Task 2 report: `scripts/lib/yxdb.py`

## What I implemented

`scripts/lib/yxdb.py`: a pure-Python (standard library only) reader and fixture writer for
Alteryx's `.yxdb` binary format, per the byte layout in `task-2-brief.md`.

- `YxdbHeader` dataclass (`file_type`, `num_records`, `fields`, `meta_xml`); each field dict is
  exactly `{"name", "type", "size", "scale"}`, with `size` filled in from `FIXED_TYPE_SIZES` when
  the metadata XML omits it (fixed-size types), and `scale` `None` except on `FixedDecimal`.
- `read_header(path)`: reads only the 512-byte header plus the metadata XML that follows it,
  never touching the record blocks.
- `read_records(path)`: a generator yielding each record as a `list` of contract-C1-typed Python
  values (`int`/`float`/`Decimal`/`bool`/`str`/`None`). Reads record blocks (raw or
  LZF-compressed) into one continuous in-memory byte stream, then decodes `num_records` records
  from it sequentially, resolving variable-length fields (`V_String`, `V_WString`, `Blob`,
  `SpatialObj`) via their 4-byte slot (empty / NULL / tiny / small / normal forms, all four
  decoded; the writer only ever emits empty, NULL, small and normal).
- `write_yxdb(path, fields, rows)`: writes the 512-byte header, UTF-16LE metadata XML, all rows
  encoded and concatenated into one byte stream, split into raw (uncompressed) blocks of at most
  262,144 bytes each, a trailing block index, then patches the header's record-block-index
  position and record count. Creation date is always written as 0 for reproducible output.
- `lzf_decompress(data, max_out=262144)`: decodes LZF-compressed blocks (literal runs and back
  references, including the extended-length and self-overlapping-copy cases), raising
  `YxdbError` if decoded output would exceed `max_out`. The writer never produces LZF-compressed
  blocks (only raw ones, per the brief), so this function is only exercised by files this module
  reads that it did not itself write, and by its own unit tests.
- `YxdbError`: raised for anything that isn't a well-formed `.yxdb` file this module can read
  (bad signature, truncated/malformed header, metadata or XML).

**Honesty note (also in the module docstring):** this layout was reconstructed from public
knowledge of the yxdb format, not from Alteryx Designer — no instance of Designer was available.
Files this module writes are verified only against this module's own reader (round-trip tests),
never against a real Alteryx-written `.yxdb` file or Designer itself. Per the brief, I skipped
the optional `pip install yxdb` cross-check (no network installs).

## Brief corrections

**`test_lzf_back_reference_with_overlap`'s hardcoded control byte was off by one hex digit.**

The test as given in the brief:
```python
def test_lzf_back_reference_with_overlap():
    assert yxdb.lzf_decompress(bytes([0x00, 0x61, 0x60, 0x00])) == b"aaaaa"   # literal 'a', then copy 4 from offset 1
```

Hand computation of the second control byte, `0x60 = 0b01100000 = 96`, under the LZF algorithm
the brief itself specifies ("`len = c >> 5` (if 7, add the next byte), then `len += 2`"):
- `len = 0x60 >> 5 = 3` (not 7, so no extension byte)
- `len += 2` → `len = 5`
- `offset = ((0x60 & 0x1F) << 8 | 0x00) + 1 = 1`

That's a copy of **5** bytes from offset 1 (a run), giving `"a" + "a"*5` = `"aaaaaa"` (6 `a`s) —
not the `"aaaaa"` (5 `a`s) the test asserts, and not the "copy 4" the test's own comment states.

Solving backwards from the comment's stated intent (copy count 4, offset 1): `len_raw + 2 = 4` →
`len_raw = 2` → control byte top 3 bits = `2` → with the same low 5 bits (`0`) that's
`ctrl = (2 << 5) | 0 = 0x40`, not `0x60`. Re-deriving forward with `0x40` confirms it:
`len = 0x40 >> 5 = 2`, `+2 = 4`, `offset = 1` → copy 4 bytes from offset 1 → `"a" + "aaaa"` =
`"aaaaa"` (5 `a`s), matching both the comment and the asserted `b"aaaaa"`.

This is consistent with the standard/canonical LZF decompression algorithm (used by liblzf,
redis, and other reimplementations): a back-reference control byte's top 3 bits give the match
length minus 2 (with the "if 7, add a byte" escape for longer matches), and the low 5 bits plus
the following byte give the offset minus 1. I implemented `lzf_decompress` per that canonical
algorithm (matching the brief's own prose description) and corrected the test's input byte from
`0x60` to `0x40` — the single-byte fix that makes the byte literal consistent with both the
test's own comment and its asserted output, without touching the algorithm description, the
comment, or the expected value. This is documented inline in the corrected test as well.

## What I tested and results

Wrote `tests/test_yxdb.py` with the 7 tests from the brief verbatim (with the one-byte
correction above), then added tests for prose-described behavior the brief's own tests don't
exercise:
- `test_wstring_fixed_round_trip` — fixed-width `WString` (only `V_WString` appears in
  `FIELDS`/`ROWS`), including a NUL-padding edge case (see below).
- `test_all_numeric_and_time_types_round_trip` — `Byte`, `Int16`, `Int64`, `Float`, `Time`,
  `DateTime` (the brief's `FIELDS` only covers `Int32` and `Double` among the numeric/time
  types), including their NULL forms and boundary values (`Byte` 255, `Int16` -32768, `Int64`
  max).
- `test_variable_length_small_normal_boundary` — the writer's small-vs-normal payload encoding
  threshold at exactly 128 bytes (127 bytes → small form, 128 bytes → normal form).
- `test_header_omits_size_attribute_for_fixed_size_types` — confirms both that `read_header`
  fills in the implied size for fixed-size types (`ID`/`Int32`→4, `OK`/`Bool`→1, `DAY`/`Date`→10)
  *and* that the on-disk XML genuinely has no `size=` attribute for those fields (regex-checked
  against the raw `Field` tag), not just that the two happen to agree by coincidence.
- `test_lzf_literal_run` — a pure literal-copy control byte (no back reference).
- `test_lzf_extended_length_back_reference` — the `len == 7` escape (extra length byte).
- `test_lzf_respects_max_out` — `lzf_decompress` raises `YxdbError` when decoded output would
  exceed `max_out`.

### RED
```
.venv/Scripts/python.exe -m pytest tests/test_yxdb.py
```
```
ImportError while importing test module '...\tests\test_yxdb.py'.
E   ImportError: cannot import name 'yxdb' from 'lib' (...\scripts\lib\__init__.py)
```
Expected: `lib/yxdb.py` didn't exist yet.

### A real bug caught by my own added test (fixed before GREEN)
`test_wstring_fixed_round_trip` (added for fixed `WString`, not covered by the brief's tests)
failed with `UnicodeDecodeError: 'utf-16-le' codec can't decode byte 0x6f ... truncated data`.
Cause: my first `WString` decode used `raw.rstrip(b"\x00")` — a byte-level strip. For a string
like `"héllo"` whose last character (`'o'` = U+006F) has a zero high byte in UTF-16LE, the
byte-level rstrip ate that character's own `0x00` high byte along with the trailing NUL padding,
leaving an odd-length byte string that can't decode. Fixed with `_rstrip_utf16_nul`, which strips
trailing NUL **code units** (2-byte pairs) instead of raw bytes. `String`/`FixedDecimal`/
`Date`/`Time`/`DateTime` don't have this problem since they're single-byte-per-char encodings.

### GREEN
```
.venv/Scripts/python.exe -m pytest tests/test_yxdb.py -v
```
```
collected 14 items
tests\test_yxdb.py ..............                                        [100%]
14 passed in 0.08s
```

Full suite (foundations + yxdb):
```
.venv/Scripts/python.exe -m pytest
```
```
30 passed in 0.15s
```
Output is pristine (no warnings).

## Files changed

- `scripts/lib/yxdb.py` (new)
- `tests/test_yxdb.py` (new)

## Self-review findings

- Fixed the `WString` NUL-stripping bug described above (caught by my own added test, not one
  from the brief).
- Corrected one hex literal in the brief's LZF test (documented above and inline in the test).
- Simplified `YxdbHeader` to have no default field values, matching the brief's interface line
  exactly (an earlier draft added defaults for convenience; removed as unnecessary embellishment
  since every caller always supplies all four fields).
- `Blob`/`SpatialObj` (variable-length) aren't exercised by any test (not present in the brief's
  `FIELDS`, and not needed by later tasks per the plan as I understand it). I implemented them
  identically to `V_String` (latin-1 passthrough decode/encode of raw bytes to `str`, per
  contract C1's "everything else → str"), consistent with the pattern for every other type, but
  flagging this as untested since the brief gives no test data for it.
- `read_records`'s block-reading loop treats a short/absent final read as end-of-blocks (`break`)
  rather than an error, once it has already read up to `block_index_pos` or hit EOF. This was
  required to pass `test_tiny_variable_value_is_readable`, which truncates the file right after
  its hand-edited record block, leaving the header's `block_index_pos` pointing past the new
  end of file; `num_records` (not `block_index_pos`) is what actually bounds how many records
  get decoded.

## Concerns

None blocking. The one open question is the untested `Blob`/`SpatialObj` encoding choice noted
above — reasonable and internally consistent, but nothing in this task's tests confirms it
matches what a later task will expect if `Blob` ends up mapped differently.
