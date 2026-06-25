import os
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs import (
    ZAKUPKI_DISCOVERY_MAX_PAGES,
    ZAKUPKI_PARSE_BATCH_SIZE,
    discover_new_purchases,
    process_parse_queue,
    run_discovery_then_parse,
)
from app.models import (
    ParseTask,
    Purchase,
    PurchaseDocument,
    SyncRun,
    get_db,
    init_db,
    utc_now,
    DocumentMarkdown,
    MarkdownTask,
)

from app.to_markdown import (
    MARKDOWN_BATCH_SIZE,
    MARKDOWN_ENQUEUE_LIMIT,
    enqueue_missing_markdown_tasks,
    enqueue_then_process_markdown,
    process_markdown_queue,
)

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "zakupki-internal-secret-change-in-prod")

DISCOVERY_ENABLED = os.getenv("ZAKUPKI_DISCOVERY_ENABLED", "true").lower() == "true"
DISCOVERY_INTERVAL_MINUTES = int(os.getenv("ZAKUPKI_DISCOVERY_INTERVAL_MINUTES", "60"))

PARSE_QUEUE_ENABLED = os.getenv("ZAKUPKI_PARSE_QUEUE_ENABLED", "true").lower() == "true"
PARSE_QUEUE_INTERVAL_MINUTES = int(os.getenv("ZAKUPKI_PARSE_QUEUE_INTERVAL_MINUTES", "10"))

MARKDOWN_QUEUE_ENABLED = os.getenv("MARKDOWN_QUEUE_ENABLED", "true").lower() == "true"
MARKDOWN_QUEUE_INTERVAL_MINUTES = int(os.getenv("MARKDOWN_QUEUE_INTERVAL_MINUTES", "15"))

NIGHTLY_FULL_JOB_ENABLED = os.getenv("ZAKUPKI_NIGHTLY_FULL_JOB_ENABLED", "true").lower() == "true"
NIGHTLY_FULL_JOB_HOUR = int(os.getenv("ZAKUPKI_NIGHTLY_FULL_JOB_HOUR", "2"))
NIGHTLY_FULL_JOB_MINUTE = int(os.getenv("ZAKUPKI_NIGHTLY_FULL_JOB_MINUTE", "15"))

app = FastAPI(
    title="Internal Zakupki Cache API",
    version="0.2.0",
)


def require_token(x_internal_token: str | None = Header(default=None)) -> None:
    if x_internal_token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid internal token")


def purchase_to_dict(purchase: Purchase, include_documents: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": purchase.id,
        "law_type": purchase.law_type,
        "reg_number": purchase.reg_number,
        "source_url": purchase.source_url,
        "common_info_url": purchase.common_info_url,
        "print_form_url": purchase.print_form_url,
        "xml_url": purchase.xml_url,
        "documents_url": purchase.documents_url,
        "purchase_object": purchase.purchase_object,
        "customer_name": purchase.customer_name,
        "customer_inn": purchase.customer_inn,
        "customer_kpp": purchase.customer_kpp,
        "max_price": float(purchase.max_price) if purchase.max_price is not None else None,
        "currency": purchase.currency,
        "placing_way": purchase.placing_way,
        "purchase_status": purchase.purchase_status,
        "publish_date_text": purchase.publish_date_text,
        "updated_date_text": purchase.updated_date_text,
        "submission_end_date_text": purchase.submission_end_date_text,
        "contract_end_date_text": purchase.contract_end_date_text,
        "region": purchase.region,
        "okpd2": purchase.okpd2,
        "parse_status": purchase.parse_status,
        "parse_source": purchase.parse_source,
        "parse_attempts": purchase.parse_attempts,
        "parse_error": purchase.parse_error,
        "xml_status": purchase.xml_status,
        "xml_error": purchase.xml_error,
        "documents_count": purchase.documents_count,
        "files_count": purchase.files_count,
        "first_seen_at": purchase.first_seen_at.isoformat() if purchase.first_seen_at else None,
        "last_seen_at": purchase.last_seen_at.isoformat() if purchase.last_seen_at else None,
        "parsed_at": purchase.parsed_at.isoformat() if purchase.parsed_at else None,
        "created_at": purchase.created_at.isoformat() if purchase.created_at else None,
        "updated_at": purchase.updated_at.isoformat() if purchase.updated_at else None,
    }

    if include_documents:
        result["documents"] = [
            {
                "id": doc.id,
                "uid": doc.uid,
                "download_url": doc.download_url,
                "source_label": doc.source_label,
                "original_name": doc.original_name,
                "local_name": doc.local_name,
                "extension": doc.extension,
                "content_type": doc.content_type,
                "size_bytes": doc.size_bytes,
                "sha256": doc.sha256,
                "download_status": doc.download_status,
                "download_error": doc.download_error,
                "downloaded_at": doc.downloaded_at.isoformat() if doc.downloaded_at else None,
                "internal_download_url": f"/api/v1/documents/{doc.id}/download",
                "markdown": {
                    "exists": doc.markdown is not None and doc.markdown.status == "converted",
                    "status": doc.markdown.status if doc.markdown else None,
                    "markdown_size_bytes": doc.markdown.markdown_size_bytes if doc.markdown else None,
                    "markdown_sha256": doc.markdown.markdown_sha256 if doc.markdown else None,
                    "internal_markdown_url": f"/api/v1/documents/{doc.id}/markdown" if doc.markdown and doc.markdown.status == "converted" else None,
                    "error_text": doc.markdown.error_text if doc.markdown else None,
                },
            }
            for doc in purchase.documents
        ]

    return result


