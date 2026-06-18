"""Document loaders: PDF, DOCX, TXT, URL -> str."""
import io
from pathlib import Path


def load_pdf(path: str) -> str:
    """Extract text from PDF. Raises ValueError on failure."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("PDF is encrypted/password-protected")
        parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
        result = "\n".join(parts).strip()
        if not result:
            raise ValueError("No extractable text in PDF")
        return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF: {exc}") from exc


def load_docx(path: str) -> str:
    """Extract text from DOCX."""
    try:
        from docx import Document
        doc = Document(path)
        parts = [para.text for para in doc.paragraphs if para.text.strip()]
        result = "\n".join(parts).strip()
        if not result:
            raise ValueError("No extractable text in DOCX")
        return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to parse DOCX: {exc}") from exc


def load_txt(path: str) -> str:
    """Read plain text file, try utf-8 then latin-1."""
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        try:
            return p.read_text(encoding="latin-1", errors="ignore").strip()
        except Exception as exc:
            raise ValueError(f"Failed to read text file: {exc}") from exc


async def load_url(url: str) -> str:
    """Fetch URL and extract readable text via trafilatura."""
    try:
        import httpx
        import trafilatura

        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if not text or not text.strip():
            raise ValueError("trafilatura could not extract text from URL")
        return text.strip()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to load URL: {exc}") from exc
