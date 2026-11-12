# India Capital RegHub

Compliance tracker for foreign investment into India (FDI, FPI and FVCI). It collects circulars and notifications from 25 Indian regulators, makes them searchable by keyword and meaning, and keeps a due-diligence checklist for each deal.

```
database/   PostgreSQL schema: regulators, regulations, checklists, deals, sectors
scrapers/   Collect circulars and PDFs from regulator websites, and create search embeddings
backend/    FastAPI service the dashboard talks to
frontend/   React dashboard (Vite + Tailwind)
```

## Quick start with Docker

Runs the database, backend and dashboard with one command. You need Docker with Compose v2.

### 1. Start the stack

```bash
docker compose up -d --build
```

The first build takes several minutes, mostly installing PyTorch. When it finishes:

- Dashboard: http://localhost:8080
- API docs: http://localhost:8000/docs

The schema is applied automatically on every start, so updates to `database/schema.sql` are picked up the next time you run `up`.

### 2. Collect data

```bash
docker compose run --rm scraper
```

This runs the RBI and SEBI scraper, then the other 23 regulators, then creates search embeddings. The embedding model downloads once and is reused afterwards.

To pass options, give the command yourself:

```bash
docker compose run --rm scraper python generic_scraper.py --only CCI,MCA --dry-run
```

### Settings

Set these in your shell, or in a `.env` file next to `docker-compose.yml`, before building:

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_PASSWORD` | `reghub` | Change it before the first start on any shared machine. It only takes effect when the database volume is first created |
| `VITE_API_URL` | `http://localhost:8000` | The address the browser uses to reach the backend. Fixed at build time, so rebuild after changing it |
| `MAX_PAGES` | `3` | Pages fetched by the RBI and SEBI scraper |

If you change `VITE_API_URL` to another address, also add the dashboard's address to `CORS_ORIGINS` under `backend` in `docker-compose.yml`.

### Day-to-day commands

| Task | Command |
|---|---|
| Follow backend logs | `docker compose logs -f backend` |
| Open a database shell | `docker compose exec db psql -U reghub` |
| Stop everything | `docker compose down` |
| Stop and delete all data | `docker compose down -v` |

`docker compose down -v` deletes the database, including every deal, tick and note, and the downloaded model. It can't be undone.

If the build fails with `429 Too Many Requests`, Docker Hub is limiting downloads. Wait and try again, or sign in with `docker login`.

## Hosted deployment (Render + Cloudflare Pages)

- **Database:** a PostgreSQL database with pgvector, such as Neon or Supabase. Apply the schema once:
  ```bash
  psql "$DATABASE_URL" -f database/schema.sql
  ```
- **Backend on Render:** create a Blueprint from this repository (it reads `render.yaml`). Set `DATABASE_URL` and `CORS_ORIGINS` in the Render dashboard. The API health check is `/health`.
- **Dashboard on Cloudflare Pages:** create a Pages project from this repository with:
  - Root directory: `frontend`
  - Build command: `npm ci && npm run build`
  - Build output directory: `dist`
  - Environment variable `VITE_API_URL`: the Render API address, e.g. `https://reghub-api.onrender.com`
- **Scrapers:** the daily GitHub Actions workflow uses the same database, via the `DATABASE_URL` repository secret.

Render's free tier sleeps after inactivity, so the first request after a pause can take a minute.

## Manual setup

Use this to run the parts without Docker, for example while developing.

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer
- PostgreSQL 15 or newer with the pgvector extension. The Docker image below includes it.

## Setup

### 1. Start the database

```bash
docker run -d --name reghub-db -e POSTGRES_USER=reghub -e POSTGRES_PASSWORD=reghub -e POSTGRES_DB=reghub -p 5432:5432 pgvector/pgvector:pg16
```

```bash
export DATABASE_URL=postgresql://reghub:reghub@localhost:5432/reghub
```

### 2. Create the tables

```bash
psql "$DATABASE_URL" -f database/schema.sql
```

This creates the tables and loads the 25 regulators, the checklist items and the sector list. It's safe to run again after updating the project: existing data is kept.

### 3. Install the Python dependencies

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

`sentence-transformers` installs PyTorch, which is a large download. The embedding model, about 90 MB, downloads the first time it's used.

### 4. Collect data

Run the scrapers from the `scrapers/` folder.

RBI and SEBI:

```bash
cd scrapers && MAX_PAGES=3 python rbi_sebi_scraper.py
```

