from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict, Union, Optional
from pathlib import Path
import tempfile
import os
import re
from dateutil import parser as date_parser
from pdfminer.high_level import extract_text
from tenacity import retry, stop_after_attempt, wait_exponential

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


app = FastAPI(title="PDF Upload API", version="0.1.0")


CHUNK_SIZE_BYTES = 1024 * 1024  # 1 MiB


def is_probably_pdf(file_start: bytes) -> bool:
    """Return True if the byte sequence looks like the start of a PDF file."""
    return file_start.startswith(b"%PDF-")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)) -> Dict[str, Union[str, int]]:
    """Accept a PDF via multipart/form-data and save it to a temporary folder.

    Returns metadata about the stored file.
    """
    if file is None:
        raise HTTPException(status_code=400, detail="No file uploaded")

    content_type = file.content_type or ""
    original_filename = file.filename or "uploaded.pdf"

    if not original_filename.lower().endswith(".pdf") and content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF with content-type application/pdf")

    # Peek and validate that the file starts like a PDF
    first_bytes = await file.read(5)
    await file.seek(0)

    if not is_probably_pdf(first_bytes):
        # You may choose to reject here instead of being lenient
        # For stricter validation uncomment the next line
        # raise HTTPException(status_code=400, detail="Uploaded file does not appear to be a valid PDF")
        pass

    # Prepare output directory under user's Downloads/pdf_uploads
    user_home = Path.home()
    downloads_dir = user_home / "Downloads"
    output_dir = downloads_dir / "pdf_uploads"
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = ".pdf" if not original_filename.lower().endswith(".pdf") else ""

    # Stream to disk without loading entire file into memory
    total_bytes = 0
    with tempfile.NamedTemporaryFile(delete=False, dir=output_dir, suffix=suffix) as tmp:
        saved_path = Path(tmp.name)
        while True:
            chunk = await file.read(CHUNK_SIZE_BYTES)
            if not chunk:
                break
            tmp.write(chunk)
            total_bytes += len(chunk)

    return JSONResponse(
        {
            "message": "PDF uploaded successfully",
            "original_filename": original_filename,
            "content_type": content_type,
            "size_bytes": total_bytes,
            "saved_path": str(saved_path),
        }
    )


def _normalize_date(value: str) -> Optional[str]:
    """Parse arbitrary date strings and format as DD-MM-YYYY.

    Returns None if parsing fails.
    """
    try:
        dt = date_parser.parse(value, dayfirst=False, fuzzy=True)
        return dt.strftime("%d-%m-%Y")
    except Exception:
        return None


def _first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _extract_address_and_suite(text: str) -> (Optional[str], Optional[str]):
    lines = [l.strip() for l in text.splitlines()]
    address: Optional[str] = None
    suite: Optional[str] = None

    # 1) Label-based capture
    m = re.search(r"\b(?:Premises(?:\s*Address)?|Property(?:\s*Address)?|Address)\s*(?:\:|\-)\s*(.+)", text, re.IGNORECASE)
    if m:
        line = m.group(1).strip()
        s = re.search(r"(?:Suite|Ste\.?|#)\s*([\w\-]+)", line, re.IGNORECASE)
        if s:
            suite = s.group(1).strip()
            address = re.sub(r"(?:,?\s*)(?:Suite|Ste\.?|#)\s*[\w\-]+", "", line, flags=re.IGNORECASE).strip(" ,;-")
        else:
            address = line

    # 2) Street-pattern lines if still missing
    if not address:
        street_pat = r"^\s*\d{1,6}\s+.+?(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Lane|Ln\.|Drive|Dr\.|Court|Ct\.|Way|Terrace|Ter\.)\b.*"
        for idx, l in enumerate(lines):
            if re.search(street_pat, l, re.IGNORECASE):
                address = l.strip(" ,;-")
                cand_lines = [l]
                if idx + 1 < len(lines):
                    cand_lines.append(lines[idx + 1])
                for cl in cand_lines:
                    s = re.search(r"(?:Suite|Ste\.?|#)\s*([\w\-]+)", cl, re.IGNORECASE)
                    if s:
                        suite = s.group(1).strip()
                        break
                break

    # 3) Global suite search as last resort
    if not suite:
        s = re.search(r"(?:Suite|Ste\.?|#)\s*([\w\-]+)", text, re.IGNORECASE)
        if s:
            suite = s.group(1).strip()

    return address, suite


