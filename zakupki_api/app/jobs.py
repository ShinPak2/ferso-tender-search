import hashlib
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session
from xml.etree import ElementTree as ET

from app.models import (
    ParseTask,
    Purchase,
    PurchaseDocument,
    SessionLocal,
    SyncRun,
    utc_now,
)


BASE_URL = "https://zakupki.gov.ru"

FILE_STORAGE_DIR = Path(os.getenv("FILE_STORAGE_DIR", "/data/files"))

ZAKUPKI_RECORDS_PER_PAGE = os.getenv("ZAKUPKI_RECORDS_PER_PAGE", "_50")
ZAKUPKI_DISCOVERY_MAX_PAGES = int(os.getenv("ZAKUPKI_DISCOVERY_MAX_PAGES", "20"))
ZAKUPKI_PARSE_BATCH_SIZE = int(os.getenv("ZAKUPKI_PARSE_BATCH_SIZE", "20"))
ZAKUPKI_REQUEST_DELAY_SECONDS = float(os.getenv("ZAKUPKI_REQUEST_DELAY_SECONDS", "1.2"))
ZAKUPKI_MAX_TASK_ATTEMPTS = int(os.getenv("ZAKUPKI_MAX_TASK_ATTEMPTS", "5"))

STORE_RAW_COMMON_HTML = os.getenv("STORE_RAW_COMMON_HTML", "false").lower() == "true"
STORE_RAW_PRINT_HTML = os.getenv("STORE_RAW_PRINT_HTML", "false").lower() == "true"
STORE_RAW_XML = os.getenv("STORE_RAW_XML", "true").lower() == "true"

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


