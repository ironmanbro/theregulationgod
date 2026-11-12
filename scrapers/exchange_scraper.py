"""NSE and BSE circular scrapers.

NSE: the circulars page loads its list from a JSON API that only answers after the browser has
     picked up cookies from the home page, so the scraper visits the home page first, then asks
     for circulars in 30-day windows going back LOOKBACK_DAYS.
BSE: notices are fetched from a JSON endpoint if BSE_NOTICES_API is set; otherwise the notices
     page HTML is parsed with the generic heuristics, using BSE's YYYYMMDD-N notice numbers.

Neither exchange documents these endpoints and both change them without notice. Endpoint URLs
and JSON field names are kept in the constants below so they can be corrected quickly.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta

from generic_scraper import GenericScraper, SourceConfig
from rbi_sebi_scraper import BaseScraper, Circular, parse_date

log = logging.getLogger("reghub.exchange")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

NSE_HOME = "https://www.nseindia.com"
NSE_REFERER = "https://www.nseindia.com/resources/exchange-communication-circulars"
NSE_API = os.environ.get("NSE_CIRCULARS_API", "https://www.nseindia.com/api/circulars")

# Candidate JSON keys, first match wins. Add the real key here if a field comes back empty.
NSE_FIELDS = {
    "ref":   ("circDisplayNo", "circNumber", "circularNo", "circNo"),
    "title": ("sub", "subject", "circSubject", "circSub"),
    "date":  ("cirDate", "circDate", "date", "circularDate"),
    "pdf":   ("circFilelink", "fileLink", "circFileLink", "attachment"),
    "dept":  ("circDepartment", "department", "dept"),
}
BSE_FIELDS = {
    "ref":   ("NOTICE_NO", "NoticeNo", "notice_no", "NOTICENO"),
    "title": ("NOTICE_SUBJECT", "Subject", "SUBJECT", "NEWSSUB"),
    "date":  ("NOTICE_DATE", "NoticeDate", "DT_TM", "NEWS_DT"),
    "pdf":   ("ATTACHMENTNAME", "Attachment", "PDF_LINK", "ATTACHMENT"),
    "dept":  ("DEPARTMENT", "Department", "SEGMENT"),
}
BSE_ATTACHMENT_BASE = "https://www.bseindia.com/downloads/UploadDocs/"
BSE_NOTICES_PAGE = os.environ.get("BSE_NOTICES_PAGE",
                                  "https://www.bseindia.com/markets/MarketInfo/NoticesCirculars.aspx")


def pick(record: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = record.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def parse_any_date(raw: str) -> date | None:
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d-%b-%Y %H:%M:%S",
                "%d %b %Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return parse_date(raw.split(" ")[0]) or parse_date(raw)


def records_from(payload) -> list[dict]:
    """Find the list of records in a JSON payload, whatever key it sits under."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "Table", "Data", "records", "result"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
    return []


def map_records(regulator: str, records: list[dict], fields: dict, source_url: str,
                attachment_base: str | None = None) -> list[Circular]:
    out = []
    for r in records:
        ref, title = pick(r, fields["ref"]), pick(r, fields["title"])
        d = parse_any_date(pick(r, fields["date"]))
        if not ref or not title or not d:
            continue
        pdf = pick(r, fields["pdf"]) or None
        if pdf and attachment_base and not pdf.startswith("http"):
            pdf = attachment_base + pdf
        dept = pick(r, fields["dept"])
        out.append(Circular(regulator, ref[:200], (f"[{dept}] {title}" if dept else title)[:500], d,
                            source_url, pdf))
    return out


class NSEScraper(BaseScraper):
    regulator = "NSE"
    index_url = NSE_REFERER
    window_days = 30

    def __init__(self, lookback_days: int = 90, fetch_pdfs: bool = True):
        super().__init__(max_pages=max(1, -(-lookback_days // self.window_days)), fetch_pdfs=fetch_pdfs)
        self.session.headers.update(BROWSER_HEADERS)
        self.session.headers["Referer"] = NSE_REFERER
        self._warmed = False

    def autoscrape_titles(self) -> set[str]:
        return set()

    def _warm(self) -> None:
        if not self._warmed:
            self.session.get(NSE_HOME, timeout=30, headers={"Accept": "text/html"})
            self.session.get(NSE_REFERER, timeout=30, headers={"Accept": "text/html"})
            self._warmed = True

    def fetch_window(self, page: int) -> list[dict]:
        self._warm()
        to_d = date.today() - timedelta(days=(page - 1) * self.window_days)
        from_d = to_d - timedelta(days=self.window_days - 1)
        params = {"fromDate": from_d.strftime("%d-%m-%Y"), "toDate": to_d.strftime("%d-%m-%Y")}
        r = self.session.get(NSE_API, params=params, timeout=30)
        if r.status_code in (401, 403):  # cookies expired: refresh once
            self._warmed = False
            self._warm()
            r = self.session.get(NSE_API, params=params, timeout=30)
        r.raise_for_status()
        time.sleep(1)
        return records_from(r.json())

    # BaseScraper.run() calls fetch_page/parse_page; an empty window doesn't mean we're done,
    # so run() is overridden to walk every window.
    def run(self) -> list[Circular]:
        seen: dict[str, Circular] = {}
        for page in range(1, self.max_pages + 1):
            try:
                for c in map_records("NSE", self.fetch_window(page), NSE_FIELDS, NSE_REFERER):
                    seen.setdefault(c.content_hash, c)
            except Exception as exc:  # noqa: BLE001
                log.error("[NSE] window %d failed: %s", page, exc)
                break
        log.info("[NSE] %d circulars", len(seen))
        return [self.enrich(c) for c in seen.values()]


class BSEScraper(GenericScraper):
    """HTML fallback reads page 1 only: older pages sit behind ASP.NET form posts."""

    def __init__(self, fetch_pdfs: bool = True):
        super().__init__("BSE", BSE_NOTICES_PAGE,
                         SourceConfig(ref_pattern=r"\b(\d{8}-\d{1,4})\b", max_pages=1),
                         fetch_pdfs=fetch_pdfs)
        self.session.headers.update(BROWSER_HEADERS)
        self.session.headers["Referer"] = "https://www.bseindia.com/"
        self.api = os.environ.get("BSE_NOTICES_API")

    def run(self) -> list[Circular]:
        if not self.api:
            return super().run()
        r = self.session.get(self.api, timeout=30)
        r.raise_for_status()
        items = map_records("BSE", records_from(r.json()), BSE_FIELDS,
                            "https://www.bseindia.com/markets/MarketInfo/NoticesCirculars.aspx",
                            BSE_ATTACHMENT_BASE)
        log.info("[BSE] %d notices via API", len(items))
        return [self.enrich(c) for c in items]


def build(code: str, max_pages: int | None, fetch_pdfs: bool) -> BaseScraper:
    if code == "NSE":
        return NSEScraper(lookback_days=(max_pages or 3) * NSEScraper.window_days, fetch_pdfs=fetch_pdfs)
    return BSEScraper(fetch_pdfs=fetch_pdfs)