def task_to_dict(task: ParseTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "purchase_id": task.purchase_id,
        "reg_number": task.purchase.reg_number if task.purchase else None,
        "status": task.status,
        "priority": task.priority,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "locked_at": task.locked_at.isoformat() if task.locked_at else None,
        "last_error": task.last_error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def sync_run_to_dict(run: SyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "pages_scanned": run.pages_scanned,
        "found_count": run.found_count,
        "new_count": run.new_count,
        "queued_count": run.queued_count,
        "parsed_count": run.parsed_count,
        "document_count": run.document_count,
        "error_text": run.error_text,
    }


@app.on_event("startup")
def startup() -> None:
    init_db()

    scheduler = BackgroundScheduler(timezone="Europe/Moscow")

    if DISCOVERY_ENABLED:
        scheduler.add_job(
            lambda: discover_new_purchases(max_pages=ZAKUPKI_DISCOVERY_MAX_PAGES),
            trigger="interval",
            minutes=DISCOVERY_INTERVAL_MINUTES,
            id="discovery_interval",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    if MARKDOWN_QUEUE_ENABLED:
        scheduler.add_job(
            lambda: enqueue_then_process_markdown(
                enqueue_limit=MARKDOWN_ENQUEUE_LIMIT,
                process_limit=MARKDOWN_BATCH_SIZE,
            ),
            trigger="interval",
            minutes=MARKDOWN_QUEUE_INTERVAL_MINUTES,
            id="markdown_queue_interval",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    


    
    if PARSE_QUEUE_ENABLED:
        scheduler.add_job(
            lambda: process_parse_queue(limit=ZAKUPKI_PARSE_BATCH_SIZE),
            trigger="interval",
            minutes=PARSE_QUEUE_INTERVAL_MINUTES,
            id="parse_queue_interval",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    if NIGHTLY_FULL_JOB_ENABLED:
        scheduler.add_job(
            lambda: run_discovery_then_parse(
                max_pages=ZAKUPKI_DISCOVERY_MAX_PAGES,
                parse_limit=ZAKUPKI_PARSE_BATCH_SIZE,
            ),
            trigger="cron",
            hour=NIGHTLY_FULL_JOB_HOUR,
            minute=NIGHTLY_FULL_JOB_MINUTE,
            id="nightly_full_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    scheduler.start()
    app.state.scheduler = scheduler


@app.on_event("shutdown")
def shutdown() -> None:
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    purchases_count = db.scalar(select(func.count(Purchase.id))) or 0
    queued_count = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.status == "queued")) or 0
    error_count = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.status.in_(["error", "failed"]))) or 0

    return {
        "status": "ok",
        "service": "zakupki-api",
        "time": utc_now().isoformat(),
        "purchases_count": purchases_count,
        "queued_count": queued_count,
        "error_count": error_count,
    }


@app.post("/api/v1/discovery/run", dependencies=[Depends(require_token)])
def run_discovery(
    max_pages: int = Query(default=ZAKUPKI_DISCOVERY_MAX_PAGES, ge=1, le=500),
) -> dict[str, Any]:
    return discover_new_purchases(max_pages=max_pages)


@app.post("/api/v1/queue/process", dependencies=[Depends(require_token)])
def run_queue_processing(
    limit: int = Query(default=ZAKUPKI_PARSE_BATCH_SIZE, ge=1, le=500),
) -> dict[str, Any]:
    return process_parse_queue(limit=limit)


@app.post("/api/v1/jobs/run-once", dependencies=[Depends(require_token)])
def run_once(
    max_pages: int = Query(default=ZAKUPKI_DISCOVERY_MAX_PAGES, ge=1, le=500),
    parse_limit: int = Query(default=ZAKUPKI_PARSE_BATCH_SIZE, ge=1, le=500),
) -> dict[str, Any]:
    return run_discovery_then_parse(max_pages=max_pages, parse_limit=parse_limit)


@app.get("/api/v1/sync-runs", dependencies=[Depends(require_token)])
def list_sync_runs(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(SyncRun)
        .order_by(SyncRun.id.desc())
        .limit(limit)
    ).all()

    return {
        "items": [sync_run_to_dict(run) for run in runs],
        "limit": limit,
    }


@app.get("/api/v1/queue", dependencies=[Depends(require_token)])
def list_queue(
    status: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(ParseTask).order_by(ParseTask.priority.asc(), ParseTask.id.asc())

    if status:
        stmt = stmt.where(ParseTask.status == status)

    tasks = db.scalars(stmt.offset(offset).limit(limit)).all()

    return {
        "items": [task_to_dict(task) for task in tasks],
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/v1/purchases/{reg_number}/requeue", dependencies=[Depends(require_token)])
def requeue_purchase(
    reg_number: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    purchase = db.scalar(select(Purchase).where(Purchase.reg_number == reg_number))

    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")

    task = db.scalar(select(ParseTask).where(ParseTask.purchase_id == purchase.id))

    if task is None:
        task = ParseTask(
            purchase_id=purchase.id,
            status="queued",
            priority=50,
            attempts=0,
            max_attempts=5,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(task)
    else:
        task.status = "queued"
        task.priority = 50
        task.last_error = None
        task.updated_at = utc_now()
        task.finished_at = None

    purchase.parse_status = "queued"
    purchase.parse_error = None
    purchase.updated_at = utc_now()

    db.commit()

    return {
        "status": "queued",
        "reg_number": reg_number,
        "task_id": task.id,
    }


@app.get("/api/v1/purchases", dependencies=[Depends(require_token)])
def list_purchases(
    q: str = Query(default=""),
    law_type: str = Query(default=""),
    parse_status: str = Query(default=""),
    xml_status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Purchase).order_by(Purchase.last_seen_at.desc())

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Purchase.reg_number.ilike(like))
            | (Purchase.purchase_object.ilike(like))
            | (Purchase.customer_name.ilike(like))
        )

    if law_type:
        stmt = stmt.where(Purchase.law_type == law_type)

    if parse_status:
        stmt = stmt.where(Purchase.parse_status == parse_status)

    if xml_status:
        stmt = stmt.where(Purchase.xml_status == xml_status)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(total_stmt) or 0

    purchases = db.scalars(stmt.offset(offset).limit(limit)).all()

    return {
        "items": [purchase_to_dict(purchase, include_documents=False) for purchase in purchases],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/purchases/{reg_number}", dependencies=[Depends(require_token)])
def get_purchase(
    reg_number: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    purchase = db.scalar(select(Purchase).where(Purchase.reg_number == reg_number))

    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")

    return purchase_to_dict(purchase, include_documents=True)


@app.get("/api/v1/documents/{document_id}/download", dependencies=[Depends(require_token)])
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    document = db.scalar(select(PurchaseDocument).where(PurchaseDocument.id == document_id))

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    path = Path(document.local_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Document file not found on disk")

    return FileResponse(
        path=path,
        filename=document.original_name or document.local_name,
        media_type=document.content_type or "application/octet-stream",
    )


@app.get("/api/v1/stats", dependencies=[Depends(require_token)])
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    total_purchases = db.scalar(select(func.count(Purchase.id))) or 0
    total_documents = db.scalar(select(func.count(PurchaseDocument.id))) or 0

    queued = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.status == "queued")) or 0
    processing = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.status == "processing")) or 0
    done = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.status == "done")) or 0
    errors = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.status.in_(["error", "failed"]))) or 0

    law_44 = db.scalar(select(func.count(Purchase.id)).where(Purchase.law_type == "44-FZ")) or 0
    law_223 = db.scalar(select(func.count(Purchase.id)).where(Purchase.law_type == "223-FZ")) or 0

    parsed = db.scalar(select(func.count(Purchase.id)).where(Purchase.parse_status == "parsed")) or 0
    parse_error = db.scalar(select(func.count(Purchase.id)).where(Purchase.parse_status == "error")) or 0

    xml_ok = db.scalar(select(func.count(Purchase.id)).where(Purchase.xml_status == "ok")) or 0
    xml_404 = db.scalar(select(func.count(Purchase.id)).where(Purchase.xml_status == "http_404")) or 0

    return {
        "purchases": {
            "total": total_purchases,
            "law_44": law_44,
            "law_223": law_223,
            "parsed": parsed,
            "error": parse_error,
        },
        "documents": {
            "total": total_documents,
        },
        "queue": {
            "queued": queued,
            "processing": processing,
            "done": done,
            "error_or_failed": errors,
        },
        "xml": {
            "ok": xml_ok,
            "http_404": xml_404,
        },
    }


