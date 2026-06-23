"""Tender parser service — h025ai-8.

Downloads tender documents from zakupki.gov.ru and integrates with the
document_parser for text extraction.

Coverage (research/zakupki-html-recon.md):
  - Search results (extendedsearch/results.html?fz44=on) — 44-ФЗ feed
  - Tender card (notice/zk20/view/common-info.html?regNumber=XXX)
  - Tender documents (notice/zk20/view/documents.html?regNumber=XXX)
  - Document download (main/public/download/downloadDocument.html?id=XXX)
  - Contract card (contract/contractCard/common-info.html?reestrNumber=XXX)
  - Acceptance acts (contract/contractCard/document-info.html?reestrNumber=XXX)

Robustness:
  - User-Agent is MANDATORY (zakupki returns 403/429 without it)
  - tenacity retry+backoff (1s, 2s, 4s) for Varnish 0-byte responses
  - Concurrency=3 for HTTP fetches (research recommends 8 req/s ceiling)
  - JSESSIONID cookie reuse for printForm/view.html
  - NO FTP (closed since 01.01.2025) — we use HTML scraping only

Phase 2 (not in MVP):
  - SOAP int44.zakupki.gov.ru bulk exports (getDocsByOrgRegionRequest)
  - 223-ФЗ parser (different layout, best-effort)
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..database import async_session
from ..models import Tender
from ..models.tender_documents import TenderDocument
from .document_parser import ParsedDocument, extract_text

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────

ZAKUPKI_BASE = "https://zakupki.gov.ru"

# User-Agent: desktop Chrome on Windows. Mandatory — zakupki returns
# 403/429 without it.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# Concurrency for HTTP fetches. Research: 8 req/s ceiling.
DEFAULT_CONCURRENCY = 3
HTTP_TIMEOUT_SEC = 30.0

# Empty response = Varnish issue. Treat as transient error.
EMPTY_RESPONSE_MARKERS = (b"", b"\n", b"<html></html>")


# ── Dataclasses ───────────────────────────────────────────────────


@dataclass
class ParsedTender:
    """Result of parsing one tender card."""

    reg_number: str
    title: str
    customer: str | None = None
    customer_inn: str | None = None
    customer_org_id: str | None = None
    customer_org_code: str | None = None
    price: float | None = None
    deadline: datetime | None = None
    published_at: datetime | None = None
    law_type: str = "44-ФЗ"
    region: str | None = None
    source_url: str | None = None
    raw_html: str | None = None  # for debug / re-parsing
    documents: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedSearchPage:
    """One page of search results."""

    tenders: list[dict[str, Any]]
    total: int
    page_number: int


# ── HTTP client factory ───────────────────────────────────────────


def _build_client(
    user_agent: str = DEFAULT_USER_AGENT,
    cookies: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT_SEC,
) -> httpx.AsyncClient:
    """Build an async HTTP client with proper headers."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    return httpx.AsyncClient(
        headers=headers,
        cookies=cookies or {},
        timeout=timeout,
        follow_redirects=True,
        http2=False,
    )


