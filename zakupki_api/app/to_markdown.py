import hashlib
import importlib.metadata
import os
import re
import time
from pathlib import Path
from typing import Any

from markitdown import MarkItDown
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DocumentMarkdown,
    MarkdownTask,
    PurchaseDocument,
    SessionLocal,
    SyncRun,
    utc_now,
)


FILE_STORAGE_DIR = Path(os.getenv("FILE_STORAGE_DIR", "/data/files")).resolve()
MARKDOWN_STORAGE_DIR = Path(os.getenv("MARKDOWN_STORAGE_DIR", "/data/markdown")).resolve()

MARKDOWN_BATCH_SIZE = int(os.getenv("MARKDOWN_BATCH_SIZE", "10"))
MARKDOWN_ENQUEUE_LIMIT = int(os.getenv("MARKDOWN_ENQUEUE_LIMIT", "1000"))
MARKDOWN_MAX_TASK_ATTEMPTS = int(os.getenv("MARKDOWN_MAX_TASK_ATTEMPTS", "3"))
MARKDOWN_REQUEST_DELAY_SECONDS = float(os.getenv("MARKDOWN_REQUEST_DELAY_SECONDS", "0.3"))
MARKDOWN_MAX_FILE_SIZE_MB = int(os.getenv("MARKDOWN_MAX_FILE_SIZE_MB", "200"))
MARKDOWN_ENABLE_PLUGINS = os.getenv("MARKDOWN_ENABLE_PLUGINS", "false").lower() == "true"


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
    ".json",
    ".xml",
    ".txt",
    ".html",
    ".htm",
    ".zip",
}


def normalize_space(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(name: str) -> str:
    name = normalize_space(name)

    if not name:
        return "document.md"

    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "_")

    name = name.strip(" .")

    if len(name) > 180:
        base = os.path.splitext(name)[0]
        ext = os.path.splitext(name)[1]
        name = base[:150] + ext

    return name


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def get_markitdown_version() -> str:
    try:
        return importlib.metadata.version("markitdown")
    except importlib.metadata.PackageNotFoundError:
        return ""


def ensure_local_source_path(path_text: str) -> Path:
    path = Path(path_text).resolve()

    if not path.exists():
        raise FileNotFoundError(f"source file does not exist: {path}")

    if not path.is_file():
        raise RuntimeError(f"source path is not a file: {path}")

    try:
        path.relative_to(FILE_STORAGE_DIR)
    except ValueError as exc:
        raise RuntimeError(f"source file is outside FILE_STORAGE_DIR: {path}") from exc

    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb > MARKDOWN_MAX_FILE_SIZE_MB:
        raise RuntimeError(f"source file is too large: {size_mb:.2f} MB")

    return path


def should_try_convert(document: PurchaseDocument) -> bool:
    extension = (document.extension or "").lower()

    if extension in SUPPORTED_EXTENSIONS:
        return True

    local_path = Path(document.local_path)
    suffix = local_path.suffix.lower()

    return suffix in SUPPORTED_EXTENSIONS


def extract_markdown_text(result: Any) -> str:
    text = getattr(result, "text_content", None)

    if text:
        return str(text)

    markdown = getattr(result, "markdown", None)

    if markdown:
        return str(markdown)

    return str(result)


def build_markdown_front_matter(document: PurchaseDocument, converter_version: str) -> str:
    original_name = document.original_name or document.local_name or ""
    source_label = document.source_label or ""

    lines = [
        "---",
        f"reg_number: {document.reg_number}",
        f"document_id: {document.id}",
        f"uid: {document.uid}",
        f"original_name: {original_name}",
        f"extension: {document.extension}",
        f"content_type: {document.content_type or ''}",
        f"size_bytes: {document.size_bytes}",
        f"source_sha256: {document.sha256}",
        f"converter: markitdown",
        f"converter_version: {converter_version}",
        f"generated_at: {utc_now().isoformat()}",
        "---",
        "",
    ]

    if source_label:
        lines.extend(
            [
                "# Source label",
                "",
                source_label,
                "",
                "# Converted content",
                "",
            ]
        )

    return "\n".join(lines)


