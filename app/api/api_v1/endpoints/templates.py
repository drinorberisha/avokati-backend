import asyncio
import io
import json
import logging
import re
import uuid
import zipfile
from typing import Any, List
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.auth import get_current_user
from app.core.consent import require_ai_consent
from app.core.supabase import get_supabase_client
from app.core.tenancy import require_office
from app.schemas.template import TemplateCreate, TemplateOut, TemplateUpdate
from app.schemas.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_VAR_TYPES = {"text", "number", "date", "select", "boolean"}
_TOKEN_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_FIELDS = "id, title, description, category, language, status, content, variables, source_type, created_at, updated_at"


def _normalize(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row.get("description"),
        "category": row.get("category"),
        "language": row.get("language"),
        "status": row.get("status") or "draft",
        "content": row.get("content") or "",
        "variables": row.get("variables") or [],
        "source_type": row.get("source_type") or "manual",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ─── Upload-to-template extraction helpers ──────────────────────────────────

def _extract_pdf(data: bytes) -> str:
    import fitz  # PyMuPDF — already a backend dependency (used by build_v2_index)

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def _extract_docx(data: bytes) -> str:
    # DOCX is a zip of XML; read the main document part with the stdlib so we
    # don't add a dependency. Concatenate each paragraph's text runs.
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for p in root.iter(f"{_DOCX_NS}p"):
        runs = [t.text for t in p.iter(f"{_DOCX_NS}t") if t.text]
        paragraphs.append("".join(runs))
    return "\n".join(paragraphs)


_SCAN_WATERMARK_RE = re.compile(r"(?i)(scanned\s+(with|by)\s+)?cam\s*scanner|adobe\s+scan")


def _is_low_text(text: str) -> bool:
    """True if a PDF/DOCX yielded too little real text to template — typically a
    scanned-image PDF where the only embedded text is a scanner watermark
    (e.g. CamScanner stamps every page). OCR is out of scope for now."""
    cleaned = _SCAN_WATERMARK_RE.sub(" ", text)
    letters = re.findall(r"[^\W\d_]", cleaned, flags=re.UNICODE)
    return len(letters) < 200


def _normalize_variables(content: str, variables: Any) -> list[dict]:
    """Reconcile the LLM's variable list with the {{tokens}} actually present:
    keep declared variables, coerce types, add an id, and backfill any token
    that has no declared variable."""
    by_name: dict[str, dict] = {}
    for v in variables if isinstance(variables, list) else []:
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "").strip()
        if not name:
            continue
        vtype = v.get("type")
        by_name[name] = {
            "id": uuid.uuid4().hex[:9],
            "name": name,
            "type": vtype if vtype in _VALID_VAR_TYPES else "text",
            "required": bool(v.get("required", True)),
            "description": v.get("description") or None,
            "options": v.get("options") if isinstance(v.get("options"), list) else None,
        }
    for token in _TOKEN_RE.findall(content or ""):
        token = token.strip()
        if token and token not in by_name:
            by_name[token] = {
                "id": uuid.uuid4().hex[:9],
                "name": token,
                "type": "text",
                "required": True,
                "description": None,
                "options": None,
            }
    return list(by_name.values())


def _finalize_draft(data: dict) -> dict:
    """Validate + normalize a parsed template dict (from Gemini or DeepSeek)."""
    content = data.get("content_html") or data.get("content") or ""
    if not data.get("title") or not content:
        raise HTTPException(status_code=502, detail="The document could not be templated.")
    return {
        "title": str(data["title"])[:200],
        "description": (data.get("description") or None),
        "category": (data.get("category") or None),
        "language": (data.get("language") or None),
        "content": content,
        "variables": _normalize_variables(content, data.get("variables")),
    }


def _parse_template_json(raw: str) -> dict:
    text = (raw or "").strip()
    # Strip ```json ... ``` fences the model may add.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first {...} block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise HTTPException(status_code=502, detail="Could not parse the extracted template.")
        data = json.loads(m.group(0))
    return _finalize_draft(data)


def _run_extraction(text: str) -> dict:
    from app.ai.llm import complete
    from app.ai.prompts.template_extract import build_messages

    result = complete(build_messages(text), temperature=0.2, max_tokens=8000)
    return _parse_template_json(result.text)


