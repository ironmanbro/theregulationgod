"""RBI & SEBI circular scrapers: autoscraper learned rules with BeautifulSoup pagination fallback."""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import psycopg2
from autoscraper import AutoScraper
from bs4 import BeautifulSoup
from psycopg2.extras import execute_values

from pdf_processor import build_session, fetch_pdf_text, summarize

log = logging.getLogger("reghub.scraper")
MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

ROUTE_KEYWORDS = {
    "FDI": [r"foreign direct investment", r"\bFDI\b", r"press note", r"non-debt instrument", r"\bNDI\b"],
    "FPI": [r"foreign portfolio investor", r"\bFPI\b", r"portfolio investment", r"custodian"],
    "FVCI": [r"foreign venture capital", r"\bFVCI\b", r"venture capital"],
}
HIGH_IMPACT = [r"master direction", r"amendment", r"prohibit", r"mandatory", r"with immediate effect", r"penalt"]


@dataclass
class Circular:
    regulator: str
    reference_no: str
    title: str
    issue_date: date
    source_url: str
    pdf_url: str | None = None
    full_text: str = ""
    summary: str = ""
    routes: list[str] = field(default_factory=lambda: ["GENERAL"])
    impact: str = "MEDIUM"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.regulator}|{self.reference_no}|{self.title}".encode()).hexdigest()


def classify(text: str) -> tuple[list[str], str]:
    routes = [r for r, pats in ROUTE_KEYWORDS.items() if any(re.search(p, text, re.I) for p in pats)]
    hits = sum(bool(re.search(p, text, re.I)) for p in HIGH_IMPACT)
    impact = "CRITICAL" if hits >= 3 else "HIGH" if hits == 2 else "MEDIUM" if hits == 1 else "LOW"
    return routes or ["GENERAL"], impact


def parse_date(raw: str) -> date | None:
    raw = re.sub(r"\s+", " ", raw.strip().replace(",", ""))
    for fmt in ("%b %d %Y", "%d %b %Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%B %d %Y", "%d %B %Y",
                "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%b/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


class BaseScraper:
    regulator = ""
    index_url = ""
    sample_wanted: list[str] = []   # sample values visible on the index page used to train autoscraper

    def __init__(self, max_pages: int = 5, fetch_pdfs: bool = True):
        self.session = build_session()
        self.max_pages = max_pages
        self.fetch_pdfs = fetch_pdfs
        self.model_path = MODEL_DIR / f"{self.regulator.lower()}.json"

    # ---- autoscraper -------------------------------------------------
    def learn(self) -> AutoScraper | None:
        scraper = AutoScraper()
        if self.model_path.exists():
            scraper.load(str(self.model_path))
            return scraper
        if not self.sample_wanted:
            return None
        try:
            scraper.build(self.index_url, wanted_list=self.sample_wanted,
                          request_args={"headers": dict(self.session.headers), "timeout": 30})
            scraper.save(str(self.model_path))
            return scraper
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] autoscraper training failed: %s", self.regulator, exc)
            return None

    def autoscrape_titles(self) -> set[str]:
        scraper = self.learn()
        if not scraper:
            return set()
        try:
            return set(scraper.get_result_similar(self.index_url, group_by_alias=False) or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] autoscraper run failed: %s", self.regulator, exc)
            return set()

    # ---- BeautifulSoup fallback -------------------------------------
    def fetch_page(self, page: int) -> BeautifulSoup:
        raise NotImplementedError

    def parse_page(self, soup: BeautifulSoup) -> list[Circular]:
        raise NotImplementedError

    def enrich(self, c: Circular) -> Circular:
        if self.fetch_pdfs and c.pdf_url:
            res = fetch_pdf_text(c.pdf_url, self.session)
            if res:
                c.full_text = res.text
        basis = c.full_text or c.title
        c.summary = summarize(basis)
        c.routes, c.impact = classify(f"{c.title}\n{basis}")
        return c

    def run(self) -> list[Circular]:
        learned = self.autoscrape_titles()
        results: dict[str, Circular] = {}
        for page in range(1, self.max_pages + 1):
            try:
                items = self.parse_page(self.fetch_page(page))
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] page %d failed: %s", self.regulator, page, exc)
                break
            if not items:
                break
            new = [i for i in items if i.content_hash not in results]
            if not new:
                break  # pagination looped back
            for c in new:
                results[c.content_hash] = c
        log.info("[%s] %d items via BS4, %d titles via autoscraper",
                 self.regulator, len(results), len(learned))
        return [self.enrich(c) for c in results.values()]