def convert_document_to_markdown(db: Session, document: PurchaseDocument) -> DocumentMarkdown:
    if not should_try_convert(document):
        raise RuntimeError(f"unsupported file extension for markdown conversion: {document.extension}")

    source_path = ensure_local_source_path(document.local_path)
    source_sha256 = sha256_file(source_path)

    existing = db.scalar(
        select(DocumentMarkdown).where(DocumentMarkdown.document_id == document.id)
    )

    if existing is not None and existing.source_sha256 == source_sha256 and Path(existing.markdown_path).exists():
        existing.status = "converted"
        existing.error_text = None
        existing.updated_at = utc_now()
        db.commit()
        return existing

    MARKDOWN_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    reg_dir = MARKDOWN_STORAGE_DIR / document.reg_number
    reg_dir.mkdir(parents=True, exist_ok=True)

    converter_version = get_markitdown_version()
    converter = MarkItDown(enable_plugins=MARKDOWN_ENABLE_PLUGINS)

    result = converter.convert(str(source_path))
    converted_text = extract_markdown_text(result)

    if not converted_text.strip():
        raise RuntimeError("markitdown returned empty markdown")

    front_matter = build_markdown_front_matter(document, converter_version)
    markdown_text = front_matter + converted_text.strip() + "\n"

    markdown_bytes = markdown_text.encode("utf-8")
    markdown_sha256 = sha256_bytes(markdown_bytes)

    markdown_name = safe_filename(f"{document.uid}.md")
    markdown_path = reg_dir / markdown_name
    markdown_path.write_bytes(markdown_bytes)

    if existing is None:
        existing = DocumentMarkdown(
            document_id=document.id,
            purchase_id=document.purchase_id,
            reg_number=document.reg_number,
            uid=document.uid,
            created_at=utc_now(),
        )
        db.add(existing)

    existing.purchase_id = document.purchase_id
    existing.reg_number = document.reg_number
    existing.uid = document.uid
    existing.source_local_path = str(source_path)
    existing.source_sha256 = source_sha256
    existing.markdown_name = markdown_name
    existing.markdown_path = str(markdown_path)
    existing.markdown_sha256 = markdown_sha256
    existing.markdown_size_bytes = len(markdown_bytes)
    existing.converter = "markitdown"
    existing.converter_version = converter_version
    existing.status = "converted"
    existing.error_text = None
    existing.generated_at = utc_now()
    existing.updated_at = utc_now()

    db.commit()
    db.refresh(existing)

    return existing


def enqueue_markdown_task(db: Session, document: PurchaseDocument, priority: int = 100) -> bool:
    existing_markdown = db.scalar(
        select(DocumentMarkdown).where(DocumentMarkdown.document_id == document.id)
    )

    if existing_markdown is not None:
        markdown_path = Path(existing_markdown.markdown_path)
        if existing_markdown.source_sha256 == document.sha256 and markdown_path.exists():
            return False

    task = db.scalar(
        select(MarkdownTask).where(MarkdownTask.document_id == document.id)
    )

    if task is None:
        task = MarkdownTask(
            document_id=document.id,
            status="queued",
            priority=priority,
            attempts=0,
            max_attempts=MARKDOWN_MAX_TASK_ATTEMPTS,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(task)
        return True

    if task.status == "processing":
        return False

    if task.status == "done" and existing_markdown is not None:
        markdown_path = Path(existing_markdown.markdown_path)
        if existing_markdown.source_sha256 == document.sha256 and markdown_path.exists():
            return False

    if task.attempts >= task.max_attempts and task.status == "failed":
        return False

    task.status = "queued"
    task.priority = min(task.priority, priority)
    task.updated_at = utc_now()

    return True


def enqueue_missing_markdown_tasks(limit: int = MARKDOWN_ENQUEUE_LIMIT) -> dict[str, Any]:
    db = SessionLocal()
    queued_count = 0
    skipped_count = 0

    run = SyncRun(
        kind="markdown_enqueue",
        status="running",
        started_at=utc_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        documents = db.scalars(
            select(PurchaseDocument)
            .where(PurchaseDocument.download_status == "downloaded")
            .order_by(PurchaseDocument.id.asc())
            .limit(limit)
        ).all()

        for document in documents:
            try:
                if not should_try_convert(document):
                    skipped_count += 1
                    continue

                if enqueue_markdown_task(db, document, priority=100):
                    queued_count += 1
                else:
                    skipped_count += 1

                db.commit()
            except Exception:
                db.rollback()
                skipped_count += 1

        run.status = "success"
        run.found_count = len(documents)
        run.queued_count = queued_count
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "success",
            "run_id": run.id,
            "documents_seen": len(documents),
            "queued_count": queued_count,
            "skipped_count": skipped_count,
        }

    except Exception as exc:
        run.status = "error"
        run.error_text = f"{type(exc).__name__}: {exc}"
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "error",
            "run_id": run.id,
            "error": run.error_text,
            "queued_count": queued_count,
            "skipped_count": skipped_count,
        }

    finally:
        db.close()


