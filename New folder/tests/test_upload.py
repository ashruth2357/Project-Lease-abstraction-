from fastapi.testclient import TestClient
from app.main import app
import io


client = TestClient(app)


def make_pdf_bytes() -> bytes:
    # Minimal valid-looking PDF header and EOF markers
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_pdf_success():
    pdf_content = make_pdf_bytes()
    files = {"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")}
    response = client.post("/upload-pdf", files=files)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["message"] == "PDF uploaded successfully"
    assert data["original_filename"] == "test.pdf"
    assert data["content_type"] in ("application/pdf", None)
    assert data["size_bytes"] == len(pdf_content)
    assert data["saved_path"]


def test_upload_rejects_non_pdf():
    files = {"file": ("notpdf.txt", io.BytesIO(b"hello world"), "text/plain")}
    response = client.post("/upload-pdf", files=files)
    assert response.status_code == 400
    assert "File must be a PDF" in response.text


