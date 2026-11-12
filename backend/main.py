"""India Capital RegHub — FastAPI backend."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from enum import Enum
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://reghub:reghub@localhost:5432/reghub")
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")


class Route(str, Enum):
    ALL = "ALL"
    FDI = "FDI"
    FPI = "FPI"
    FVCI = "FVCI"


class Regulator(BaseModel):
    id: int
    code: str
    name: str
    sector: str
    website: str


class Regulation(BaseModel):
    id: int
    regulator: str
    reference_no: str
    title: str
    summary: Optional[str]
    doc_type: str
    routes: list[str]
    impact: str
    issue_date: date
    source_url: str
    pdf_url: Optional[str]


class RegulationPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Regulation]


class ChecklistItem(BaseModel):
    id: int
    regulator: str
    route: str
    item: str
    is_complete: bool
    notes: Optional[str] = None


class ChecklistUpdate(BaseModel):
    """Either field may be sent alone. An empty or blank `notes` clears the note."""
    is_complete: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class SemanticQuery(BaseModel):
    embedding: list[float] = Field(min_length=384, max_length=384)
    limit: int = Field(default=10, ge=1, le=50)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    yield
    await app.state.pool.close()


app = FastAPI(title="India Capital RegHub API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["*"])

SELECT_REG = """
SELECT m.id, r.code AS regulator, m.reference_no, m.title, m.summary, m.doc_type,
       m.routes::text[] AS routes, m.impact::text AS impact, m.issue_date, m.source_url, m.pdf_url
