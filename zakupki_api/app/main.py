import hashlib
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from xml.etree import ElementTree as ET


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://zakupki:zakupki_secret@zakupki-postgres:5432/zakupki_cache",
)
FILE_STORAGE_DIR = Path(os.getenv("FILE_STORAGE_DIR", "/data/files"))
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "zakupki-internal-secret-change-in-prod")

ZAKUPKI_SYNC_ENABLED = os.getenv("ZAKUPKI_SYNC_ENABLED", "true").lower() == "true"
ZAKUPKI_SYNC_HOUR = int(os.getenv("ZAKUPKI_SYNC_HOUR", "2"))
ZAKUPKI_SYNC_MINUTE = int(os.getenv("ZAKUPKI_SYNC_MINUTE", "15"))
ZAKUPKI_SEARCH_QUERY = os.getenv("ZAKUPKI_SEARCH_QUERY", "стройка")
ZAKUPKI_RECORDS_PER_PAGE = os.getenv("ZAKUPKI_RECORDS_PER_PAGE", "_50")
ZAKUPKI_INITIAL_MAX_PAGES = int(os.getenv("ZAKUPKI_INITIAL_MAX_PAGES", "20"))
ZAKUPKI_REQUEST_DELAY_SECONDS = float(os.getenv("ZAKUPKI_REQUEST_DELAY_SECONDS", "1.2"))

BASE_URL = "https://zakupki.gov.ru"

HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

HEADERS_FILE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


class Base(DeclarativeBase):
    pass


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    law_type: Mapped[str] = mapped_column(String(20), index=True)
    reg_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    xml_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    documents_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    purchase_object: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    placing_way: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_date_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    end_date_text: Mapped[str | None] = mapped_column(String(100), nullable=True)

    raw_xml: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["PurchaseDocument"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")


class PurchaseDocument(Base):
    __tablename__ = "purchase_documents"
    __table_args__ = (
        UniqueConstraint("reg_number", "uid", name="uq_purchase_documents_reg_uid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reg_number: Mapped[str] = mapped_column(String(40), ForeignKey("purchases.reg_number", ondelete="CASCADE"), index=True)
    uid: Mapped[str] = mapped_column(String(80), index=True)
    download_url: Mapped[str] = mapped_column(Text)

    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_name: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(20))
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))

    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    purchase: Mapped[Purchase] = relationship(back_populates="documents")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query: Mapped[str] = mapped_column(Text)
    max_pages: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="running")
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    parsed_count: Mapped[int] = mapped_column(Integer, default=0)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

app = FastAPI(title="Internal Zakupki API", version="0.1.0")


def db_session() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_token(x_internal_token: str | None = Header(default=None)) -> None:
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid internal token")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_space(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def first_xml_text(root: ET.Element, names: list[str]) -> str:
    wanted = set(names)
    for elem in root.iter():
        if local_name(elem.tag) in wanted:
            text = normalize_space("".join(elem.itertext()))
            if text:
                return text
    return ""


def parse_decimal(value: str) -> float | None:
    value = normalize_space(value)
    if not value:
        return None
    value = value.replace(" ", "").replace(",", ".")
    match = re.search(r"\d+(\.\d+)?", value)
    if not match:
        return None
    return float(match.group(0))