def process_markdown_queue(limit: int = MARKDOWN_BATCH_SIZE) -> dict[str, Any]:
    db = SessionLocal()

    run = SyncRun(
        kind="markdown_queue",
        status="running",
        started_at=utc_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    converted_count = 0
    error_count = 0

    try:
        tasks = db.scalars(
            select(MarkdownTask)
            .where(MarkdownTask.status.in_(["queued", "error"]))
            .where(MarkdownTask.attempts < MarkdownTask.max_attempts)
            .order_by(MarkdownTask.priority.asc(), MarkdownTask.id.asc())
            .limit(limit)
        ).all()

        for task in tasks:
            document = db.scalar(
                select(PurchaseDocument).where(PurchaseDocument.id == task.document_id)
            )

            if document is None:
                task.status = "failed"
                task.last_error = "document not found"
                task.finished_at = utc_now()
                task.updated_at = utc_now()
                error_count += 1
                db.commit()
                continue

            task.status = "processing"
            task.locked_at = utc_now()
            task.attempts += 1
            task.updated_at = utc_now()
            db.commit()

            try:
                convert_document_to_markdown(db, document)

                task.status = "done"
                task.last_error = None
                task.finished_at = utc_now()
                task.updated_at = utc_now()

                converted_count += 1
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

                existing = db.scalar(
                    select(DocumentMarkdown).where(DocumentMarkdown.document_id == document.id)
                )

                if existing is None:
                    existing = DocumentMarkdown(
                        document_id=document.id,
                        purchase_id=document.purchase_id,
                        reg_number=document.reg_number,
                        uid=document.uid,
                        source_local_path=document.local_path,
                        source_sha256=document.sha256,
                        markdown_name="",
                        markdown_path="",
                        markdown_sha256="",
                        markdown_size_bytes=0,
                        status="error",
                        error_text=error_text,
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                    db.add(existing)
                else:
                    existing.status = "error"
                    existing.error_text = error_text
                    existing.updated_at = utc_now()

                db.commit()

            time.sleep(MARKDOWN_REQUEST_DELAY_SECONDS)

        run.status = "success"
        run.parsed_count = converted_count
        run.document_count = converted_count
        run.error_text = None if error_count == 0 else f"errors={error_count}"
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "success",
            "run_id": run.id,
            "tasks": len(tasks),
            "converted_count": converted_count,
            "error_count": error_count,
        }

    except Exception as exc:
        run.status = "error"
        run.error_text = f"{type(exc).__name__}: {exc}"
        run.parsed_count = converted_count
        run.finished_at = utc_now()
        db.commit()

        return {
            "status": "error",
            "run_id": run.id,
            "error": run.error_text,
            "converted_count": converted_count,
            "error_count": error_count,
        }

    finally:
        db.close()


def enqueue_then_process_markdown(enqueue_limit: int, process_limit: int) -> dict[str, Any]:
    enqueue_result = enqueue_missing_markdown_tasks(limit=enqueue_limit)
    process_result = process_markdown_queue(limit=process_limit)

    return {
        "enqueue": enqueue_result,
        "process": process_result,
    }