@router.post("/extract", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def extract_template(
    *,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
    _consent: None = Depends(require_ai_consent),
) -> Any:
    """Upload a PDF/DOCX/image contract → analyse it → create a draft template
    the user can refine in the editor. Saved with source_type='imported'.

    When GEMINI_API_KEY is set, Gemini 2.5 Flash reads the raw file directly
    (incl. scanned PDFs / photos — built-in OCR). Otherwise we fall back to
    PyMuPDF/DOCX text extraction + DeepSeek, which only handles digital text.
    """
    from app.ai.gemini_extract import (
        gemini_enabled,
        extract_template_from_file,
        extract_template_from_text,
        SUPPORTED_INLINE_MIME,
    )

    raw = await file.read()
    name = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()

    is_pdf = name.endswith(".pdf") or "pdf" in ctype
    is_docx = name.endswith(".docx") or "wordprocessingml" in ctype or "officedocument" in ctype
    is_image = ctype.startswith("image/") or name.endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif")
    )

    scanned_msg = {
        "code": "EMPTY_EXTRACTION",
        "message": (
            "This file has no readable text — it looks like a scanned image "
            "(e.g. a CamScanner/Adobe Scan PDF). Add a GEMINI_API_KEY to enable "
            "scanned-document reading, or upload a text-based PDF or DOCX."
        ),
    }

    try:
        if gemini_enabled():
            # Gemini path — reads the document directly (digital or scanned).
            if is_pdf or is_image:
                mime = "application/pdf" if is_pdf else (ctype or "image/jpeg")
                if mime not in SUPPORTED_INLINE_MIME:
                    mime = "application/pdf" if is_pdf else "image/jpeg"
                draft = _finalize_draft(await extract_template_from_file(raw, mime))
            elif is_docx:
                text = (_extract_docx(raw) or "").strip()
                if len(text) < 50:
                    raise HTTPException(status_code=422, detail=scanned_msg)
                draft = _finalize_draft(await extract_template_from_text(text[:40000]))
            else:
                raise HTTPException(status_code=400, detail="Unsupported file type. Upload a PDF, DOCX or image.")
        else:
            # Fallback path — digital text only (no OCR).
            if is_pdf:
                text = _extract_pdf(raw)
            elif is_docx:
                text = _extract_docx(raw)
            else:
                raise HTTPException(status_code=400, detail="Unsupported file type. Upload a PDF or DOCX.")
            text = (text or "").strip()
            if len(text) < 100 or _is_low_text(text):
                raise HTTPException(status_code=422, detail=scanned_msg)
            draft = await asyncio.to_thread(_run_extraction, text[:20000])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        logger.exception("template extraction failed")
        raise HTTPException(status_code=502, detail="Template extraction failed. Please try again.") from exc

    payload = {
        "office_id": office_id,
        "owner_id": str(current_user.id),
        "source_type": "imported",
        "status": "draft",
        "title": draft["title"],
        "description": draft["description"],
        "category": draft["category"],
        "language": draft["language"],
        "content": draft["content"],
        "variables": draft["variables"],
    }
    resp = supabase.table("templates").insert(payload).execute()
    if not resp.data:
        raise HTTPException(status_code=400, detail="Failed to save the imported template")
    return _normalize(resp.data[0])


@router.get("/", response_model=List[TemplateOut])
async def list_templates(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("templates")
        .select(_FIELDS)
        .eq("office_id", office_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return [_normalize(row) for row in resp.data or []]


@router.post("/", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    *,
    template_in: TemplateCreate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = template_in.model_dump(mode="json")
    payload["office_id"] = office_id
    payload["owner_id"] = str(current_user.id)
    payload["source_type"] = "manual"
    resp = supabase.table("templates").insert(payload).execute()
    if not resp.data:
        raise HTTPException(status_code=400, detail="Failed to create template")
    return _normalize(resp.data[0])


@router.get("/{template_id}", response_model=TemplateOut)
async def read_template(
    *,
    template_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("templates")
        .select(_FIELDS)
        .eq("id", template_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return _normalize(resp.data[0])


@router.patch("/{template_id}", response_model=TemplateOut)
async def update_template(
    *,
    template_id: str,
    template_in: TemplateUpdate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    update_data = template_in.model_dump(mode="json", exclude_unset=True)
    # Never let a payload move a template to another office.
    update_data.pop("office_id", None)
    if not update_data:
        raise HTTPException(status_code=400, detail="Nothing to update")
    resp = (
        supabase.table("templates")
        .update(update_data)
        .eq("id", template_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return _normalize(resp.data[0])


@router.delete("/{template_id}")
async def delete_template(
    *,
    template_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("templates")
        .delete()
        .eq("id", template_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True}
