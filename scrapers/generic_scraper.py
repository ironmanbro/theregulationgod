"""Config-driven scraper for the 23 regulators without a dedicated scraper.

Index URLs come from the `regulators.circulars_url` column. Each listing page is parsed with
general heuristics: every link whose surrounding row (table row, list item or card) contains a
recognisable date, or that points to a PDF, is treated as a notification. Per-regulator tweaks
(pagination, link filters, reference-number patterns) live in OVERRIDES.

Usage:
    python generic_scraper.py                 # all regulators except RBI and SEBI
    python generic_scraper.py --only CCI,MCA  # selected regulators
    python generic_scraper.py --dry-run       # parse and print, no database writes
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import psycopg2
from bs4 import BeautifulSoup, Tag
from urllib.parse import unquote

from rbi_sebi_scraper import BaseScraper, Circular, parse_date, persist

log = logging.getLogger("reghub.generic")

# Lookarounds, not \b: file names put dates next to underscores, which count as word characters.
DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[ -](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[ -,]*\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})(?!\d)", re.I)
GENERIC_REF_RE = re.compile(
    r"\b((?:F\.?\s?No\.?|No\.?|G\.?S\.?R\.?|S\.?O\.?|Press Note No\.?|Circular No\.?|Notification No\.?)"
    r"\s*[:.\-]?\s*[A-Za-z0-9][A-Za-z0-9/\-.()]{2,60}\d)", re.I)
SKIP_LINK_TEXT = re.compile(r"^(home|about|contact|sitemap|login|next|previous|prev|more|read more|click here|"
                            r"download|view|hindi|english|\d+|»|«|>|<)$", re.I)


@dataclass
class SourceConfig:
    page_param: str | None = None           # e.g. "page" -> ?page=2
    page_start: int = 1                     # value used for the first page
    max_pages: int = 1
    link_filter: str | None = None          # regex the href must match
    ref_pattern: str | None = None          # regex with one group for the reference number
    min_title_len: int = 15
    extra_headers: dict = field(default_factory=dict)
    render: bool = False                    # load the page in a headless browser (for JavaScript sites)


# Starting points only: confirm each against the live site (see README note in main()).
OVERRIDES: dict[str, SourceConfig] = {
    "FSSAI":  SourceConfig(render=True, link_filter=r"\.pdf(\?|$)"),
    "DPIIT":  SourceConfig(ref_pattern=r"(Press Note No\.?\s*\d+\s*\(\d{4}(?:\s*Series)?\))"),
    "MCA":    SourceConfig(ref_pattern=r"((?:G\.S\.R|S\.O)\.?\s*\d+\s*\(E\))"),
    "CBDT":   SourceConfig(ref_pattern=r"(Notification No\.?\s*\d+/\d{4})"),
    "IFSCA":  SourceConfig(ref_pattern=r"(IFSCA/[A-Za-z0-9/\-]+/\d{4}(?:-\d{2})?/\d+)"),
    "NSDL":   SourceConfig(ref_pattern=r"(NSDL/[A-Z]+/\d{4}/\d+)"),
    "CDSL":   SourceConfig(ref_pattern=r"(CDSL/[A-Z/]+/\d{4}/\d+)"),
    "IRDAI":  SourceConfig(ref_pattern=r"(IRDAI/[A-Za-z0-9/\-&]+/\d{4}(?:-\d{2})?/?\d*)"),
    "PFRDA":  SourceConfig(ref_pattern=r"(PFRDA/\d{4}/\d+/[A-Z\-]+/\d+)"),
    "TRAI":   SourceConfig(page_param="page", page_start=0, max_pages=3),
    # NSE and BSE are handled by exchange_scraper.py.
}


class GenericScraper(BaseScraper):
    def __init__(self, code: str, index_url: str, cfg: SourceConfig, max_pages: int | None = None,
                 fetch_pdfs: bool = True):
        self.regulator = code
        self.index_url = index_url
        self.cfg = cfg
        super().__init__(max_pages=max_pages or cfg.max_pages, fetch_pdfs=fetch_pdfs)
        self.session.headers.update(cfg.extra_headers)
        self.host = urlparse(index_url).netloc

    def autoscrape_titles(self) -> set[str]:
        return set()  # no trained autoscraper rules for these sites yet

    def fetch_page(self, page: int) -> BeautifulSoup:
        params = None
        if self.cfg.page_param and page > 1:
            params = {self.cfg.page_param: self.cfg.page_start + page - 1}
        elif page > 1:
            return BeautifulSoup("", "lxml")  # no pagination configured: stop after page 1
        if self.cfg.render:
            from browser import render_html
            url = self.index_url + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
            return BeautifulSoup(render_html(url), "lxml")
        r = self.session.get(self.index_url, params=params, timeout=30)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")

    @staticmethod
    def _row(a: Tag) -> Tag:
        for parent in a.parents:
            if parent.name in ("tr", "li", "article") or (
                    parent.name == "div" and len(parent.get_text(" ", strip=True)) < 600):
                return parent
            if parent.name in ("table", "ul", "ol", "body"):
                break
        return a  # no row-like container: use the link alone, never the whole page

    def _reference(self, text: str, href: str) -> str:
        if self.cfg.ref_pattern:
            m = re.search(self.cfg.ref_pattern, text, re.I)
            if m:
                return re.sub(r"\s+", " ", m.group(1)).strip()
        m = GENERIC_REF_RE.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
        return f"{self.regulator}-{hashlib.sha1(href.encode()).hexdigest()[:12]}"

    def parse_page(self, soup: BeautifulSoup) -> list[Circular]:
        out: dict[str, Circular] = {}
        link_filter = re.compile(self.cfg.link_filter, re.I) if self.cfg.link_filter else None
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(self.index_url, href)
            if urlparse(url).scheme not in ("http", "https") or url in out:
                continue
            if link_filter and not link_filter.search(url):
                continue
            is_pdf = bool(re.search(r"\.pdf(\?|$)", url, re.I))
            row = self._row(a)
            row_text = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
            fname = unquote(url.rsplit("/", 1)[-1]).rsplit(".", 1)[0]
            m = DATE_RE.search(row_text) or DATE_RE.search(fname)
            d = parse_date(m.group(1)) if m else None
            if not d and not is_pdf:
                continue
            title = re.sub(r"\s+", " ", a.get("title") or a.get_text(" ", strip=True)).strip()
            if len(title) < self.cfg.min_title_len or SKIP_LINK_TEXT.match(title):
                # Link text is generic ("Download"); fall back to the row text minus the date.
                cleaned = DATE_RE.sub("", row_text)
                cleaned = re.sub(r"\b(download|view|click here|read more|pdf)\b|\(\s*\d+(\.\d+)?\s*[KM]B\s*\)", "",
                                 cleaned, flags=re.I)
                title = (re.sub(r"\s+", " ", cleaned).strip(" -|:") or title)[:500]
            generic_link = bool(re.match(r"(pdf|download|format|view)\b|\[[\d.]+ ?[KM]B\]", title, re.I))
            if is_pdf and (generic_link or len(title) < self.cfg.min_title_len):
                from_file = re.sub(r"\s+", " ", DATE_RE.sub("", fname).replace("_", " ")).strip(" -|:")
                if len(from_file) >= self.cfg.min_title_len:
                    title = from_file
            if len(title) < self.cfg.min_title_len or not d:
                continue
            out[url] = Circular(self.regulator, self._reference(f"{title} {row_text}", url)[:200],
                                title[:500], d, url if not is_pdf else self.index_url,
                                url if is_pdf else None)
        return list(out.values())


def load_sources(dsn: str, only: set[str] | None) -> list[tuple[str, str]]:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT code, circulars_url FROM regulators "
                    "WHERE is_active AND circulars_url IS NOT NULL AND code NOT IN ('RBI','SEBI') ORDER BY id")
        rows = cur.fetchall()
    return [(c, u) for c, u in rows if not only or c in only]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated regulator codes")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--no-pdfs", action="store_true", help="Skip PDF download and text extraction")
    ap.add_argument("--dry-run", action="store_true", help="Print parsed items instead of saving")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL", "postgresql://reghub:reghub@localhost:5432/reghub")
    only = {c.strip() for c in args.only.split(",")} if args.only else None
    summary = {}
    for code, url in load_sources(dsn, only):
        fetch_pdfs = not args.no_pdfs and not args.dry_run
        if code in ("NSE", "BSE"):
            from exchange_scraper import build
            scraper = build(code, args.max_pages, fetch_pdfs)
        else:
            scraper = GenericScraper(code, url, OVERRIDES.get(code, SourceConfig()), args.max_pages,
                                     fetch_pdfs=fetch_pdfs)
        try:
            items = scraper.run()
        except Exception as exc:  # noqa: BLE001 — one failing site must not stop the rest
            log.error("[%s] failed: %s", code, exc)
            summary[code] = "ERROR"
            continue
        if args.dry_run:
            for c in items[:10]:
                print(f"{code:7} {c.issue_date} {c.reference_no[:30]:30} {c.title[:80]}")
            summary[code] = len(items)
        else:
            try:
                summary[code] = persist(items, dsn)
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] save failed: %s", code, exc)
                summary[code] = "ERROR"
    log.info("summary: %s", summary)
    zero = [c for c, n in summary.items() if n in (0, "ERROR")]
    if zero:
        log.warning("No items from %s — check the index URL in the regulators table or add an OVERRIDES entry",
                    ", ".join(zero))
    if not args.dry_run and os.environ.get("SKIP_EMBED") != "1":
        from embedder import backfill
        log.info("embedded %d new rows", backfill(dsn))


if __name__ == "__main__":
    main()
