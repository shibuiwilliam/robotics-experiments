"""ベクトル索引（双対表現の片翼）— LanceDB、IRIキー。"""

from __future__ import annotations

from pathlib import Path

import lancedb
import pyarrow as pa


class VectorIndex:
    """IRI→埋め込みベクトルの永続索引。"""

    TABLE = "embeddings"

    def __init__(self, directory: Path, dim: int) -> None:
        self.dim = dim
        directory.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(directory))
        schema = pa.schema(
            [
                pa.field("iri", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
            ]
        )
        if self.TABLE in self._db.list_tables():
            self._table = self._db.open_table(self.TABLE)
        else:
            self._table = self._db.create_table(self.TABLE, schema=schema)

    def upsert(self, iri_value: str, vector: list[float]) -> None:
        if len(vector) != self.dim:
            raise ValueError(f"次元不一致: expected {self.dim}, got {len(vector)}")
        if '"' in iri_value:
            raise ValueError(f"不正なIRI: {iri_value!r}")
        self._table.delete(f'iri = "{iri_value}"')
        self._table.add([{"iri": iri_value, "vector": vector}])

    def search(self, vector: list[float], k: int = 5) -> list[tuple[str, float]]:
        if len(vector) != self.dim:
            raise ValueError(f"次元不一致: expected {self.dim}, got {len(vector)}")
        rows = self._table.search(vector).limit(k).to_list()
        return [(row["iri"], float(row["_distance"])) for row in rows]

    def count(self) -> int:
        return int(self._table.count_rows())
