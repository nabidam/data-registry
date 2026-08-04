"""Dataset filters, shared by preview, search, statistics and snapshot builds."""

from pydantic import BaseModel


def _sql_str_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


class DatasetFilters(BaseModel):
    src_langs: list[str] = []
    tgt_langs: list[str] = []
    domains: list[str] = []
    source_ids: list[int] = []
    batch_ids: list[int] = []
    min_quality: float | None = None
    max_quality: float | None = None
    text_contains: str | None = None
    # Allocation is never a filter: the build context is scoped to exactly one
    # allocation, so reserved and ignored samples can never be filtered back in.

    def where_sql(self) -> str:
        """Render filters as a SQL predicate over the canonical Parquet schema."""
        clauses: list[str] = ["TRUE"]
        if self.src_langs:
            clauses.append(f"src_lang IN ({_sql_str_list(self.src_langs)})")
        if self.tgt_langs:
            clauses.append(f"tgt_lang IN ({_sql_str_list(self.tgt_langs)})")
        if self.domains:
            clauses.append(f"domain IN ({_sql_str_list(self.domains)})")
        if self.source_ids:
            clauses.append(f"source_id IN ({', '.join(str(int(i)) for i in self.source_ids)})")
        if self.batch_ids:
            clauses.append(f"batch_id IN ({', '.join(str(int(i)) for i in self.batch_ids)})")
        if self.min_quality is not None:
            clauses.append(f"quality >= {float(self.min_quality)}")
        if self.max_quality is not None:
            clauses.append(f"quality <= {float(self.max_quality)}")
        if self.text_contains:
            needle = self.text_contains.replace("'", "''")
            clauses.append(f"(source_text ILIKE '%{needle}%' OR target_text ILIKE '%{needle}%')")
        return " AND ".join(clauses)
