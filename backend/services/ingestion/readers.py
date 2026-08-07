"""Format readers. Each returns a raw polars DataFrame; normalization is separate."""

import csv as _csv
import json
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

SUPPORTED_FORMATS = ["csv", "tsv", "json", "jsonl", "tmx", "xlsx", "parquet"]

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    return {"txt": "csv", "ndjson": "jsonl", "xls": "xlsx"}.get(ext, ext)


def read_tmx(path: Path) -> pl.DataFrame:
    """Read a TMX file into (lang -> text) columns, one row per translation unit."""
    rows: list[dict[str, str]] = []
    for _, elem in ElementTree.iterparse(path, events=("end",)):
        if not elem.tag.endswith("tu"):
            continue
        row: dict[str, str] = {}
        for tuv in elem.iter():
            if not tuv.tag.endswith("tuv"):
                continue
            lang = tuv.get(_XML_LANG) or tuv.get("lang")
            seg = next((c for c in tuv.iter() if c.tag.endswith("seg")), None)
            if lang and seg is not None:
                row[lang.lower()] = "".join(seg.itertext()).strip()
        if row:
            rows.append(row)
        elem.clear()
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def read_any(path: Path, fmt: str) -> pl.DataFrame:
    match fmt:
        case "csv":
            return _read_delimited(path, separator=",")
        case "tsv":
            return _read_delimited(path, separator="\t")
        case "jsonl":
            return pl.read_ndjson(path)
        case "json":
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Accept {"data": [...]} style wrappers.
                data = next((v for v in data.values() if isinstance(v, list)), [data])
            return pl.DataFrame(data)
        case "parquet":
            return pl.read_parquet(path)
        case "xlsx":
            return pl.read_excel(path)
        case "tmx":
            return read_tmx(path)
        case _:
            raise ValueError(f"unsupported format: {fmt} (supported: {SUPPORTED_FORMATS})")


def _header_names(header: list[str]) -> list[str]:
    """Give every column a unique, non-empty name for polars."""
    names: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(header):
        base = raw if raw else f"column_{index + 1}"
        name = base
        suffix = 2
        while name in seen:
            name = f"{base}_{suffix}"
            suffix += 1
        seen.add(name)
        names.append(name)
    return names


def _frame(rows: list[list], names: list[str]) -> pl.DataFrame:
    """Build a typed frame from plain rows, padding/truncating to the header width."""
    width = len(names)
    padded = [row[:width] + [None] * (width - len(row)) for row in rows]
    return pl.DataFrame(padded, schema={name: pl.Utf8 for name in names}, orient="row")


def _read_delimited(path: Path, *, separator: str) -> pl.DataFrame:
    """Read a CSV/TSV, tolerating unescaped quotes.

    Polars' parser rejects a field that opens with a double quote and is not
    properly escaped, which is common in hand-made translation files. Those
    files still carry a header and mostly-valid rows, so fall back to Python's
    lenient csv reader instead of failing the whole import.
    """
    try:
        return pl.read_csv(
            path,
            separator=separator,
            infer_schema_length=10_000,
            ignore_errors=True,
        )
    except Exception:
        rows = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in _csv.reader(handle, delimiter=separator):
                if any(cell for cell in row):
                    rows.append([cell for cell in row])
        if not rows:
            return pl.DataFrame()
        width = max(len(row) for row in rows)
        names = _header_names(rows[0])
        # Keep any cells from records wider than the header under a numbered name.
        names.extend(f"column_{len(names) + i}" for i in range(width - len(names)))
        return _frame(rows, names)


def _iter_delimited_lenient(
    path: Path, *, separator: str, batch_size: int
) -> Iterator[pl.DataFrame]:
    """Stream a CSV/TSV in bounded frames, tolerating unescaped quotes.

    Polars' batched reader keeps a multi-gigabyte upload out of process memory
    but rejects unescaped quotes that are common in hand-made translation
    files (the same class Polars `read_csv` rejects). The Python csv reader
    stays both lenient and streaming, so the large-import path handles those
    files without materializing the corpus in RAM.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = _csv.reader(handle, delimiter=separator)
        header = next(reader, None)
        if header is None:
            return
        names = _header_names([cell for cell in header])
        chunk: list[list[str]] = []
        for row in reader:
            if not any(cell for cell in row):
                continue
            chunk.append([cell for cell in row])
            if len(chunk) >= batch_size:
                yield _frame(chunk, names)
                chunk = []
        if chunk:
            yield _frame(chunk, names)


def iter_any(path: Path, fmt: str, *, batch_size: int) -> Iterator[pl.DataFrame]:
    """Yield bounded frames for large delimited imports.

    Formats without a streaming reader retain the existing behavior; they are
    not the production large-import path.
    """
    if fmt == "csv":
        yield from _iter_delimited_lenient(path, separator=",", batch_size=batch_size)
        return
    if fmt == "tsv":
        yield from _iter_delimited_lenient(path, separator="\t", batch_size=batch_size)
        return
    yield read_any(path, fmt)