FROM regulations_master m JOIN regulators r ON r.id = m.regulator_id
"""


@app.get("/health")
async def health():
    async with app.state.pool.acquire() as c:
        await c.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/api/regulators", response_model=list[Regulator])
async def list_regulators():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT id, code, name, sector, website FROM regulators WHERE is_active ORDER BY id")
    return [dict(r) for r in rows]


@app.get("/api/regulations", response_model=RegulationPage)
async def list_regulations(
    regulators: Optional[str] = Query(None, description="Comma-separated codes, e.g. RBI,SEBI"),
    route: Route = Route.ALL,
    search: Optional[str] = Query(None, max_length=200),
    impact: Optional[str] = Query(None, pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    where, args = [], []

    def add(clause: str, value):
        args.append(value)
        where.append(clause.format(f"${len(args)}"))

    if regulators:
        add("r.code = ANY({})", [c.strip() for c in regulators.split(",") if c.strip()])
    if route != Route.ALL:
        add("{}::investment_route = ANY(m.routes)", route.value)
    if search:
        add("(m.search_tsv @@ websearch_to_tsquery('english', {0}) OR m.title ILIKE '%' || {0} || '%')", search)
    if impact:
        add("m.impact = {}::impact_level", impact)
    if date_from:
        add("m.issue_date >= {}", date_from)
    if date_to:
        add("m.issue_date <= {}", date_to)

    cond = (" WHERE " + " AND ".join(where)) if where else ""
    async with app.state.pool.acquire() as c:
        total = await c.fetchval(
            f"SELECT count(*) FROM regulations_master m JOIN regulators r ON r.id = m.regulator_id{cond}", *args)
        rows = await c.fetch(
            f"{SELECT_REG}{cond} ORDER BY m.issue_date DESC, m.id DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}


EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
_embedder = None


def _encode(text: str) -> list[float]:
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder.encode(text, normalize_embeddings=True).tolist()


# Declared before /{reg_id} so the path isn't captured by the integer route.
@app.get("/api/regulations/semantic-search", response_model=list[Regulation])
async def semantic_text_search(
    q: str = Query(..., min_length=2, max_length=500),
    route: Route = Route.ALL,
    regulators: Optional[str] = Query(None, description="Comma-separated codes, e.g. RBI,SEBI"),
    limit: int = Query(10, ge=1, le=50),
):
    from starlette.concurrency import run_in_threadpool
    vec = await run_in_threadpool(_encode, q)
    if len(vec) != 384:
        raise HTTPException(500, f"Embedding model returned {len(vec)} dims; expected 384")
    vec_str = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    cond = "WHERE m.embedding IS NOT NULL"
    args: list = [vec_str, limit]
    if route != Route.ALL:
        args.append(route.value)
        cond += f" AND ${len(args)}::investment_route = ANY(m.routes)"
    codes = [c.strip() for c in (regulators or "").split(",") if c.strip()]
    if codes:
        args.append(codes)
        cond += f" AND r.code = ANY(${len(args)})"
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"{SELECT_REG} {cond} ORDER BY m.embedding <=> $1::vector LIMIT $2", *args)
    return [dict(r) for r in rows]


@app.get("/api/regulations/{reg_id}")
async def get_regulation(reg_id: int):
    async with app.state.pool.acquire() as c:
        row = await c.fetchrow(
            SELECT_REG.replace("m.pdf_url", "m.pdf_url, m.full_text") + " WHERE m.id = $1", reg_id)
    if not row:
        raise HTTPException(404, "Regulation not found")
    return dict(row)


@app.post("/api/regulations/semantic", response_model=list[Regulation])
async def semantic_search(q: SemanticQuery):
    vec = "[" + ",".join(f"{x:.6f}" for x in q.embedding) + "]"
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"{SELECT_REG} WHERE m.embedding IS NOT NULL ORDER BY m.embedding <=> $1::vector LIMIT $2", vec, q.limit)
    return [dict(r) for r in rows]


@app.get("/api/stats")
async def stats():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("""
            SELECT r.code, count(m.id) AS total,
                   count(m.id) FILTER (WHERE m.impact IN ('HIGH','CRITICAL')) AS high_impact,
                   max(m.issue_date) AS latest
            FROM regulators r LEFT JOIN regulations_master m ON m.regulator_id = r.id
            GROUP BY r.id, r.code ORDER BY r.id""")
    return [dict(r) for r in rows]


class Deal(BaseModel):
    id: int
    name: str
    route: str
    sectors: Optional[list[str]] = None  # None = not set: all regulators apply


class Sector(BaseModel):
    sector: str
    label: str
    regulators: list[str]


class DealCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    route: Route = Route.ALL  # ALL is stored as GENERAL
    sectors: Optional[list[str]] = None


DEAL_COLS = "id, name, route::text AS route, sectors"


async def _check_sectors(c, sectors: Optional[list[str]]) -> Optional[list[str]]:
    if sectors is None:
        return None
    sectors = sorted(set(sectors))
    known = {r["sector"] for r in await c.fetch("SELECT DISTINCT sector FROM sector_regulators")}
    unknown = [s for s in sectors if s not in known]
    if unknown:
        raise HTTPException(422, f"Unknown sectors: {', '.join(unknown)}")
    return sectors


@app.get("/api/sectors", response_model=list[Sector])
async def list_sectors():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("""
            SELECT s.sector, min(s.label) AS label, array_agg(r.code ORDER BY r.id) AS regulators
            FROM sector_regulators s JOIN regulators r ON r.id = s.regulator_id
            GROUP BY s.sector ORDER BY min(r.id)""")
    return [dict(r) for r in rows]


@app.get("/api/deals", response_model=list[Deal])
async def list_deals():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(f"SELECT {DEAL_COLS} FROM deals ORDER BY created_at DESC")
    return [dict(r) for r in rows]


@app.post("/api/deals", response_model=Deal, status_code=201)
async def create_deal(body: DealCreate):
    route = "GENERAL" if body.route == Route.ALL else body.route.value
    async with app.state.pool.acquire() as c:
        sectors = await _check_sectors(c, body.sectors)
        try:
            row = await c.fetchrow(
                "INSERT INTO deals (name, route, sectors) VALUES ($1, $2::investment_route, $3) "
                f"RETURNING {DEAL_COLS}", body.name.strip(), route, sectors)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "A deal with that name already exists")
    return dict(row)


class DealUpdate(BaseModel):
    """Send `sectors: null` to clear sectors (show all regulators); omit it to leave unchanged."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    route: Optional[str] = Field(default=None, pattern="^(GENERAL|FDI|FPI|FVCI)$")
    sectors: Optional[list[str]] = None