async def _fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> httpx.Response:
    """Fetch URL with tenacity retry on transient errors.

    Backoff: 1s, 2s, 4s. Retries on:
      - httpx.HTTPStatusError for 5xx and 429
      - Empty body (Varnish 0-byte responses)
      - httpx.TimeoutException
    """
    last_exc: Exception | None = None
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(
                (httpx.HTTPStatusError, httpx.TimeoutException, EmptyResponseError)
            ),
            reraise=True,
        ):
            with attempt:
                resp = await client.get(url, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"Server returned {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                if not resp.content or resp.content in EMPTY_RESPONSE_MARKERS:
                    raise EmptyResponseError(f"Empty body from {url}")
                resp.raise_for_status()
                return resp
    except RetryError as e:
        logger.error("Retry exhausted for %s: %s", url, e)
        raise
    return resp  # type: ignore[name-defined]


class EmptyResponseError(Exception):
    """Raised when the response body is suspiciously empty (Varnish)."""


# ── Search results parsing ────────────────────────────────────────


async def search_tenders_44fz(
    query: str = "",
    *,
    page: int = 1,
    records_per_page: int = 50,
    region: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    publish_date_from: str | None = None,
    publish_date_to: str | None = None,
    sort_by: str = "UPDATE_DATE",
    user_agent: str = DEFAULT_USER_AGENT,
) -> ParsedSearchPage:
    """Fetch one page of 44-ФЗ search results.

    URL: {base}/epz/order/extendedsearch/results.html?fz44=on&...
    """
    params: dict[str, Any] = {
        "fz44": "on",
        "morphology": "on",
        "openMode": "USE_DEFAULT_PARAMS",
        "pageNumber": page,
        "recordsPerPage": f"_{records_per_page}",
        "sortBy": sort_by,
        "sortDirection": "false",
        "showLotsInfoHidden": "false",
    }
    if query:
        params["searchString"] = query
    if region:
        params["regions"] = region
    if price_min is not None:
        params["priceFromGeneral"] = int(price_min)
    if price_max is not None:
        params["priceToGeneral"] = int(price_max)
    if publish_date_from:
        params["publishDateFrom"] = publish_date_from
    if publish_date_to:
        params["publishDateTo"] = publish_date_to

    url = f"{ZAKUPKI_BASE}/epz/order/extendedsearch/results.html"
    async with _build_client(user_agent) as client:
        resp = await _fetch_with_retry(client, url, params=params)
        return _parse_search_results(resp.text, page)


def _parse_search_results(html: str, page: int) -> ParsedSearchPage:
    """Extract tender stubs from search results HTML."""
    soup = BeautifulSoup(html, "lxml")
    tenders: list[dict[str, Any]] = []

    # Two selector patterns (zakupki uses both):
    #  - Modern: div.search-registry-entry
    #  - Legacy: div.registry-entry
    entries = soup.select(".search-registry-entry") or soup.select(".registry-entry")
    if not entries:
        # Fallback to broader selector
        entries = soup.find_all("div", class_=lambda c: c and "entry" in c.lower())

    for entry in entries:
        try:
            stub = _extract_tender_stub(entry)
            if stub:
                tenders.append(stub)
        except Exception as e:
            logger.debug("Failed to extract tender stub: %s", e)

    # Total count from "Найдено записей: N"
    total = 0
    total_match = re.search(r"Найдено\s*записей[:\s]*(\d[\d\s]*)", html)
    if total_match:
        total = int(total_match.group(1).replace(" ", "").replace("\u00a0", ""))

    return ParsedSearchPage(tenders=tenders, total=total, page_number=page)


def _extract_tender_stub(entry) -> dict[str, Any] | None:
    """Pull regNumber, title, price, customer, deadline from one result row."""
    # Link to tender detail
    link = entry.select_one("a[href*='regNumber'], a[href*='notice']")
    if not link:
        link = entry.find("a", href=True)
    if not link:
        return None

    href = link.get("href", "")
    reg_match = re.search(r"regNumber=(\d+)", href)
    if not reg_match:
        # Try alternative format
        reg_match = re.search(r"/(\d{10,})", href)
    if not reg_match:
        return None
    reg_number = reg_match.group(1)

    title = link.get_text(strip=True) or entry.get_text(strip=True)[:200]

    # Price (with NBSP cleanup)
    price = None
    price_el = entry.select_one(".price-block__value, .price, .cost")
    if price_el:
        raw = price_el.get_text(" ", strip=True)
        nums = re.findall(r"[\d][\d\s\u00a0]*[.,]?\d*", raw)
        for n in nums:
            cleaned = n.replace(" ", "").replace("\u00a0", "").replace(",", ".")
            try:
                price = float(cleaned)
                break
            except ValueError:
                continue

    # Customer
    customer = None
    customer_el = entry.select_one(".customer, .orgName, .organization")
    if customer_el:
        customer = customer_el.get_text(strip=True)

    # Deadline (submission end)
    deadline = None
    deadline_el = entry.select_one(".deadline, .endDate, .submission")
    if deadline_el:
        deadline = _parse_russian_date(deadline_el.get_text(strip=True))

    # Customer orgId / orgCode (3 identifiers!)
    org_id = None
    org_code = None
    org_el = entry.select_one("[data-organization-id], [data-org-id]")
    if org_el:
        org_id = org_el.get("data-organization-id") or org_el.get("data-org-id")
    org_code_el = entry.select_one("[data-organization-code], [data-org-code]")
    if org_code_el:
        org_code = org_code_el.get("data-organization-code") or org_code_el.get("data-org-code")

    return {
        "reg_number": reg_number,
        "title": title,
        "customer": customer,
        "price": price,
        "deadline": deadline,
        "customer_org_id": org_id,
        "customer_org_code": org_code,
        "source_url": (
            href if href.startswith("http") else f"{ZAKUPKI_BASE}{href}"
        ),
    }


def _parse_russian_date(text: str) -> datetime | None:
    """Parse Russian date formats: '28.06.2026', '28 июня 2026' etc."""
    if not text:
        return None
    text = text.strip()
    # Numeric DD.MM.YYYY [HH:MM]
    m = re.match(
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})(?:\s+(\d{1,2}):(\d{1,2}))?",
        text,
    )
    if m:
        d, mo, y, hh, mm = m.groups()
        y = int(y)
        if y < 100:
            y += 2000
        try:
            return datetime(
                year=y,
                month=int(mo),
                day=int(d),
                hour=int(hh) if hh else 0,
                minute=int(mm) if mm else 0,
            )
        except ValueError:
            return None

    # Russian month names
    months_ru = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    m = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        d, mo_name, y = m.groups()
        mo = months_ru.get(mo_name.lower())
        if mo:
            try:
                return datetime(year=int(y), month=mo, day=int(d))
            except ValueError:
                return None

    return None