class RBIScraper(BaseScraper):
    regulator = "RBI"
    index_url = "https://www.rbi.org.in/Scripts/BS_CircularIndexDisplay.aspx"
    base = "https://www.rbi.org.in/Scripts/"

    def fetch_page(self, page: int) -> BeautifulSoup:
        # RBI's index is an ASP.NET form; year-wise views are fetched via GET param fallback.
        year = date.today().year - (page - 1)
        r = self.session.get(self.index_url, params={"year": year}, timeout=30)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")

    def parse_page(self, soup: BeautifulSoup) -> list[Circular]:
        out = []
        for row in soup.select("table.tablebg tr, table tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            link = row.find("a", href=re.compile(r"NotificationUser|BS_CircularIndexDisplay|\.pdf", re.I))
            d = parse_date(cells[1].get_text(" ", strip=True)) or parse_date(cells[0].get_text(" ", strip=True))
            if not link or not d:
                continue
            ref = cells[0].get_text(" ", strip=True)
            title = cells[-1].get_text(" ", strip=True) or link.get_text(strip=True)
            href = urljoin(self.base, link["href"])
            pdf = row.find("a", href=re.compile(r"\.pdf", re.I))
            out.append(Circular("RBI", ref[:200], title, d, href,
                                urljoin(self.base, pdf["href"]) if pdf else None))
        return out


class SEBIScraper(BaseScraper):
    regulator = "SEBI"
    index_url = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0"
    ajax_url = "https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp"

    def fetch_page(self, page: int) -> BeautifulSoup:
        if page == 1:
            r = self.session.get(self.index_url, timeout=30)
        else:
            r = self.session.post(self.ajax_url, timeout=30, data={
                "nextValue": str(page - 1), "next": "n", "search": "", "fromDate": "", "toDate": "",
                "fromYear": "", "toYear": "", "deptId": "", "sid": "1", "ssid": "7", "smid": "0",
                "ssidhidden": "7", "intmid": "-1", "sText": "", "ssText": "-- All Sub Section --",
                "smText": "", "doDirect": str(page - 1)})
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")

    def parse_page(self, soup: BeautifulSoup) -> list[Circular]:
        out = []
        for row in soup.select("table#sample_1 tr, table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            d = parse_date(cells[0].get_text(" ", strip=True))
            link = cells[1].find("a")
            if not d or not link or not link.get("href"):
                continue
            title = link.get("title") or link.get_text(" ", strip=True)
            href = urljoin("https://www.sebi.gov.in", link["href"])
            m = re.search(r"SEBI/[A-Z0-9/\-_]+/\d{4}/\d+", title)
            ref = m.group(0) if m else href.rsplit("/", 1)[-1].replace(".html", "")
            out.append(Circular("SEBI", ref, title, d, href, self._pdf_from_detail(href)))
        return out

    def _pdf_from_detail(self, href: str) -> str | None:
        if not self.fetch_pdfs:
            return None
        try:
            r = self.session.get(href, timeout=30)
            soup = BeautifulSoup(r.text, "lxml")
            frame = soup.find("iframe", src=re.compile(r"\.pdf", re.I))
            if frame:
                src = frame["src"].split("file=")[-1]
                return urljoin(href, src)
            a = soup.find("a", href=re.compile(r"\.pdf$", re.I))
            return urljoin(href, a["href"]) if a else None
        except Exception:  # noqa: BLE001
            return None


def persist(items: list[Circular], dsn: str) -> int:
    if not items:
        return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT code, id FROM regulators")
        ids = dict(cur.fetchall())
        rows = [(ids[c.regulator], c.reference_no, c.title, c.summary, c.full_text,
                 "{" + ",".join(c.routes) + "}", c.impact, c.issue_date, c.source_url,
                 c.pdf_url, c.content_hash) for c in items]
        execute_values(cur, """
            INSERT INTO regulations_master (regulator_id, reference_no, title, summary, full_text,
                routes, impact, issue_date, source_url, pdf_url, content_hash)
            VALUES %s
            ON CONFLICT (regulator_id, reference_no) DO UPDATE SET
                title = EXCLUDED.title, summary = EXCLUDED.summary,
                full_text = COALESCE(NULLIF(EXCLUDED.full_text,''), regulations_master.full_text),
                routes = EXCLUDED.routes, impact = EXCLUDED.impact, pdf_url = EXCLUDED.pdf_url
            """, rows,
            template="(%s,%s,%s,%s,%s,%s::investment_route[],%s::impact_level,%s,%s,%s,%s)")
        return cur.rowcount


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ.get("DATABASE_URL", "postgresql://reghub:reghub@localhost:5432/reghub")
    pages = int(os.environ.get("MAX_PAGES", "3"))
    for cls in (RBIScraper, SEBIScraper):
        items = cls(max_pages=pages).run()
        log.info("[%s] upserted %d rows", cls.regulator, persist(items, dsn))
    if os.environ.get("SKIP_EMBED") != "1":
        from embedder import backfill
        log.info("embedded %d new rows", backfill(dsn))


if __name__ == "__main__":
    main()
