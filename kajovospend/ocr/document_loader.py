from __future__ import annotations

import base64
import hashlib
import io
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional dependency guard
    pdfium = None


def _load_pillow_image_module() -> Any:
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - exercised via caller secondary path
        raise RuntimeError(f'Pillow není dostupný nebo je poškozený: {exc}') from exc
    return Image


def _load_pdf_reader() -> Any:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - exercised via caller secondary path
        raise RuntimeError(f'PDF reader není dostupný nebo je poškozený: {exc}') from exc
    return PdfReader


@dataclass(slots=True)
class LoadedPage:
    page_no: int
    text: str
    width: float = 0.0
    height: float = 0.0
    rotation_deg: float = 0.0
    text_layer_present: bool = False
    source_kind: str = ''
    ocr_status: str = ''
    confidence_avg: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LoadedDocument:
    file_type: str
    pages: list[LoadedPage]
    warnings: list[str] = field(default_factory=list)
    source_path: str = ''
    source_sha256: str = ''
    source_bytes: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def merged_text(self) -> str:
        return '\n\n'.join(page.text.strip() for page in self.pages if page.text.strip()).strip()

    @property
    def has_readable_text(self) -> bool:
        return any(page.text.strip() for page in self.pages)


class DocumentLoader:
    IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}

    def __init__(self, *, tesseract_cmd: str = 'tesseract', pdf_render_scale: float = 300 / 72) -> None:
        self.tesseract_cmd = tesseract_cmd
        self.pdf_render_scale = pdf_render_scale

    def inspect_page_count(self, source_path: Path) -> int:
        return self.load(source_path).page_count

    def build_openai_image_inputs(
        self,
        source_path: Path,
        *,
        max_pages: int = 4,
        page_numbers: list[int] | None = None,
    ) -> list[dict[str, str]]:
        suffix = source_path.suffix.lower()
        if suffix in self.IMAGE_SUFFIXES:
            payload = self._image_to_data_url(source_path)
            return [{'type': 'input_image', 'image_url': payload, 'detail': 'high', 'page_no': '1'}] if payload else []
        if suffix == '.pdf':
            return self._pdf_to_openai_images(source_path, max_pages=max_pages, page_numbers=page_numbers)
        return []

    def load(self, source_path: Path) -> LoadedDocument:
        source_path = Path(source_path).expanduser().resolve()
        suffix = source_path.suffix.lower()
        if suffix == '.txt':
            return self._attach_provenance(source_path, self._load_text(source_path, file_type='text'))
        if suffix == '.pdf':
            return self._attach_provenance(source_path, self._load_pdf(source_path))
        if suffix in self.IMAGE_SUFFIXES:
            return self._attach_provenance(source_path, self._load_image(source_path))
        return self._attach_provenance(source_path, self._load_text(source_path, file_type='generic-text'))

    def _attach_provenance(self, source_path: Path, document: LoadedDocument) -> LoadedDocument:
        raw = source_path.read_bytes()
        document.source_path = str(source_path)
        document.source_bytes = len(raw)
        document.source_sha256 = hashlib.sha256(raw).hexdigest()
        document.provenance.update({
            'source_path': str(source_path),
            'source_name': source_path.name,
            'source_suffix': source_path.suffix.lower(),
            'source_sha256': document.source_sha256,
            'source_bytes': len(raw),
            'loader_branch': document.file_type,
        })
        return document

    def _load_text(self, source_path: Path, *, file_type: str) -> LoadedDocument:
        text = source_path.read_text(encoding='utf-8', errors='replace')
        raw_pages = text.split('')
        pages = [
            LoadedPage(
                page_no=index,
                text=content.strip(),
                text_layer_present=bool(content.strip()),
                source_kind=file_type,
                ocr_status='not_needed',
                metadata={'source_branch': file_type},
            )
            for index, content in enumerate(raw_pages or [''], start=1)
        ]
        return LoadedDocument(file_type=file_type, pages=pages)

    def _load_pdf(self, source_path: Path) -> LoadedDocument:
        try:
            header = source_path.read_bytes()[:5]
        except OSError:
            header = b''
        if header != b'%PDF-':
            secondary = self._load_text(source_path, file_type='pdf-text-secondary')
            secondary.warnings.append('Soubor s příponou PDF nemá validní PDF hlavičku, byl načten jako textový režim.')
            secondary.provenance['fallback_reason'] = 'invalid_pdf_header'
            return secondary
        try:
            pdf_reader = _load_pdf_reader()
            reader = pdf_reader(str(source_path))
        except Exception as exc:
            secondary = self._load_text(source_path, file_type='pdf-text-secondary')
            secondary.warnings.append(f'PDF parse selhal, dokument byl načten jako textový režim. Detail: {exc}')
            secondary.provenance['fallback_reason'] = 'pdf_parse_failed'
            return secondary

        pages: list[LoadedPage] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or '').strip()
            rotation = float(page.get('/Rotate', 0) or 0)
            media_box = page.mediabox
            width = float(media_box.width or 0)
            height = float(media_box.height or 0)
            pages.append(
                LoadedPage(
                    page_no=index,
                    text=text,
                    width=width,
                    height=height,
                    rotation_deg=rotation,
                    text_layer_present=bool(text),
                    source_kind='pdf-text-layer' if text else 'pdf-no-text-layer',
                    ocr_status='not_needed' if text else 'pending',
                    metadata={'source_branch': 'pdf_text_layer' if text else 'pdf_raster_ocr'},
                )
            )

        warnings: list[str] = []
        missing_page_numbers = [page.page_no for page in pages if not page.text_layer_present]
        if missing_page_numbers:
            raster_results, raster_warnings = self._ocr_pdf_pages(source_path, missing_page_numbers)
            warnings.extend(raster_warnings)
            for page in pages:
                raster_page = raster_results.get(page.page_no)
                if raster_page is None:
                    continue
                page.text = str(raster_page.get('text') or '').strip()
                page.source_kind = 'pdf-raster-ocr'
                page.ocr_status = 'completed' if page.text else 'failed'
                page.confidence_avg = raster_page.get('confidence_avg')
                if raster_page.get('width'):
                    page.width = float(raster_page['width'])
                if raster_page.get('height'):
                    page.height = float(raster_page['height'])
                metadata = raster_page.get('metadata') or {}
                if isinstance(metadata, dict):
                    page.metadata.update(metadata)
            unresolved = [page.page_no for page in pages if not page.text.strip()]
            if unresolved and not raster_results:
                warnings.append('PDF neobsahuje textovou vrstvu a raster OCR pro PDF není dostupné.')
        return LoadedDocument(file_type='pdf', pages=pages or [LoadedPage(page_no=1, text='')], warnings=warnings, provenance={'missing_page_numbers': missing_page_numbers})

    def _load_image(self, source_path: Path) -> LoadedDocument:
        text, warnings, width, height, confidence = self._ocr_image_path(source_path)
        return LoadedDocument(
            file_type='image',
            pages=[
                LoadedPage(
                    page_no=1,
                    text=text.strip(),
                    width=float(width),
                    height=float(height),
                    text_layer_present=False,
                    source_kind='image-ocr',
                    ocr_status='completed' if text.strip() else 'failed',
                    confidence_avg=confidence,
                    metadata={'source_branch': 'image_ocr'},
                )
            ],
            warnings=warnings,
        )

    def _ocr_image_path(self, source_path: Path) -> tuple[str, list[str], int, int, float | None]:
        try:
            image_module = _load_pillow_image_module()
            with image_module.open(source_path) as image:
                width, height = image.size
        except Exception as exc:
            return '', [str(exc)], 0, 0, None
        text, warnings, confidence = self._run_tesseract(source_path)
        return text, warnings, width, height, confidence

    def _ocr_pdf_pages(self, source_path: Path, page_numbers: list[int]) -> tuple[dict[int, dict[str, Any]], list[str]]:
        if not page_numbers:
            return {}, []
        if pdfium is None:
            return {}, ['PDF raster OCR není dostupné: chybí knihovna pypdfium2.']
        try:
            document = pdfium.PdfDocument(str(source_path))
        except Exception as exc:
            return {}, [f'PDF raster OCR selhalo při otevření dokumentu: {exc}']

        results: dict[int, dict[str, Any]] = {}
        warnings: list[str] = []
        with TemporaryDirectory(prefix='kajovospend-pdf-ocr-') as temp_dir:
            temp_root = Path(temp_dir)
            for page_no in page_numbers:
                try:
                    page = document[page_no - 1]
                    bitmap = page.render(scale=self.pdf_render_scale)
                    image = bitmap.to_pil().convert('L')
                except Exception as exc:
                    warnings.append(f'PDF raster OCR selhalo při renderu strany {page_no}: {exc}')
                    continue
                image_path = temp_root / f'page_{page_no}.png'
                try:
                    image.save(image_path, format='PNG')
                except Exception as exc:
                    warnings.append(f'PDF raster OCR selhalo při uložení strany {page_no}: {exc}')
                    continue
                text, page_warnings, width, height, confidence = self._ocr_image_path(image_path)
                warnings.extend([f'Strana {page_no}: {warning}' for warning in page_warnings])
                results[page_no] = {
                    'text': text,
                    'width': width,
                    'height': height,
                    'confidence_avg': confidence,
                    'metadata': {'rasterized_from_pdf': True, 'page_no': page_no},
                }
        return results, warnings

    def _pdf_to_openai_images(
        self,
        source_path: Path,
        *,
        max_pages: int = 4,
        page_numbers: list[int] | None = None,
    ) -> list[dict[str, str]]:
        if pdfium is None or max_pages <= 0:
            return []
        try:
            document = pdfium.PdfDocument(str(source_path))
        except Exception:
            return []
        image_inputs: list[dict[str, str]] = []
        ordered_pages = [page_no for page_no in (page_numbers or list(range(1, len(document) + 1))) if 1 <= int(page_no) <= len(document)]
        if not ordered_pages:
            ordered_pages = list(range(1, len(document) + 1))
        for page_no in ordered_pages[:max_pages]:
            try:
                page = document[page_no - 1]
                bitmap = page.render(scale=self.pdf_render_scale)
                image = bitmap.to_pil().convert('RGB')
            except Exception:
                continue
            payload = self._pil_image_to_data_url(image)
            if payload:
                image_inputs.append({'type': 'input_image', 'image_url': payload, 'detail': 'high', 'page_no': str(page_no)})
        return image_inputs

    def _image_to_data_url(self, source_path: Path) -> str:
        try:
            image_module = _load_pillow_image_module()
            with image_module.open(source_path) as image:
                return self._pil_image_to_data_url(image.convert('RGB'))
        except Exception:
            return ''

    def _pil_image_to_data_url(self, image: Any) -> str:
        preview = image.copy()
        preview.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        preview.save(buffer, format='JPEG', quality=85, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{encoded}'

    def _run_tesseract(self, source_path: Path) -> tuple[str, list[str], float | None]:
        command = [
            self.tesseract_cmd,
            str(source_path),
            'stdout',
            '-l',
            'ces+eng',
            '--oem',
            '1',
            '--psm',
            '6',
            'tsv',
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=False, check=False, timeout=60)
        except FileNotFoundError:
            return '', ['Tesseract není dostupný v systému.'], None
        except subprocess.TimeoutExpired:
            return '', ['Tesseract překročil časový limit.'], None
        stdout = result.stdout.decode('utf-8', errors='replace') if isinstance(result.stdout, bytes) else str(result.stdout or '')
        stderr = result.stderr.decode('utf-8', errors='replace') if isinstance(result.stderr, bytes) else str(result.stderr or '')
        if result.returncode != 0:
            message = stderr.strip() or 'Tesseract selhal.'
            return '', [message], None
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines:
            return '', [], None
        header = lines[0].split('	')
        confidence_values: list[float] = []
        words: list[str] = []
        for line in lines[1:]:
            parts = line.split('	')
            if len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            text = str(row.get('text', '') or '').strip()
            if text:
                words.append(text)
            try:
                conf = float(str(row.get('conf', '') or '-1'))
            except ValueError:
                conf = -1.0
            if conf >= 0:
                confidence_values.append(conf)
        joined = ' '.join(words).strip()
        confidence = round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else None
        return joined, [], confidence