def detect_file_extension(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return ".pdf"

    if data.startswith(b"\xD0\xCF\x11\xE0"):
        return ".doc_or_xls"

    if data.startswith(b"PK\x03\x04"):
        return detect_office_zip_extension(data)

    return ".bin"


def detect_office_zip_extension(data: bytes) -> str:
    temp_path = FILE_STORAGE_DIR / "_tmp_detect_office.zip"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(data)

    try:
        with zipfile.ZipFile(temp_path, "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" in names:
                if any(name.startswith("word/") for name in names):
                    return ".docx"
                if any(name.startswith("xl/") for name in names):
                    return ".xlsx"
                if any(name.startswith("ppt/") for name in names):
                    return ".pptx"
            return ".zip"
    except zipfile.BadZipFile:
        return ".bin"
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def safe_filename(name: str) -> str:
    name = normalize_space(name)
    if not name:
        return ""

    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "_")

    name = name.strip(" .")
    if len(name) > 180:
        base, ext = os.path.splitext(name)
        name = base[:150] + ext

    return name


def build_search_url(query: str, page: int) -> str:
    params = httpx.QueryParams(
        {
            "searchString": query,
            "morphology": "on",
            "search-filter": "Дата размещения",
            "pageNumber": str(page),
            "recordsPerPage": ZAKUPKI_RECORDS_PER_PAGE,
            "sortBy": "UPDATE_DATE",
            "sortDirection": "false",
            "fz44": "on",
            "fz223": "on",
            "af": "on",
            "ca": "on",
            "pc": "on",
        }
    )
    return f"{BASE_URL}/epz/order/extendedsearch/results.html?{params}"


def absolute_url(url: str) -> str:
    url = url.replace("&amp;", "&")
    if url.startswith("http://zakupki.gov.ru"):
        return url.replace("http://zakupki.gov.ru", "https://zakupki.gov.ru", 1)
    if url.startswith("https://"):
        return url
    if url.startswith("/"):
        return BASE_URL + url
    return BASE_URL + "/" + url


def extract_purchase_links(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = absolute_url(a["href"])

        law_type = ""
        if "/epz/order/notice/" in href and "common-info.html?regNumber=" in href:
            law_type = "44-FZ"
        elif "/223/purchase/public/purchase/info/common-info.html?regNumber=" in href:
            law_type = "223-FZ"

        if not law_type:
            continue

        match = re.search(r"regNumber=(\d+)", href)
        if not match:
            continue

        reg_number = match.group(1)

        key = f"{law_type}:{reg_number}"
        if key in seen:
            continue

        seen.add(key)
        result.append(
            {
                "law_type": law_type,
                "reg_number": reg_number,
                "source_url": href,
            }
        )

    return result


def build_documents_url(source_url: str) -> str:
    if "/common-info.html" in source_url:
        return source_url.replace("/common-info.html", "/documents.html")
    return source_url


def build_xml_url(reg_number: str) -> str:
    return f"{BASE_URL}/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}"


def parse_44fz_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text.encode("utf-8"))

    purchase_object = first_xml_text(
        root,
        [
            "purchaseObjectInfo",
            "purchaseObject",
            "objectInfo",
            "name",
        ],
    )

    customer_name = first_xml_text(
        root,
        [
            "fullName",
            "customerFullName",
            "organizationFullName",
        ],
    )

    price_text = first_xml_text(
        root,
        [
            "maxPrice",
            "initialSum",
            "price",
            "initialContractPrice",
        ],
    )

    return {
        "purchase_object": purchase_object,
        "customer_name": customer_name,
        "max_price": parse_decimal(price_text),
        "currency": first_xml_text(root, ["currencyCode", "currency"]),
        "placing_way": first_xml_text(root, ["placingWayName", "placingWay"]),
        "status": first_xml_text(root, ["state", "status", "orderState"]),
        "publish_date_text": first_xml_text(root, ["publishDate", "placingDate", "docPublishDate"]),
        "end_date_text": first_xml_text(root, ["endDate", "collectingEndDate", "submissionCloseDateTime"]),
    }


def extract_document_links(documents_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(documents_html, "lxml")
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = absolute_url(a["href"])
        if not re.search(r"/filestore/.*/download/.*/file\.html\?uid=", href):
            continue

        uid_match = re.search(r"uid=([A-Za-z0-9]+)", href)
        if not uid_match:
            continue

        uid = uid_match.group(1)
        if uid in seen:
            continue

        seen.add(uid)

        label = normalize_space(a.get_text(" ", strip=True))
        result.append(
            {
                "uid": uid,
                "download_url": href,
                "label": label,
            }
        )

    return result


def download_document(client: httpx.Client, reg_number: str, doc: dict[str, str]) -> dict[str, Any]:
    uid = doc["uid"]
    url = doc["download_url"]

    response = client.get(url, headers=HEADERS_FILE, timeout=120)
    response.raise_for_status()

    content = response.content
    sha256 = hashlib.sha256(content).hexdigest()
    extension = detect_file_extension(content)

    original_name = ""
    content_disposition = response.headers.get("content-disposition", "")
    filename_match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition)
    if filename_match:
        original_name = httpx.URL(f"http://x/?v={filename_match.group(1)}").params["v"]
    else:
        filename_match = re.search(r'filename="?([^";]+)"?', content_disposition)
        if filename_match:
            original_name = filename_match.group(1)

    original_name = safe_filename(original_name)

    if not original_name:
        label = safe_filename(doc.get("label", ""))
        if label:
            original_name = label + extension
        else:
            original_name = uid + extension

    if not os.path.splitext(original_name)[1]:
        original_name = original_name + extension

    reg_dir = FILE_STORAGE_DIR / reg_number
    reg_dir.mkdir(parents=True, exist_ok=True)

    local_name = f"{uid}_{original_name}"
    local_name = safe_filename(local_name)
    local_path = reg_dir / local_name
    local_path.write_bytes(content)

    return {
        "uid": uid,
        "download_url": url,
        "original_name": original_name,
        "local_name": local_name,
        "local_path": str(local_path),
        "extension": os.path.splitext(local_name)[1].lower(),
        "content_type": response.headers.get("content-type", ""),
        "size_bytes": len(content),
        "sha256": sha256,
    }


def upsert_purchase(db: Session, item: dict[str, str]) -> Purchase:
    purchase = db.scalar(select(Purchase).where(Purchase.reg_number == item["reg_number"]))

    if purchase is None:
        purchase = Purchase(
            law_type=item["law_type"],
            reg_number=item["reg_number"],
            source_url=item["source_url"],
            first_seen_at=now_utc(),
            last_seen_at=now_utc(),
        )
        db.add(purchase)
    else:
        purchase.law_type = item["law_type"]
        purchase.source_url = item["source_url"]
        purchase.last_seen_at = now_utc()

    return purchase


def upsert_document(db: Session, reg_number: str, doc_data: dict[str, Any]) -> PurchaseDocument:
    document = db.scalar(
        select(PurchaseDocument).where(
            PurchaseDocument.reg_number == reg_number,
            PurchaseDocument.uid == doc_data["uid"],
        )
    )

    if document is None:
        document = PurchaseDocument(reg_number=reg_number, uid=doc_data["uid"])
        db.add(document)

    document.download_url = doc_data["download_url"]
    document.original_name = doc_data["original_name"]
    document.local_name = doc_data["local_name"]
    document.local_path = doc_data["local_path"]
    document.extension = doc_data["extension"]
    document.content_type = doc_data["content_type"]
    document.size_bytes = doc_data["size_bytes"]
    document.sha256 = doc_data["sha256"]
    document.downloaded_at = now_utc()

    return document


def sync_zakupki(query: str, max_pages: int) -> dict[str, Any]:
    FILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    run = SyncRun(query=query, max_pages=max_pages, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    found_count = 0
    parsed_count = 0
    document_count = 0

    try:
        with httpx.Client(headers=HEADERS_HTML, follow_redirects=True, timeout=90) as client:
            client.get(f"{BASE_URL}/epz/main/public/home.html")

            for page in range(1, max_pages + 1):
                search_url = build_search_url(query, page)
                search_response = client.get(search_url)
                search_response.raise_for_status()

                html = search_response.text
                links = extract_purchase_links(html)

                if not links:
                    break

                found_count += len(links)

                for item in links:
                    purchase = upsert_purchase(db, item)
                    purchase.raw_html = html
                    purchase.documents_url = build_documents_url(item["source_url"])

                    if item["law_type"] == "44-FZ":
                        xml_url = build_xml_url(item["reg_number"])
                        purchase.xml_url = xml_url

                        try:
                            xml_response = client.get(xml_url)
                            xml_response.raise_for_status()
                            xml_text = xml_response.text
                            parsed = parse_44fz_xml(xml_text)

                            purchase.raw_xml = xml_text
                            purchase.purchase_object = parsed["purchase_object"]
                            purchase.customer_name = parsed["customer_name"]
                            purchase.max_price = parsed["max_price"]
                            purchase.currency = parsed["currency"]
                            purchase.placing_way = parsed["placing_way"]
                            purchase.status = parsed["status"]
                            purchase.publish_date_text = parsed["publish_date_text"]
                            purchase.end_date_text = parsed["end_date_text"]
                            purchase.parsed_at = now_utc()
                            parsed_count += 1
                        except Exception as exc:
                            purchase.status = f"XML_PARSE_ERROR: {type(exc).__name__}: {exc}"

                    try:
                        documents_response = client.get(purchase.documents_url)
                        documents_response.raise_for_status()
                        documents = extract_document_links(documents_response.text)

                        for doc in documents:
                            try:
                                doc_data = download_document(client, item["reg_number"], doc)
                                upsert_document(db, item["reg_number"], doc_data)
                                document_count += 1
                                time.sleep(ZAKUPKI_REQUEST_DELAY_SECONDS)
                            except Exception:
                                continue
                    except Exception:
                        pass

                    db.commit()
                    time.sleep(ZAKUPKI_REQUEST_DELAY_SECONDS)

                time.sleep(ZAKUPKI_REQUEST_DELAY_SECONDS)

        run.status = "success"
        run.found_count = found_count
        run.parsed_count = parsed_count
        run.document_count = document_count
        run.finished_at = now_utc()
        db.commit()

        return {
            "status": "success",
            "run_id": run.id,
            "found_count": found_count,
            "parsed_count": parsed_count,
            "document_count": document_count,
        }

    except Exception as exc:
        run.status = "error"
        run.error_text = f"{type(exc).__name__}: {exc}"
        run.found_count = found_count
        run.parsed_count = parsed_count
        run.document_count = document_count
        run.finished_at = now_utc()
        db.commit()

        return {
            "status": "error",
            "run_id": run.id,
            "error": run.error_text,
            "found_count": found_count,
            "parsed_count": parsed_count,
            "document_count": document_count,
        }
    finally:
        db.close()


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    FILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    if ZAKUPKI_SYNC_ENABLED:
        scheduler = BackgroundScheduler(timezone="Europe/Moscow")
        scheduler.add_job(
            lambda: sync_zakupki(ZAKUPKI_SEARCH_QUERY, ZAKUPKI_INITIAL_MAX_PAGES),
            trigger="cron",
            hour=ZAKUPKI_SYNC_HOUR,
            minute=ZAKUPKI_SYNC_MINUTE,
            id="nightly_zakupki_sync",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        app.state.scheduler = scheduler


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "zakupki-api",
        "time": now_utc().isoformat(),
    }


@app.post("/api/v1/sync/run", dependencies=[Depends(require_token)])
def run_sync(
    query: str = Query(default=ZAKUPKI_SEARCH_QUERY),
    max_pages: int = Query(default=ZAKUPKI_INITIAL_MAX_PAGES, ge=1, le=500),
) -> dict[str, Any]:
    return sync_zakupki(query=query, max_pages=max_pages)


@app.get("/api/v1/sync/runs", dependencies=[Depends(require_token)])
def list_sync_runs(db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    rows = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(50)).all()

    return [
        {
            "id": row.id,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "query": row.query,
            "max_pages": row.max_pages,
            "status": row.status,
            "found_count": row.found_count,
            "parsed_count": row.parsed_count,
            "document_count": row.document_count,
            "error_text": row.error_text,
        }
        for row in rows
    ]


@app.get("/api/v1/purchases", dependencies=[Depends(require_token)])
def list_purchases(
    q: str = Query(default=""),
    law_type: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    stmt = select(Purchase).order_by(Purchase.last_seen_at.desc()).offset(offset).limit(limit)

    if q:
        like = f"%{q}%"
        stmt = (
            select(Purchase)
            .where(
                (Purchase.reg_number.ilike(like))
                | (Purchase.purchase_object.ilike(like))
                | (Purchase.customer_name.ilike(like))
            )
            .order_by(Purchase.last_seen_at.desc())
            .offset(offset)
            .limit(limit)
        )

    if law_type:
        stmt = stmt.where(Purchase.law_type == law_type)

    rows = db.scalars(stmt).all()

    return {
        "items": [
            {
                "reg_number": row.reg_number,
                "law_type": row.law_type,
                "source_url": row.source_url,
                "xml_url": row.xml_url,
                "documents_url": row.documents_url,
                "purchase_object": row.purchase_object,
                "customer_name": row.customer_name,
                "max_price": float(row.max_price) if row.max_price is not None else None,
                "currency": row.currency,
                "placing_way": row.placing_way,
                "status": row.status,
                "publish_date_text": row.publish_date_text,
                "end_date_text": row.end_date_text,
                "first_seen_at": row.first_seen_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "parsed_at": row.parsed_at.isoformat() if row.parsed_at else None,
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/purchases/{reg_number}", dependencies=[Depends(require_token)])
def get_purchase(reg_number: str, db: Session = Depends(db_session)) -> dict[str, Any]:
    purchase = db.scalar(select(Purchase).where(Purchase.reg_number == reg_number))

    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")

    return {
        "reg_number": purchase.reg_number,
        "law_type": purchase.law_type,
        "source_url": purchase.source_url,
        "xml_url": purchase.xml_url,
        "documents_url": purchase.documents_url,
        "purchase_object": purchase.purchase_object,
        "customer_name": purchase.customer_name,
        "max_price": float(purchase.max_price) if purchase.max_price is not None else None,
        "currency": purchase.currency,
        "placing_way": purchase.placing_way,
        "status": purchase.status,
        "publish_date_text": purchase.publish_date_text,
        "end_date_text": purchase.end_date_text,
        "first_seen_at": purchase.first_seen_at.isoformat(),
        "last_seen_at": purchase.last_seen_at.isoformat(),
        "parsed_at": purchase.parsed_at.isoformat() if purchase.parsed_at else None,
        "documents": [
            {
                "id": doc.id,
                "uid": doc.uid,
                "original_name": doc.original_name,
                "local_name": doc.local_name,
                "extension": doc.extension,
                "content_type": doc.content_type,
                "size_bytes": doc.size_bytes,
                "sha256": doc.sha256,
                "download_url": doc.download_url,
                "downloaded_at": doc.downloaded_at.isoformat(),
                "internal_download_url": f"/api/v1/documents/{doc.id}/download",
            }
            for doc in purchase.documents
        ],
    }


@app.get("/api/v1/documents/{document_id}/download", dependencies=[Depends(require_token)])
def download_cached_document(document_id: int, db: Session = Depends(db_session)) -> FileResponse:
    doc = db.scalar(select(PurchaseDocument).where(PurchaseDocument.id == document_id))

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    path = Path(doc.local_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Document file not found on disk")

    return FileResponse(
        path=path,
        filename=doc.original_name or doc.local_name,
        media_type=doc.content_type or "application/octet-stream",
    )
