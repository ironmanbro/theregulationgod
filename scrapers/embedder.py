"""Generate 384-dim embeddings for regulations_master rows missing one (pgvector)."""
from __future__ import annotations

import logging
import os

import psycopg2
from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer

log = logging.getLogger("reghub.embedder")
MODEL_NAME = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")  # 384 dims
BATCH = int(os.environ.get("EMBED_BATCH", "64"))
MAX_CHARS = 4000

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
        dim = _model.get_sentence_embedding_dimension()
        if dim != 384:
            raise ValueError(f"{MODEL_NAME} produces {dim} dims; schema expects vector(384)")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_model().encode(texts, batch_size=BATCH, normalize_embeddings=True,
                              show_progress_bar=False).tolist()


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def backfill(dsn: str, reembed_all: bool = False) -> int:
    """Embed rows with NULL embedding (or all rows if reembed_all). Returns rows updated."""
    total, last_id = 0, 0
    where = "id > %s" if reembed_all else "embedding IS NULL AND id > %s"
    with psycopg2.connect(dsn) as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, reference_no, title, coalesce(summary,''), coalesce(full_text,'')
                    FROM regulations_master WHERE {where}
                    ORDER BY id LIMIT %s""", (last_id, BATCH))
                rows = cur.fetchall()
                if not rows:
                    break
                texts = [f"{ref}. {title}. {summary}\n{full[:MAX_CHARS]}"
                         for _, ref, title, summary, full in rows]
                vecs = embed_texts(texts)
                execute_values(cur, """
                    UPDATE regulations_master m SET embedding = v.emb::vector
                    FROM (VALUES %s) AS v(id, emb) WHERE m.id = v.id""",
                    [(r[0], to_pgvector(v)) for r, v in zip(rows, vecs)])
            conn.commit()
            last_id = rows[-1][0]
            total += len(rows)
            log.info("embedded %d rows (total %d)", len(rows), total)
    return total


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ.get("DATABASE_URL", "postgresql://reghub:reghub@localhost:5432/reghub")
    print(backfill(dsn, reembed_all="--all" in sys.argv))
