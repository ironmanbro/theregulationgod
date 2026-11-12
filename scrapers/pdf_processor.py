"""PDF fetching and text extraction for regulatory notifications."""
from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from dataclasses import dataclass

import pdfplumber
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; RegHubBot/1.0)"
MAX_PDF_BYTES = 50 * 1024 * 1024


@dataclass
class PDFResult:
    url: str
    text: str
    pages: int
    sha256: str


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "HEAD"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"})
    return s


def _download(url: str, session: requests.Session, timeout: int) -> bytes:
    with session.get(url, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_content(64 * 1024):
            buf.write(chunk)
            if buf.tell() > MAX_PDF_BYTES:
                raise ValueError(f"PDF exceeds {MAX_PDF_BYTES} bytes: {url}")
        data = buf.getvalue()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"Response is not a PDF: {url}")
    return data


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"-\n(\w)", r"\1", text)            # de-hyphenate line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"^\s*Page \d+ of \d+\s*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(data: bytes) -> tuple[str, int]:
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
            for table in page.extract_tables() or []:
                rows = [" | ".join(c or "" for c in row) for row in table]
                txt += "\n" + "\n".join(rows)
            parts.append(txt)
        return clean_text("\n\n".join(parts)), len(pdf.pages)


def fetch_pdf_text(url: str, session: requests.Session | None = None,
                   timeout: int = 45, delay: float = 0.5) -> PDFResult | None:
    """Download a PDF and return cleaned text; returns None on failure."""
    session = session or build_session()
    try:
        data = _download(url, session, timeout)
        text, pages = extract_text(data)
        time.sleep(delay)
        return PDFResult(url=url, text=text, pages=pages,
                         sha256=hashlib.sha256(data).hexdigest())
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF extraction failed for %s: %s", url, exc)
        return None


def summarize(text: str, max_chars: int = 400) -> str:
    """Lightweight extractive summary: first meaningful sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
    out = ""
    for s in sentences:
        if len(s) < 30:
            continue
        if len(out) + len(s) > max_chars:
            break
        out += s + " "
    return out.strip() or text[:max_chars]


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    res = fetch_pdf_text(sys.argv[1])
    print(res.text[:2000] if res else "failed")