@app.post("/api/v1/markdown/enqueue", dependencies=[Depends(require_token)])
def enqueue_markdown(
    limit: int = Query(default=MARKDOWN_ENQUEUE_LIMIT, ge=1, le=10000),
) -> dict[str, Any]:
    return enqueue_missing_markdown_tasks(limit=limit)


@app.post("/api/v1/markdown/process", dependencies=[Depends(require_token)])
def run_markdown_processing(
    limit: int = Query(default=MARKDOWN_BATCH_SIZE, ge=1, le=500),
) -> dict[str, Any]:
    return process_markdown_queue(limit=limit)


@app.post("/api/v1/markdown/run-once", dependencies=[Depends(require_token)])
def run_markdown_once(
    enqueue_limit: int = Query(default=MARKDOWN_ENQUEUE_LIMIT, ge=1, le=10000),
    process_limit: int = Query(default=MARKDOWN_BATCH_SIZE, ge=1, le=500),
) -> dict[str, Any]:
    return enqueue_then_process_markdown(
        enqueue_limit=enqueue_limit,
        process_limit=process_limit,
    )


@app.get("/api/v1/markdown/tasks", dependencies=[Depends(require_token)])
def list_markdown_tasks(
    status: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(MarkdownTask).order_by(MarkdownTask.priority.asc(), MarkdownTask.id.asc())

    if status:
        stmt = stmt.where(MarkdownTask.status == status)

    tasks = db.scalars(stmt.offset(offset).limit(limit)).all()

    return {
        "items": [
            {
                "id": task.id,
                "document_id": task.document_id,
                "status": task.status,
                "priority": task.priority,
                "attempts": task.attempts,
                "max_attempts": task.max_attempts,
                "locked_at": task.locked_at.isoformat() if task.locked_at else None,
                "last_error": task.last_error,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            }
            for task in tasks
        ],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/documents/{document_id}/markdown", dependencies=[Depends(require_token)])
def get_document_markdown(
    document_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    markdown = db.scalar(
        select(DocumentMarkdown).where(DocumentMarkdown.document_id == document_id)
    )

    if markdown is None:
        raise HTTPException(status_code=404, detail="Markdown not found")

    if markdown.status != "converted":
        raise HTTPException(status_code=409, detail=f"Markdown is not converted: {markdown.status}")

    path = Path(markdown.markdown_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Markdown file not found on disk")

    return FileResponse(
        path=path,
        filename=markdown.markdown_name,
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/api/v1/documents/{document_id}/markdown/text", dependencies=[Depends(require_token)])
def get_document_markdown_text(
    document_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    markdown = db.scalar(
        select(DocumentMarkdown).where(DocumentMarkdown.document_id == document_id)
    )

    if markdown is None:
        raise HTTPException(status_code=404, detail="Markdown not found")

    if markdown.status != "converted":
        raise HTTPException(status_code=409, detail=f"Markdown is not converted: {markdown.status}")

    path = Path(markdown.markdown_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Markdown file not found on disk")

    text = path.read_text(encoding="utf-8")

    return {
        "document_id": document_id,
        "reg_number": markdown.reg_number,
        "uid": markdown.uid,
        "markdown_size_bytes": markdown.markdown_size_bytes,
        "markdown_sha256": markdown.markdown_sha256,
        "text": text,
    }
