## PDF Upload FastAPI Example

This small FastAPI app exposes an endpoint that accepts a multipart/form-data upload of a PDF file and streams it to a temporary folder on disk.

### Setup (Windows PowerShell)

```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

### Run the server

```powershell
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`. Interactive docs at `http://127.0.0.1:8000/docs`.

### Upload a PDF

Using curl (PowerShell includes curl):

```powershell
curl -X POST "http://127.0.0.1:8000/upload-pdf" `
  -H "Content-Type: multipart/form-data" `
  -F "file=@C:\path\to\your\file.pdf;type=application/pdf"
```

Or using Invoke-RestMethod:

```powershell
$Form = @{ file = Get-Item "C:\path\to\your\file.pdf" }
Invoke-RestMethod -Uri "http://127.0.0.1:8000/upload-pdf" -Method Post -Form $Form
```

### Extract lease key facts

```powershell
curl -X POST "http://127.0.0.1:8000/extract-lease-facts" `
  -H "Content-Type: multipart/form-data" `
  -F "file=@C:\Users\ashru\Downloads\New folder\Bayer 2015-10-05 Lease (1).pdf;type=application/pdf"
```

Response fields:
- `tenant_name`, `landlord_name`
- `property_address_and_suite`, `total_square_feet`
- `lease_commencement_date`, `lease_expiration_date` (DD-MM-YYYY)
- `proportionate_share`, `base_year`, `security_deposit`

### Response

```json
{
  "message": "PDF uploaded successfully",
  "original_filename": "file.pdf",
  "content_type": "application/pdf",
  "size_bytes": 123456,
  "saved_path": "C:\\Users\\<you>\\Downloads\\pdf_uploads\\tmpabcd1234.pdf"
}
```

Notes:
- Basic validation checks the filename/content-type and the `%PDF-` header.
- Files are saved under your Downloads folder in `pdf_uploads`.
- Increase or decrease chunk size in `app/main.py` via `CHUNK_SIZE_BYTES`.

### Run tests

```powershell
pytest -q
```

### One-command start (Windows PowerShell)

```powershell
./start.ps1
```

Options:
- `-NoReload`: run without auto-reload

### LLM-assisted extraction (optional)

Set your OpenAI API key:

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

The `/extract-lease-facts` endpoint will automatically use the LLM to fill in missing fields or improve accuracy. If the key is not set, it runs with regex-only extraction.