@app.patch("/api/deals/{deal_id}", response_model=Deal)
async def update_deal(deal_id: int, body: DealUpdate):
    """Rename, change route and/or sectors. Ticks on items that drop out are kept and reappear if switched back."""
    name = body.name.strip() if body.name is not None else None
    if name == "":
        raise HTTPException(422, "Deal name cannot be blank")
    set_sectors = "sectors" in body.model_fields_set
    if name is None and body.route is None and not set_sectors:
        raise HTTPException(422, "Nothing to update")
    async with app.state.pool.acquire() as c:
        sectors = await _check_sectors(c, body.sectors) if set_sectors else None
        try:
            row = await c.fetchrow(
                "UPDATE deals SET name = COALESCE($2, name), route = COALESCE($3::investment_route, route), "
                "sectors = CASE WHEN $4::boolean THEN $5::text[] ELSE sectors END "
                f"WHERE id = $1 RETURNING {DEAL_COLS}",
                deal_id, name, body.route, set_sectors, sectors)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "A deal with that name already exists")
    if not row:
        raise HTTPException(404, "Deal not found")
    return dict(row)


@app.delete("/api/deals/{deal_id}", status_code=204)
async def delete_deal(deal_id: int):
    async with app.state.pool.acquire() as c:
        result = await c.execute("DELETE FROM deals WHERE id = $1", deal_id)  # ticks cascade
    if result == "DELETE 0":
        raise HTTPException(404, "Deal not found")


DEAL_ITEMS = """
SELECT c.id, r.code AS regulator, c.route::text AS route, c.item,
       coalesce(dc.is_complete, false) AS is_complete, dc.notes
FROM deals d
JOIN compliance_checklist c ON c.route IN (d.route, 'GENERAL')
JOIN regulators r ON r.id = c.regulator_id
LEFT JOIN deal_checklist dc ON dc.deal_id = d.id AND dc.item_id = c.id
WHERE d.id = $1
  AND (d.sectors IS NULL OR r.is_core OR EXISTS (
        SELECT 1 FROM sector_regulators sr WHERE sr.regulator_id = r.id AND sr.sector = ANY(d.sectors)))
"""


@app.get("/api/deals/{deal_id}/checklist", response_model=list[ChecklistItem])
async def get_deal_checklist(deal_id: int):
    async with app.state.pool.acquire() as c:
        if not await c.fetchval("SELECT 1 FROM deals WHERE id = $1", deal_id):
            raise HTTPException(404, "Deal not found")
        rows = await c.fetch(DEAL_ITEMS + " ORDER BY r.id, c.id", deal_id)
    return [dict(r) for r in rows]


@app.patch("/api/deals/{deal_id}/checklist/{item_id}", response_model=ChecklistItem)
async def update_deal_checklist(deal_id: int, item_id: int, body: ChecklistUpdate):
    async with app.state.pool.acquire() as c, c.transaction():
        row = await c.fetchrow(DEAL_ITEMS + " AND c.id = $2", deal_id, item_id)
        if not row:
            raise HTTPException(404, "Deal or checklist item not found")
        if body.is_complete is None and "notes" not in body.model_fields_set:
            raise HTTPException(422, "Nothing to update")
        await c.execute("""
            INSERT INTO deal_checklist (deal_id, item_id, is_complete, completed_at, notes)
            VALUES ($1, $2, coalesce($3::boolean, false), CASE WHEN $3::boolean THEN now() END,
                    NULLIF(trim($5::text), ''))
            ON CONFLICT (deal_id, item_id) DO UPDATE SET
                is_complete = COALESCE($3::boolean, deal_checklist.is_complete),
                completed_at = CASE WHEN $3::boolean IS NULL THEN deal_checklist.completed_at
                                    WHEN $3::boolean THEN now() END,
                notes = CASE WHEN $4::boolean THEN NULLIF(trim($5::text), '') ELSE deal_checklist.notes END""",
            deal_id, item_id, body.is_complete, "notes" in body.model_fields_set, body.notes)
        row = await c.fetchrow(DEAL_ITEMS + " AND c.id = $2", deal_id, item_id)
    return dict(row)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
