"""Manual file parser — PDF, TXT, Markdown, CSV ingestion into manual_docs table."""

from pathlib import Path

from src.data.database import upsert_manual_doc
from src.data.models import ManualDoc
from src.utils.config import get_manual_dir
from src.utils.logger import get_logger, log_event

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


def _extract_pdf(path: Path) -> tuple[str, str]:
    """Extract text from PDF. Returns (text, status)."""
    try:
        import pdfplumber
        texts = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
        if not texts:
            return "", "failed"
        return "\n\n".join(texts), "success"
    except ImportError:
        logger.warning("pdfplumber not installed. Run: pip install pdfplumber")
        return "", "failed"
    except Exception as e:
        logger.warning("PDF extraction failed for %s: %s", path.name, e)
        return "", "failed"


def _extract_text(path: Path) -> tuple[str, str]:
    """Extract text from TXT / Markdown / CSV. Returns (text, status)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text, "success"
    except Exception as e:
        logger.warning("Text extraction failed for %s: %s", path.name, e)
        return "", "failed"


def ingest_file(file_path: Path, ticker: str, doc_type: str = "other") -> ManualDoc:
    """
    Parse a single file and store extracted text in the database.
    Returns the ManualDoc with final status.
    """
    doc = ManualDoc(
        ticker=ticker,
        file_name=file_path.name,
        file_path=str(file_path),
        doc_type=doc_type,
    )

    # Validate
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        doc.status = "failed"
        upsert_manual_doc(doc)
        return doc

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported file type: %s", file_path.suffix)
        doc.status = "failed"
        upsert_manual_doc(doc)
        return doc

    if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
        logger.error("File too large (>50MB): %s", file_path.name)
        doc.status = "failed"
        upsert_manual_doc(doc)
        return doc

    # Extract
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        text, status = _extract_pdf(file_path)
    else:
        text, status = _extract_text(file_path)

    doc.extracted_text = text if text else None
    doc.text_length = len(text)
    doc.status = status

    upsert_manual_doc(doc)
    log_event("manual_doc_ingested", {
        "ticker": ticker,
        "file_name": file_path.name,
        "text_length": doc.text_length,
        "status": doc.status,
    })
    logger.info("[Ingest] %s/%s → %s (%d chars)", ticker, file_path.name,
                doc.status, doc.text_length)
    return doc


def ingest_ticker_dir(ticker: str) -> list[ManualDoc]:
    """
    Scan data/manual/{ticker}/ and ingest all new/unprocessed files.
    Returns list of ingested ManualDoc objects.
    """
    manual_dir = get_manual_dir(ticker)
    results: list[ManualDoc] = []

    for file_path in sorted(manual_dir.iterdir()):
        if file_path.is_dir() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        doc = ingest_file(file_path, ticker)
        results.append(doc)

    return results


def ingest_all() -> list[ManualDoc]:
    """Scan all sub-directories under data/manual/ and ingest new files."""
    base = get_manual_dir()
    results: list[ManualDoc] = []
    for subdir in sorted(base.iterdir()):
        if subdir.is_dir():
            results.extend(ingest_ticker_dir(subdir.name))
    return results


def get_manual_context(ticker: str, max_chars: int = 8000) -> str:
    """
    Retrieve all manual doc text for a ticker as a single context string,
    truncated to max_chars. Used by LLM agents to inject into prompts.
    """
    from src.data.database import get_manual_docs

    docs = get_manual_docs(ticker)
    if not docs:
        return ""

    parts = []
    total = 0
    for doc in docs:
        text = doc.get("extracted_text") or ""
        if not text:
            continue
        header = f"\n\n=== [{doc['doc_type'].upper()}] {doc['file_name']} ===\n"
        chunk = header + text[:max_chars - total - len(header)]
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break

    return "\n".join(parts)[:max_chars]
