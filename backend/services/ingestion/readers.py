"""Format readers. Each returns a raw polars DataFrame; normalization is separate."""

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
            return pl.read_csv(path, infer_schema_length=10_000, ignore_errors=True)
        case "tsv":
            return pl.read_csv(path, separator="\t", infer_schema_length=10_000, ignore_errors=True)
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


def iter_any(path: Path, fmt: str, *, batch_size: int) -> Iterator[pl.DataFrame]:
    """Yield bounded frames for large delimited imports.

    Polars' batched CSV reader keeps a multi-gigabyte upload out of process
    memory. Formats without a streaming reader retain the existing behavior;
    they are not the production large-import path.
    """
    if fmt not in {"csv", "tsv"}:
        yield read_any(path, fmt)
        return

    reader = pl.read_csv_batched(
        path,
        separator="\t" if fmt == "tsv" else ",",
        infer_schema_length=10_000,
        ignore_errors=True,
        batch_size=batch_size,
    )
    while batches := reader.next_batches(1):
        yield batches[0]