# ── Tender card parsing ───────────────────────────────────────────


async def fetch_tender_card(
    reg_number: str,
    user_agent: str = DEFAULT_USER_AGENT,
) -> ParsedTender | None:
    """Fetch and parse a single tender card (common-info.html)."""
    url = (
        f"{ZAKUPKI_BASE}/epz/order/notice/zk20/view/common-info.html"
        f"?regNumber={reg_number}"
    )
    async with _build_client(user_agent) as client:
        resp = await _fetch_with_retry(client, url)
        return _parse_tender_card(html=resp.text, reg_number=reg_number, source_url=str(resp.url))


def _parse_tender_card(html: str, reg_number: str, source_url: str) -> ParsedTender | None:
    """Parse tender card HTML."""
    soup = BeautifulSoup(html, "lxml")

    # Title (in <h1> or .cardMainInfo__title)
    title_el = soup.select_one("h1, .cardMainInfo__title, .notice-main-info__title")
    title = title_el.get_text(strip=True) if title_el else f"Тендер {reg_number}"

    # Customer + INN
    customer = None
    customer_inn = None
    customer_org_id = None
    customer_org_code = None
    cust_block = soup.select_one(".customer, .orgName, .cardMainInfo__customer")
    if cust_block:
        customer = cust_block.get_text(strip=True)
        inn_link = cust_block.select_one("a[href*='inn=']") or cust_block
        if inn_link:
            inn_match = re.search(r"inn[=:]?\s*(\d{10,12})", str(inn_link))
            if inn_match:
                customer_inn = inn_match.group(1)

    # Customer identifiers (3 of them in EIS!)
    org_id_el = soup.select_one("[data-organization-id], [data-org-id]")
    if org_id_el:
        customer_org_id = (
            org_id_el.get("data-organization-id")
            or org_id_el.get("data-org-id")
        )
    org_code_el = soup.select_one("[data-organization-code], [data-org-code]")
    if org_code_el:
        customer_org_code = (
            org_code_el.get("data-organization-code")
            or org_code_el.get("data-org-code")
        )

    # Price (NMCCK from common-info.html — NOT from contract card!)
    price = None
    price_block = soup.select_one(".price-block__value, .cost, .price")
    if price_block:
        raw = price_block.get_text(" ", strip=True)
        nums = re.findall(r"[\d][\d\s\u00a0]*[.,]?\d*", raw)
        for n in nums:
            cleaned = n.replace(" ", "").replace("\u00a0", "").replace(",", ".")
            try:
                price = float(cleaned)
                break
            except ValueError:
                continue

    # Deadline
    deadline = None
    deadline_el = soup.select_one(".endDate, .submission, .deadline")
    if deadline_el:
        deadline = _parse_russian_date(deadline_el.get_text(strip=True))

    # Published at
    published_at = None
    pub_el = soup.select_one(".publishDate, .publicationDate")
    if pub_el:
        published_at = _parse_russian_date(pub_el.get_text(strip=True))

    # Region
    region = None
    region_el = soup.select_one(".region, .address")
    if region_el:
        region = region_el.get_text(strip=True)

    return ParsedTender(
        reg_number=reg_number,
        title=title,
        customer=customer,
        customer_inn=customer_inn,
        customer_org_id=customer_org_id,
        customer_org_code=customer_org_code,
        price=price,
        deadline=deadline,
        published_at=published_at,
        law_type="44-ФЗ",
        region=region,
        source_url=source_url,
        raw_html=html[:100_000],  # truncate for storage
    )