def _extract_lease_facts_from_text(text: str) -> Dict[str, Optional[str]]:
    # Parties
    tenant = _first_match(r"\bTenant\s*:\s*(.+)", text)
    landlord = _first_match(r"\bLandlord\s*:\s*(.+)", text)

    # Address and suite heuristics
    address, suite = _extract_address_and_suite(text)

    # Square footage
    square_feet_raw = _first_match(r"\b(?:Rentable|Leasable|Approx\.?|Total)?\s*(?:Square\s*Feet|Sq\.?\s*Ft\.?|SF)[^\d]*(\d[\d,\.]+)", text)
    square_feet = re.sub(r"[^\d\.]", "", square_feet_raw) if square_feet_raw else None

    # Dates
    commence_raw = _first_match(r"\b(?:Lease\s*)?Commencement\s*Date\s*:?\s*([^\n\r]+)", text)
    expire_raw = _first_match(r"\b(?:Lease\s*)?(?:Expiration|Expiry)\s*Date\s*:?\s*([^\n\r]+)", text)
    lease_commencement = _normalize_date(commence_raw) if commence_raw else None
    lease_expiration = _normalize_date(expire_raw) if expire_raw else None

    # Proportionate Share
    proportionate_share = _first_match(r"\bProportionate\s+Share\s*:?\s*(\d{1,2}(?:\.\d+)?\s*%)", text)

    # Base Year
    base_year = _first_match(r"\bBase\s+Year\s*:?\s*(\d{4})\b", text)

    # Security Deposit
    security_deposit = _first_match(r"\bSecurity\s+Deposit\s*:?\s*(?:\$\s*)?([\d,]+(?:\.\d{2})?)\b", text)
    if not security_deposit:
        # Detect explicit none
        none_flag = re.search(r"\bSecurity\s+Deposit\b[^\n\r]*(?:None|N/A|No\s+Deposit)", text, re.IGNORECASE)
        security_deposit = "None" if none_flag else None

    combined_addr = None
    if address and suite:
        combined_addr = f"{address} Suite {suite}"
    elif address:
        combined_addr = address
    elif suite:
        combined_addr = f"Suite {suite}"

    return {
        "tenant_name": tenant,
        "landlord_name": landlord,
        "property_address": address,
        "suite": suite,
        "property_address_and_suite": combined_addr,
        "total_square_feet": square_feet,
        "lease_commencement_date": lease_commencement,
        "lease_expiration_date": lease_expiration,
        "proportionate_share": proportionate_share,
        "base_year": base_year,
        "security_deposit": security_deposit if security_deposit else None,
    }


LEASE_FACTS_SCHEMA = {
    "tenant_name": None,
    "landlord_name": None,
    "property_address": None,
    "suite": None,
    "property_address_and_suite": None,
    "total_square_feet": None,
    "lease_commencement_date": None,
    "lease_expiration_date": None,
    "proportionate_share": None,
    "base_year": None,
    "security_deposit": None,
}


def _format_facts_output(facts: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    out = dict(LEASE_FACTS_SCHEMA)
    out.update({k: (v if v not in ("", None) else None) for k, v in facts.items() if k in out})
    return out


def _build_llm_prompt(document_text: str) -> str:
    return (
        "You are an expert lease analyst. Extract the following key facts from the lease text.\n"
        "Return ONLY a JSON object with these exact keys: tenant_name, landlord_name, property_address, suite, property_address_and_suite, "
        "total_square_feet, lease_commencement_date, lease_expiration_date, proportionate_share, base_year, security_deposit.\n"
        "Instructions:\n"
        "- property_address: the street address only (no suite).\n"
        "- suite: only the suite/ste/# designator (e.g., 120B).\n"
        "- property_address_and_suite: a human-readable combination like '<address> Suite <suite>' when both exist.\n"
        "- Dates must be DD-MM-YYYY.\n"
        "- If a field is not present, use null.\n"
        "- For percentages include the % sign.\n"
        "- For currency include only numbers and decimal point (e.g., 1234.56).\n\n"
        "Lease Text:\n" + document_text[:12000]
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6))
def _call_openai_json(prompt: str, model: str = "gpt-4o-mini") -> Dict[str, Optional[str]]:
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not available. Install requirements and set OPENAI_API_KEY.")
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": "Extract structured facts as valid JSON only."}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=400,
    )
    content = response.choices[0].message.content or "{}"
    import json as _json

    try:
        raw = _json.loads(content)
        return _format_facts_output(raw)
    except Exception as exc:
        # If LLM returns invalid JSON, fallback to empty structure
        return dict(LEASE_FACTS_SCHEMA)


def _merge_facts(primary: Dict[str, Optional[str]], secondary: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    merged = dict(LEASE_FACTS_SCHEMA)
    for key in merged.keys():
        merged[key] = primary.get(key) or secondary.get(key)
    return merged


@app.post("/extract-lease-facts")
async def extract_lease_facts(file: UploadFile = File(...)) -> Dict[str, Optional[str]]:
    """Accept a lease PDF and extract key facts from the document text.

    Dates are formatted as DD-MM-YYYY.
    """
    if file is None:
        raise HTTPException(status_code=400, detail="No file uploaded")

    content_type = file.content_type or ""
    original_filename = file.filename or "lease.pdf"
    if not original_filename.lower().endswith(".pdf") and content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF with content-type application/pdf")

    # Persist upload to a temp file so pdfminer can read it efficiently
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        temp_path = Path(tmp.name)
        while True:
            chunk = await file.read(CHUNK_SIZE_BYTES)
            if not chunk:
                break
            tmp.write(chunk)

    try:
        text = extract_text(str(temp_path)) or ""
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read PDF: {exc}")
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass

    # Deterministic baseline
    regex_facts = _extract_lease_facts_from_text(text)

    # Optional LLM enhancement if API key present
    llm_facts: Dict[str, Optional[str]] = {}
    if os.getenv("OPENAI_API_KEY"):
        prompt = _build_llm_prompt(text)
        try:
            llm_facts = _call_openai_json(prompt)
        except Exception:
            llm_facts = {}

    # Merge, preferring regex facts, filling gaps with LLM
    merged = _merge_facts(regex_facts, llm_facts)
    return _format_facts_output(merged)