The other 23 regulators, including NSE and BSE. Try a dry run first; it prints what it finds without saving:

```bash
python generic_scraper.py --dry-run
```

```bash
python generic_scraper.py
```

Both scripts create search embeddings for new rows when they finish. Set `SKIP_EMBED=1` to skip that step, and run it later on its own:

```bash
python embedder.py
```

Check what was collected:

```bash
psql "$DATABASE_URL" -c "SELECT r.code, count(*) FROM regulations_master m JOIN regulators r ON r.id = m.regulator_id GROUP BY r.code ORDER BY 2 DESC;"
```

### 5. Start the backend

```bash
cd backend && uvicorn main:app --reload
```

The API runs at http://localhost:8000, with interactive docs at http://localhost:8000/docs.

### 6. Start the dashboard

```bash
cd frontend && npm install
```

```bash
npm run dev
```

Open http://localhost:5173. To use a backend somewhere other than `localhost:8000`, copy `frontend/.env.example` to `frontend/.env` and change `VITE_API_URL`.

## Scraper options

`generic_scraper.py` flags:

| Flag | Effect |
|---|---|
| `--only CCI,MCA` | Run only these regulators |
| `--max-pages N` | Pages to fetch where pagination is set up. For NSE, each page is 30 days |
| `--no-pdfs` | Skip downloading PDFs and extracting their text |
| `--dry-run` | Print results without saving |

Environment variables:

| Variable | Default | Used by |
|---|---|---|
| `DATABASE_URL` | `postgresql://reghub:reghub@localhost:5432/reghub` | Everything |
| `MAX_PAGES` | `3` | `rbi_sebi_scraper.py` |
| `SKIP_EMBED` | unset | Set to `1` to skip embeddings after scraping |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedder and backend. Must produce 384 dimensions |
| `NSE_CIRCULARS_API` | NSE circulars data feed | NSE scraper |
| `BSE_NOTICES_API` | unset | BSE scraper. If set, reads this data feed instead of the notices page |
| `BSE_NOTICES_PAGE` | BSE notices page | BSE scraper |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Backend: dashboard addresses allowed to call it |

## When a scraper returns nothing

None of the scrapers have been tested against the live regulator websites, and government sites change their pages often. If a regulator comes back with 0 items:

1. **Check the page address.** Each regulator's circulars page is stored in the `circulars_url` column of the `regulators` table. Open it in a browser and correct it if it has moved:
   ```bash
   psql "$DATABASE_URL" -c "UPDATE regulators SET circulars_url = 'https://…' WHERE code = 'CCI';"
   ```
2. **Adjust the settings.** In `scrapers/generic_scraper.py`, add or edit that regulator's entry in `OVERRIDES` to set pagination, which links to keep, or the reference-number format.
3. **NSE and BSE:** if fields come back empty, open the data feed in your browser, find the real field names and add them to `NSE_FIELDS` or `BSE_FIELDS` in `scrapers/exchange_scraper.py`.

Known limits:

- **RBI:** older years may not load, because the index page switches years through form submissions.
- **BSE:** without `BSE_NOTICES_API`, only the first page of notices is read.
- **Most generic sites:** only page 1 is fetched until pagination is configured. TRAI is the exception.

## Checklists

- **One checklist per deal.** Each deal has a route (GENERAL, FDI, FPI or FVCI) and optional sectors.
- **What a deal shows:**
  - **Route items:** every deal shows the general items; FDI, FPI and FVCI deals also show that route's items.
  - **Sectors not set (ALL):** all 25 regulators.
  - **Sectors selected:** the 12 core regulators plus those for the chosen sectors.
  - **Every sector unselected:** the 12 core regulators only.
- **Core regulators:** RBI, SEBI, DPIIT, DEA, MCA, CCI, CBDT, IFSCA, NSDL, CDSL, NSE and BSE.
- **Ticks and notes are never deleted** when you change a deal's route or sectors. They're hidden and come back if you switch back.
- **Editing the lists:** checklist items, sectors and the core list are all in `database/schema.sql`. Edit them there and run the schema again.

The checklist wording is general guidance, not legal advice. Have it reviewed by legal or compliance before relying on it.

## Limitations

- There are no user accounts. Everyone using the dashboard can see and edit every deal.
- Impact levels and FDI/FPI/FVCI tags are assigned by keyword matching, not by a person.