def normalize_space(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def parse_decimal(value: str | None) -> float | None:
    value = normalize_space(value)
    if not value:
        return None

    value = value.replace("\u00a0", " ")
    value = re.sub(r"[^\d,.\s]", " ", value)
    value = normalize_space(value)
    value = value.replace(" ", "").replace(",", ".")

    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def absolute_url(url: str) -> str:
    url = url.replace("&amp;", "&")

    if url.startswith("http://zakupki.gov.ru"):
        return url.replace("http://zakupki.gov.ru", "https://zakupki.gov.ru", 1)

    if url.startswith("https://"):
        return url

    if url.startswith("/"):
        return BASE_URL + url

    return BASE_URL + "/" + url


def build_discovery_url(page: int) -> str:
    params = httpx.QueryParams(
        {
            "morphology": "on",
            "search-filter": "Дата размещения",
            "pageNumber": str(page),
            "recordsPerPage": ZAKUPKI_RECORDS_PER_PAGE,
            "showLotsInfoHidden": "false",
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


def build_xml_url(reg_number: str) -> str:
    return f"{BASE_URL}/epz/order/notice/printForm/viewXml.html?regNumber={reg_number}"


def build_print_form_url(reg_number: str) -> str:
    return f"{BASE_URL}/epz/order/notice/printForm/view.html?regNumber={reg_number}"


def build_documents_url(source_url: str) -> str:
    if "/common-info.html" in source_url:
        return source_url.replace("/common-info.html", "/documents.html")
    return source_url


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


def clean_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)
    text = text.replace("\u00a0", " ")
    text = text.replace("&#034;", '"')
    text = text.replace("&quot;", '"')
    text = text.replace("&amp;", "&")
    return normalize_space(text)


def find_value_by_labels(text: str, labels: list[str], max_len: int = 600) -> str:
    for label in labels:
        pattern = re.escape(label) + r"\s*:?\s*(.{1," + str(max_len) + r"})"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = normalize_space(match.group(1))
            value = cut_at_next_label(value)
            if value:
                return value
    return ""


def cut_at_next_label(value: str) -> str:
    labels = [
        "Заказчик",
        "Организация",
        "Объект закупки",
        "Наименование объекта",
        "Начальная",
        "Максимальная",
        "Цена",
        "Валюта",
        "Дата",
        "Способ",
        "Статус",
        "Этап",
        "Преимущества",
        "Требования",
        "Ограничения",
        "Обеспечение",
        "Контактная информация",
    ]

    best = len(value)

    for label in labels:
        idx = value.lower().find(label.lower())
        if 20 < idx < best:
            best = idx

    return normalize_space(value[:best])


def find_first_date_after_labels(text: str, labels: list[str]) -> str:
    for label in labels:
        idx = text.lower().find(label.lower())
        if idx < 0:
            continue

        chunk = text[idx : idx + 500]
        match = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?", chunk)
        if match:
            return match.group(0)

        match = re.search(r"\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?)?", chunk)
        if match:
            return match.group(0)

    return ""


def find_price(text: str) -> float | None:
    patterns = [
        r"Начальная\s*\(максимальная\)\s*цена[^0-9]{0,120}([\d\s]+[,.]\d{1,2})",
        r"НМЦК[^0-9]{0,120}([\d\s]+[,.]\d{1,2})",
        r"Цена\s+контракта[^0-9]{0,120}([\d\s]+[,.]\d{1,2})",
        r"Максимальное\s+значение\s+цены[^0-9]{0,120}([\d\s]+[,.]\d{1,2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_decimal(match.group(1))
            if parsed is not None:
                return parsed

    return None


def parse_html_purchase(html: str) -> dict[str, Any]:
    text = clean_html_text(html)

    reg_match = re.search(r"\b\d{11,20}\b", text)

    purchase_object = find_value_by_labels(
        text,
        [
            "Объект закупки",
            "Наименование объекта закупки",
            "Наименование закупки",
            "Предмет контракта",
        ],
        max_len=900,
    )

    customer_name = find_value_by_labels(
        text,
        [
            "Заказчик",
            "Наименование заказчика",
            "Организация, осуществляющая размещение",
            "Размещено",
        ],
        max_len=700,
    )

    placing_way = find_value_by_labels(
        text,
        [
            "Способ определения поставщика",
            "Способ закупки",
            "Способ размещения закупки",
        ],
        max_len=500,
    )

    status = find_value_by_labels(
        text,
        [
            "Статус закупки",
            "Статус",
            "Этап закупки",
        ],
        max_len=300,
    )

    inn = ""
    inn_match = re.search(r"\bИНН\b\s*:?\s*(\d{10}|\d{12})", text, flags=re.IGNORECASE)
    if inn_match:
        inn = inn_match.group(1)

    kpp = ""
    kpp_match = re.search(r"\bКПП\b\s*:?\s*(\d{9})", text, flags=re.IGNORECASE)
    if kpp_match:
        kpp = kpp_match.group(1)

    currency = ""
    if "Российский рубль" in text or re.search(r"\bруб", text, flags=re.IGNORECASE):
        currency = "RUB"

    return {
        "reg_number": reg_match.group(0) if reg_match else "",
        "purchase_object": purchase_object,
        "customer_name": customer_name,
        "customer_inn": inn,
        "customer_kpp": kpp,
        "max_price": find_price(text),
        "currency": currency,
        "placing_way": placing_way,
        "purchase_status": status,
        "publish_date_text": find_first_date_after_labels(
            text,
            [
                "Дата размещения",
                "Размещено",
                "Дата публикации",
            ],
        ),
        "updated_date_text": find_first_date_after_labels(
            text,
            [
                "Дата обновления",
                "Последнее изменение",
                "Обновлено",
            ],
        ),
        "submission_end_date_text": find_first_date_after_labels(
            text,
            [
                "Дата и время окончания срока подачи заявок",
                "Дата окончания подачи заявок",
                "Окончание подачи заявок",
            ],
        ),
        "contract_end_date_text": find_first_date_after_labels(
            text,
            [
                "Срок исполнения контракта",
                "Дата окончания исполнения контракта",
                "Сроки поставки товара",
            ],
        ),
    }


def local_xml_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def first_xml_text(root: ET.Element, names: list[str]) -> str:
    wanted = set(names)

    for elem in root.iter():
        if local_xml_name(elem.tag) in wanted:
            text = normalize_space("".join(elem.itertext()))
            if text:
                return text

    return ""


def all_xml_texts(root: ET.Element, name: str) -> list[str]:
    result: list[str] = []

    for elem in root.iter():
        if local_xml_name(elem.tag) == name:
            text = normalize_space("".join(elem.itertext()))
            if text:
                result.append(text)

    return result


def try_parse_44fz_xml(reg_number: str, xml_text: str) -> tuple[bool, dict[str, Any], str]:
    text = xml_text.strip()

    if len(text) < 100:
        return False, {}, "too_small_response"

    if "Запрашиваемая страница не существует" in text:
        return False, {}, "page_not_exists_html"

    if not (text.startswith("<?xml") or text.startswith("<")):
        return False, {}, "not_xml_like"

    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        return False, {}, f"xml_parse_error: {exc}"

    purchase_number = first_xml_text(root, ["purchaseNumber"])

    if not purchase_number:
        return False, {}, "missing_purchaseNumber"

    if purchase_number != reg_number:
        return False, {}, f"purchaseNumber_mismatch: expected={reg_number}, actual={purchase_number}"

    full_names = all_xml_texts(root, "fullName")
    customer_name = full_names[-1] if full_names else ""

    price_text = first_xml_text(
        root,
        [
            "maxPrice",
            "initialSum",
            "price",
            "initialContractPrice",
        ],
    )

    parsed = {
        "purchase_object": first_xml_text(
            root,
            [
                "purchaseObjectInfo",
                "purchaseObject",
                "objectInfo",
                "name",
            ],
        ),
        "customer_name": customer_name,
        "customer_inn": first_xml_text(root, ["INN", "inn"]),
        "customer_kpp": first_xml_text(root, ["KPP", "kpp"]),
        "max_price": parse_decimal(price_text),
        "currency": first_xml_text(root, ["currencyCode", "currency"]),
        "placing_way": first_xml_text(root, ["placingWayName", "placingWay"]),
        "purchase_status": first_xml_text(root, ["state", "status", "orderState"]),
        "publish_date_text": first_xml_text(root, ["plannedPublishDate", "publishDate", "placingDate", "docPublishDate"]),
        "updated_date_text": first_xml_text(root, ["modificationDate", "updateDate"]),
        "submission_end_date_text": first_xml_text(root, ["endDate", "collectingEndDate", "submissionCloseDateTime", "summarizingDate"]),
        "contract_end_date_text": first_xml_text(root, ["contractEndDate", "executionEndDate"]),
    }

    return True, parsed, "ok"


def apply_purchase_data(purchase: Purchase, data: dict[str, Any]) -> None:
    if data.get("purchase_object"):
        purchase.purchase_object = data["purchase_object"]

    if data.get("customer_name"):
        purchase.customer_name = data["customer_name"]

    if data.get("customer_inn"):
        purchase.customer_inn = data["customer_inn"]

    if data.get("customer_kpp"):
        purchase.customer_kpp = data["customer_kpp"]

    if data.get("max_price") is not None:
        purchase.max_price = data["max_price"]

    if data.get("currency"):
        currency = str(data["currency"])
        if "RUB" in currency:
            purchase.currency = "RUB"
        else:
            purchase.currency = currency[:16]

    if data.get("placing_way"):
        purchase.placing_way = data["placing_way"]

    if data.get("purchase_status"):
        purchase.purchase_status = data["purchase_status"]

    if data.get("publish_date_text"):
        purchase.publish_date_text = data["publish_date_text"]

    if data.get("updated_date_text"):
        purchase.updated_date_text = data["updated_date_text"]

    if data.get("submission_end_date_text"):
        purchase.submission_end_date_text = data["submission_end_date_text"]

    if data.get("contract_end_date_text"):
        purchase.contract_end_date_text = data["contract_end_date_text"]


def extract_document_links(documents_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(documents_html, "lxml")

    result: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = absolute_url(a["href"])

        if not re.search(r"/filestore/.*/download/.*/file\.html\?uid=", href, flags=re.IGNORECASE):
            continue

        uid_match = re.search(r"uid=([A-Za-z0-9]+)", href)
        if not uid_match:
            continue

        uid = uid_match.group(1)

        if uid in seen:
            continue

        seen.add(uid)

        source_label = normalize_space(a.get_text(" ", strip=True))

        parent = a.find_parent(["tr", "div", "li"])
        if parent is not None:
            parent_text = normalize_space(parent.get_text(" ", strip=True))
            if len(parent_text) > len(source_label):
                source_label = parent_text[:500]

        result.append(
            {
                "uid": uid,
                "download_url": href,
                "source_label": source_label,
            }
        )

    return result


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


def detect_file_extension(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return ".pdf"

    if data.startswith(b"\xD0\xCF\x11\xE0"):
        return ".doc_or_xls"

    if data.startswith(b"PK\x03\x04"):
        return detect_office_zip_extension(data)

    return ".bin"


def safe_filename(name: str) -> str:
    name = normalize_space(name)

    if not name:
        return ""

    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "_")

    name = name.strip(" .")

    if len(name) > 180:
        base = os.path.splitext(name)[0]
        ext = os.path.splitext(name)[1]
        name = base[:150] + ext

    return name


def decode_content_disposition_filename(content_disposition: str) -> str:
    if not content_disposition:
        return ""

    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, flags=re.IGNORECASE)
    if match:
        return safe_filename(unquote(match.group(1)))

    match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if match:
        return safe_filename(unquote(match.group(1)))

    return ""


def normalize_downloaded_file_name(original_name: str, uid: str, extension: str, source_label: str) -> str:
    original_name = safe_filename(original_name)

    if not original_name:
        source_label = safe_filename(source_label)
        if source_label:
            original_name = source_label
        else:
            original_name = uid

    lower_name = original_name.lower()

    if lower_name.endswith(".zip") and extension in {".pdf", ".docx", ".xlsx", ".pptx"}:
        original_name = original_name[:-4]

    current_ext = os.path.splitext(original_name)[1].lower()

    if not current_ext:
        original_name = original_name + extension
    elif current_ext != extension and extension in {".pdf", ".docx", ".xlsx", ".pptx", ".zip"}:
        original_name = os.path.splitext(original_name)[0] + extension

    return safe_filename(original_name)


def upsert_purchase(db: Session, item: dict[str, str]) -> tuple[Purchase, bool]:
    purchase = db.scalar(select(Purchase).where(Purchase.reg_number == item["reg_number"]))
    is_new = False

    if purchase is None:
        is_new = True
        purchase = Purchase(
            law_type=item["law_type"],
            reg_number=item["reg_number"],
            source_url=item["source_url"],
            common_info_url=item["source_url"],
            documents_url=build_documents_url(item["source_url"]),
            print_form_url=build_print_form_url(item["reg_number"]) if item["law_type"] == "44-FZ" else None,
            xml_url=build_xml_url(item["reg_number"]) if item["law_type"] == "44-FZ" else None,
            parse_status="new",
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(purchase)
        db.flush()
    else:
        purchase.law_type = item["law_type"]
        purchase.source_url = item["source_url"]
        purchase.common_info_url = item["source_url"]
        purchase.documents_url = build_documents_url(item["source_url"])
        purchase.print_form_url = build_print_form_url(item["reg_number"]) if item["law_type"] == "44-FZ" else None
        purchase.xml_url = build_xml_url(item["reg_number"]) if item["law_type"] == "44-FZ" else None
        purchase.last_seen_at = utc_now()
        purchase.updated_at = utc_now()

    return purchase, is_new


def enqueue_parse_task(db: Session, purchase: Purchase, priority: int = 100) -> bool:
    existing = db.scalar(select(ParseTask).where(ParseTask.purchase_id == purchase.id))

    if existing is None:
        task = ParseTask(
            purchase_id=purchase.id,
            status="queued",
            priority=priority,
            attempts=0,
            max_attempts=ZAKUPKI_MAX_TASK_ATTEMPTS,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(task)
        purchase.parse_status = "queued"
        purchase.updated_at = utc_now()
        return True

    if existing.status in {"done", "processing"}:
        return False

    if existing.attempts >= existing.max_attempts:
        return False

    existing.status = "queued"
    existing.priority = min(existing.priority, priority)
    existing.updated_at = utc_now()
    purchase.parse_status = "queued"
    purchase.updated_at = utc_now()
    return True


def upsert_document(db: Session, purchase: Purchase, doc_data: dict[str, Any]) -> PurchaseDocument:
    document = db.scalar(
        select(PurchaseDocument).where(
            PurchaseDocument.reg_number == purchase.reg_number,
            PurchaseDocument.uid == doc_data["uid"],
        )
    )

    if document is None:
        document = PurchaseDocument(
            purchase_id=purchase.id,
            reg_number=purchase.reg_number,
            uid=doc_data["uid"],
            created_at=utc_now(),
        )
        db.add(document)

    document.purchase_id = purchase.id
    document.download_url = doc_data["download_url"]
    document.source_label = doc_data["source_label"]
    document.original_name = doc_data["original_name"]
    document.local_name = doc_data["local_name"]
    document.local_path = doc_data["local_path"]
    document.extension = doc_data["extension"]
    document.content_type = doc_data["content_type"]
    document.size_bytes = doc_data["size_bytes"]
    document.sha256 = doc_data["sha256"]
    document.download_status = "downloaded"
    document.download_error = None
    document.downloaded_at = utc_now()
    document.updated_at = utc_now()

    return document


def download_document(client: httpx.Client, purchase: Purchase, doc: dict[str, str]) -> dict[str, Any]:
    uid = doc["uid"]
    download_url = doc["download_url"]
    source_label = doc.get("source_label", "")

    response = client.get(download_url, headers=HEADERS_FILE, timeout=180)
    response.raise_for_status()

    content = response.content

    if response.headers.get("content-type", "").lower().startswith("text/html") and content.strip().startswith(b"<"):
        raise RuntimeError("filestore returned html instead of binary file")

    extension = detect_file_extension(content)
    sha256 = hashlib.sha256(content).hexdigest()

    content_disposition = response.headers.get("content-disposition", "")
    original_name = decode_content_disposition_filename(content_disposition)
    original_name = normalize_downloaded_file_name(original_name, uid, extension, source_label)

    reg_dir = FILE_STORAGE_DIR / purchase.reg_number
    reg_dir.mkdir(parents=True, exist_ok=True)

    local_name = safe_filename(f"{uid}_{original_name}")
    local_path = reg_dir / local_name

    local_path.write_bytes(content)

    return {
        "uid": uid,
        "download_url": download_url,
        "source_label": source_label,
        "original_name": original_name,
        "local_name": local_name,
        "local_path": str(local_path),
        "extension": os.path.splitext(local_name)[1].lower(),
        "content_type": response.headers.get("content-type", "application/octet-stream"),
        "size_bytes": len(content),
        "sha256": sha256,
    }


def parse_purchase_card(db: Session, client: httpx.Client, purchase: Purchase) -> dict[str, int]:
    parsed_count = 0
    document_count = 0

    purchase.parse_status = "parsing"
    purchase.parse_attempts += 1
    purchase.parse_error = None
    purchase.updated_at = utc_now()
    db.commit()

    if not purchase.common_info_url:
        purchase.common_info_url = purchase.source_url

    if not purchase.documents_url:
        purchase.documents_url = build_documents_url(purchase.source_url)

    if purchase.law_type == "44-FZ":
        purchase.print_form_url = build_print_form_url(purchase.reg_number)
        purchase.xml_url = build_xml_url(purchase.reg_number)

    common_response = client.get(purchase.common_info_url, timeout=120)
    common_response.raise_for_status()
    common_html = common_response.text

    if STORE_RAW_COMMON_HTML:
        purchase.raw_common_html = common_html

    common_data = parse_html_purchase(common_html)
    apply_purchase_data(purchase, common_data)
    purchase.parse_source = "common_html"
    parsed_count += 1

    if purchase.print_form_url:
        try:
            print_response = client.get(purchase.print_form_url, timeout=180)
            if print_response.status_code == 200:
                print_html = print_response.text

                if STORE_RAW_PRINT_HTML:
                    purchase.raw_print_html = print_html

                print_data = parse_html_purchase(print_html)
                apply_purchase_data(purchase, print_data)
                purchase.parse_source = "print_html"
        except Exception as exc:
            purchase.parse_error = f"print_form_error: {type(exc).__name__}: {exc}"

    if purchase.law_type == "44-FZ" and purchase.xml_url:
        try:
            xml_response = client.get(purchase.xml_url, timeout=120)

            if xml_response.status_code == 404:
                purchase.xml_status = "http_404"
                purchase.xml_error = "viewXml returned 404"
            else:
                xml_response.raise_for_status()
                xml_text = xml_response.text

                is_valid_xml, xml_data, xml_reason = try_parse_44fz_xml(purchase.reg_number, xml_text)

                if is_valid_xml:
                    if STORE_RAW_XML:
                        purchase.raw_xml = xml_text

                    apply_purchase_data(purchase, xml_data)
                    purchase.xml_status = "ok"
                    purchase.xml_error = None
                    purchase.parse_source = "mixed"
                else:
                    purchase.xml_status = "invalid_xml"
                    purchase.xml_error = xml_reason

        except Exception as exc:
            purchase.xml_status = "request_error"
            purchase.xml_error = f"{type(exc).__name__}: {exc}"

    if purchase.documents_url:
        documents_response = client.get(purchase.documents_url, timeout=120)
        documents_response.raise_for_status()

        docs = extract_document_links(documents_response.text)
        purchase.documents_count = len(docs)

        for doc in docs:
            try:
                doc_data = download_document(client, purchase, doc)
                upsert_document(db, purchase, doc_data)
                document_count += 1
                time.sleep(ZAKUPKI_REQUEST_DELAY_SECONDS)
            except Exception:
                continue

    purchase.files_count = document_count
    purchase.parse_status = "parsed"
    purchase.parsed_at = utc_now()
    purchase.updated_at = utc_now()

    db.commit()

    return {
        "parsed_count": parsed_count,
        "document_count": document_count,
    }


def discover_new_purchases(max_pages: int = ZAKUPKI_DISCOVERY_MAX_PAGES) -> dict[str, Any]:
    db = SessionLocal()

    run = SyncRun(
        kind="discovery",
        status="running",
        started_at=utc_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    pages_scanned = 0
    found_count = 0
    new_count = 0
    queued_count = 0

    try:
        with httpx.Client(headers=HEADERS_HTML, follow_redirects=True, timeout=120) as client:
            client.get(f"{BASE_URL}/epz/main/public/home.html", timeout=120)

            for page in range(1, max_pages + 1):
                url = build_discovery_url(page)
                response = client.get(url, timeout=120)
                response.raise_for_status()

                links = extract_purchase_links(response.text)

                pages_scanned += 1
                found_count += len(links)

                if not links:
                    break

                for item in links:
                    purchase, is_new = upsert_purchase(db, item)

                    if is_new:
                        new_count += 1
                        if enqueue_parse_task(db, purchase, priority=100):
                            queued_count += 1
                    else:
                        if purchase.parse_status in {"new", "error"} and purchase.parse_attempts < ZAKUPKI_MAX_TASK_ATTEMPTS:
                            if enqueue_parse_task(db, purchase, priority=200):
                                queued_count += 1

                    db.commit()

                time.sleep(ZAKUPKI_REQUEST_DELAY_SECONDS)

        run.status = "success"
        run.pages_scanned = pages_scanned
        run.found_count = found_count
        run.new_count = new_count
        run.queued_count = queued_count
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "success",
            "run_id": run.id,
            "pages_scanned": pages_scanned,
            "found_count": found_count,
            "new_count": new_count,
            "queued_count": queued_count,
        }

    except Exception as exc:
        run.status = "error"
        run.pages_scanned = pages_scanned
        run.found_count = found_count
        run.new_count = new_count
        run.queued_count = queued_count
        run.error_text = f"{type(exc).__name__}: {exc}"
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "error",
            "run_id": run.id,
            "error": run.error_text,
            "pages_scanned": pages_scanned,
            "found_count": found_count,
            "new_count": new_count,
            "queued_count": queued_count,
        }

    finally:
        db.close()


def process_parse_queue(limit: int = ZAKUPKI_PARSE_BATCH_SIZE) -> dict[str, Any]:
    db = SessionLocal()

    run = SyncRun(
        kind="parse_queue",
        status="running",
        started_at=utc_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    parsed_count = 0
    document_count = 0
    error_count = 0

    try:
        tasks = db.scalars(
            select(ParseTask)
            .where(ParseTask.status.in_(["queued", "error"]))
            .where(ParseTask.attempts < ParseTask.max_attempts)
            .order_by(ParseTask.priority.asc(), ParseTask.id.asc())
            .limit(limit)
        ).all()

        if not tasks:
            run.status = "success"
            run.parsed_count = 0
            run.document_count = 0
            run.finished_at = utc_now()
            db.commit()

            return {
                "status": "success",
                "run_id": run.id,
                "tasks": 0,
                "parsed_count": 0,
                "document_count": 0,
                "error_count": 0,
            }

        with httpx.Client(headers=HEADERS_HTML, follow_redirects=True, timeout=180) as client:
            client.get(f"{BASE_URL}/epz/main/public/home.html", timeout=120)

            for task in tasks:
                purchase = task.purchase

                task.status = "processing"
                task.locked_at = utc_now()
                task.attempts += 1
                task.updated_at = utc_now()

                purchase.parse_status = "parsing"
                purchase.updated_at = utc_now()

                db.commit()

                try:
                    result = parse_purchase_card(db, client, purchase)

                    parsed_count += result["parsed_count"]
                    document_count += result["document_count"]

                    task.status = "done"
                    task.finished_at = utc_now()
                    task.last_error = None
                    task.updated_at = utc_now()

                    purchase.parse_status = "parsed"
                    purchase.parse_error = None
                    purchase.updated_at = utc_now()

                    db.commit()

                except Exception as exc:
                    error_count += 1

                    error_text = f"{type(exc).__name__}: {exc}"

                    task.last_error = error_text
                    task.updated_at = utc_now()

                    if task.attempts >= task.max_attempts:
                        task.status = "failed"
                    else:
                        task.status = "error"

                    purchase.parse_status = "error"
                    purchase.parse_error = error_text
                    purchase.updated_at = utc_now()

                    db.commit()

                time.sleep(ZAKUPKI_REQUEST_DELAY_SECONDS)

        run.status = "success"
        run.parsed_count = parsed_count
        run.document_count = document_count
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "success",
            "run_id": run.id,
            "tasks": len(tasks),
            "parsed_count": parsed_count,
            "document_count": document_count,
            "error_count": error_count,
        }

    except Exception as exc:
        run.status = "error"
        run.parsed_count = parsed_count
        run.document_count = document_count
        run.error_text = f"{type(exc).__name__}: {exc}"
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "error",
            "run_id": run.id,
            "error": run.error_text,
            "parsed_count": parsed_count,
            "document_count": document_count,
            "error_count": error_count,
        }

    finally:
        db.close()


def run_discovery_then_parse(max_pages: int, parse_limit: int) -> dict[str, Any]:
    discovery_result = discover_new_purchases(max_pages=max_pages)
    parse_result = process_parse_queue(limit=parse_limit)

    return {
        "discovery": discovery_result,
        "parse": parse_result,
    }