# ── Document listing & download ───────────────────────────────────


async def fetch_tender_documents(
    reg_number: str,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[dict[str, Any]]:
    """Get list of documents attached to a tender.

    URL: /epz/order/notice/zk20/view/documents.html?regNumber=XXX

    Returns list of dicts: {eis_document_id, filename, file_url, file_type}
    """
    url = (
        f"{ZAKUPKI_BASE}/epz/order/notice/zk20/view/documents.html"
        f"?regNumber={reg_number}"
    )
    async with _build_client(user_agent) as client:
        resp = await _fetch_with_retry(client, url)
        return _parse_documents_listing(resp.text)


def _parse_documents_listing(html: str) -> list[dict[str, Any]]:
    """Extract document list from documents.html."""
    soup = BeautifulSoup(html, "lxml")
    docs: list[dict[str, Any]] = []

    # Each document is in a row of a table (or block wrapper).
    # Link format: /epz/main/public/download/downloadDocument.html?id=NNNNN
    for link in soup.select("a[href*='downloadDocument.html']"):
        href = link.get("href", "")
        id_match = re.search(r"id=(\d+)", href)
        if not id_match:
            continue
        eis_id = id_match.group(1)
        filename = link.get_text(strip=True) or f"document_{eis_id}"

        # File type from icon class or filename
        file_type = ""
        if "." in filename:
            file_type = filename.rsplit(".", 1)[-1].lower()
        if not file_type:
            # Try to detect from icon class (e.g., fileIcon-pdf, fileIcon-docx)
            icon = link.find_previous("span", class_=lambda c: c and "fileIcon" in c)
            if icon:
                cls = " ".join(icon.get("class", []))
                m = re.search(r"fileIcon-(\w+)", cls)
                if m:
                    file_type = m.group(1).lower()

        docs.append({
            "eis_document_id": eis_id,
            "filename": filename,
            "file_url": href if href.startswith("http") else f"{ZAKUPKI_BASE}{href}",
            "file_type": file_type,
        })

    return docs


async def download_document(
    document_id: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 60.0,
) -> bytes:
    """Download a single document by EIS documentId.

    Returns raw bytes. Caller is responsible for parsing + storing.
    """
    url = (
        f"{ZAKUPKI_BASE}/epz/main/public/download/downloadDocument.html"
        f"?id={document_id}"
    )
    async with _build_client(user_agent, timeout=timeout) as client:
        resp = await _fetch_with_retry(client, url)
        return resp.content


async def download_and_parse_documents(
    reg_number: str,
    user_agent: str = DEFAULT_USER_AGENT,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress_callback=None,
) -> list[tuple[dict[str, Any], ParsedDocument]]:
    """Download all documents for a tender and parse them in parallel.

    Returns list of (doc_meta, parsed_document).
    """
    docs = await fetch_tender_documents(reg_number, user_agent)
    sem = asyncio.Semaphore(concurrency)
    out: list[tuple[dict[str, Any], ParsedDocument]] = []

    async def _one(meta: dict[str, Any]) -> None:
        async with sem:
            try:
                data = await download_document(
                    meta["eis_document_id"], user_agent=user_agent
                )
                parsed = extract_text(meta["filename"], data)
                meta["file_size"] = len(data)
                out.append((meta, parsed))
            except Exception as e:
                logger.error("Failed to download/parse %s: %s", meta["filename"], e)
                # Append a placeholder for visibility
                out.append(
                    (
                        meta,
                        ParsedDocument(
                            plain_text="",
                            text_length=0,
                            content_hash="",
                            parse_status="failed",
                            parse_error=str(e)[:500],
                        ),
                    )
                )
            if progress_callback:
                try:
                    progress_callback(len(out), len(docs))
                except Exception:
                    pass

    await asyncio.gather(*[_one(d) for d in docs])
    return out


# ── DB persistence ────────────────────────────────────────────────


async def upsert_tender_with_documents(
    parsed: ParsedTender,
    document_results: list[tuple[dict[str, Any], ParsedDocument]],
    db: AsyncSession,
) -> Tender:
    """Insert or update a Tender + its TenderDocuments in the DB.

    - Looks up by external_id (= regNumber).
    - Creates TenderDocument rows for each (filename, content_hash).
    - Skips re-parsing if content_hash already seen (cache hit).
    """
    # 1) Upsert Tender
    result = await db.execute(
        select(Tender).where(Tender.external_id == parsed.reg_number)
    )
    tender = result.scalar_one_or_none()
    if tender is None:
        tender = Tender(
            external_id=parsed.reg_number,
            title=parsed.title,
            description=parsed.title,  # enriched later by AI
            customer=parsed.customer,
            price=parsed.price,
            deadline=parsed.deadline,
            law_type=parsed.law_type,
            region=parsed.region,
            source_url=parsed.source_url,
            published_at=parsed.published_at or datetime.utcnow(),
            currency="RUB",
            status="active",
        )
        db.add(tender)
        await db.flush()
    else:
        # Update mutable fields
        tender.title = parsed.title
        tender.customer = parsed.customer
        if parsed.price is not None:
            tender.price = parsed.price
        if parsed.deadline:
            tender.deadline = parsed.deadline
        if parsed.region:
            tender.region = parsed.region
        tender.source_url = parsed.source_url
        db.add(tender)
        await db.flush()

    # 2) Upsert each TenderDocument (dedup by content_hash)
    existing_hashes = set(
        (
            await db.execute(
                select(TenderDocument.content_hash).where(
                    TenderDocument.content_hash.in_(
                        [pr.content_hash for _, pr in document_results if pr.content_hash]
                    )
                )
            )
        )
        .scalars()
        .all()
    )

    for meta, parsed_doc in document_results:
        if not parsed_doc.content_hash:
            continue
        if parsed_doc.content_hash in existing_hashes:
            # Cache hit: link existing document to this tender via a new row
            # (or skip if we want to dedup rows — here we skip to keep docs 1:1)
            logger.debug(
                "Content cache hit for hash=%s (skipped re-insert)",
                parsed_doc.content_hash[:8],
            )
            continue

        td = TenderDocument(
            tender_id=tender.id,
            eis_document_id=meta.get("eis_document_id"),
            filename=meta["filename"],
            file_type=meta.get("file_type"),
            file_size=meta.get("file_size"),
            file_url=meta.get("file_url"),
            content_hash=parsed_doc.content_hash,
            extracted_text=parsed_doc.plain_text[:100_000],  # truncate to ~100k
            text_length=parsed_doc.text_length,
            download_status="downloaded",
            parse_status=parsed_doc.parse_status,
            parse_error=parsed_doc.parse_error,
            parsed_at=datetime.utcnow() if parsed_doc.parse_status == "parsed" else None,
        )
        db.add(td)

    await db.flush()
    await db.refresh(tender)
    return tender


# ── High-level orchestration ──────────────────────────────────────


async def sync_tender(
    reg_number: str,
    db: AsyncSession | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    skip_documents: bool = False,
) -> Tender | None:
    """High-level: fetch card + docs, parse, upsert to DB.

    If `db` is None, opens a session internally.
    """
    card = await fetch_tender_card(reg_number, user_agent)
    if not card:
        return None

    document_results: list[tuple[dict[str, Any], ParsedDocument]] = []
    if not skip_documents:
        try:
            document_results = await download_and_parse_documents(
                reg_number, user_agent
            )
        except Exception as e:
            logger.error("Document download failed for %s: %s", reg_number, e)

    if db is None:
        async with async_session() as session:
            tender = await upsert_tender_with_documents(card, document_results, session)
            await session.commit()
            return tender
    else:
        return await upsert_tender_with_documents(card, document_results, db)


async def sync_recent_tenders(
    query: str = "",
    *,
    max_tenders: int = 50,
    user_agent: str = DEFAULT_USER_AGENT,
) -> int:
    """Sync the most recent N tenders matching the query.

    Returns number of tenders successfully synced.
    """
    page = await search_tenders_44fz(query, page=1, records_per_page=max_tenders)
    count = 0
    for stub in page.tenders:
        reg_number = stub.get("reg_number")
        if not reg_number:
            continue
        try:
            tender = await sync_tender(reg_number, user_agent=user_agent)
            if tender:
                count += 1
        except Exception as e:
            logger.error("Failed to sync tender %s: %s", reg_number, e)
    return count


# ── Module exports ───────────────────────────────────────────────

__all__ = [
    "DEFAULT_USER_AGENT",
    "DEFAULT_CONCURRENCY",
    "EmptyResponseError",
    "ParsedSearchPage",
    "ParsedTender",
    "download_and_parse_documents",
    "download_document",
    "fetch_tender_card",
    "fetch_tender_documents",
    "search_tenders_44fz",
    "sync_recent_tenders",
    "sync_tender",
    "upsert_tender_with_documents",
]