from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover
    pdfium = None

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


class RegionSelectorWidget(QLabel):
    regionChanged = Signal(dict)

    def __init__(self) -> None:
        super().__init__('Sem přetáhněte oblast čtení myší')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setProperty('regionCanvas', True)
        self.setMinimumSize(QSize(280, 240))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._base_pixmap = QPixmap()
        self._drag_origin: QPoint | None = None
        self._current_rect = QRect()
        self._preview_path: Path | None = None
        self._page_no = 1
        self._page_count = 1
        self._zoom = 100

    @property
    def page_count(self) -> int:
        return self._page_count

    @property
    def page_no(self) -> int:
        return self._page_no

    @property
    def zoom(self) -> int:
        return self._zoom

    def set_preview_path(self, path: str, *, page_no: int = 1, zoom: int | None = None) -> None:
        self._preview_path = Path(path)
        self._page_no = max(1, int(page_no or 1))
        if zoom is not None:
            self._zoom = max(25, min(int(zoom), 300))
        self._render_preview()

    def set_page_no(self, page_no: int) -> None:
        self._page_no = max(1, int(page_no or 1))
        self._render_preview()

    def set_zoom(self, zoom: int) -> None:
        self._zoom = max(25, min(int(zoom), 300))
        self._render_preview()

    def zoom_in(self) -> int:
        self.set_zoom(self._zoom + 25)
        return self._zoom

    def zoom_out(self) -> int:
        self.set_zoom(self._zoom - 25)
        return self._zoom

    def _render_preview(self) -> None:
        if not self._preview_path or not self._preview_path.exists():
            self._base_pixmap = QPixmap()
            self.setPixmap(QPixmap())
            self.setText('Náhled není dostupný. Vyberte PDF nebo obrázek.')
            return
        suffix = self._preview_path.suffix.lower()
        pixmap = QPixmap()
        if suffix == '.pdf' and pdfium is not None:
            pixmap = self._render_pdf_page(self._preview_path)
        elif suffix in {'.png', '.jpg', '.jpeg', '.bmp'}:
            pixmap = QPixmap(str(self._preview_path))
            self._page_count = 1
            self._page_no = 1
        if pixmap.isNull():
            self._base_pixmap = QPixmap()
            self.setPixmap(QPixmap())
            self.setText(f'Nepodařilo se vyrenderovat náhled souboru {self._preview_path.name}.')
            return
        self._base_pixmap = pixmap
        self._current_rect = QRect()
        self._apply_overlay()

    def _render_pdf_page(self, path: Path) -> QPixmap:
        document = None
        page = None
        bitmap = None
        try:
            document = pdfium.PdfDocument(path.read_bytes())
            self._page_count = max(1, len(document))
            self._page_no = max(1, min(self._page_no, self._page_count))
            page = document.get_page(self._page_no - 1)
            bitmap = page.render(scale=max(self._zoom / 100.0, 0.25))
            if Image is None:
                return QPixmap()
            buffer = BytesIO()
            bitmap.to_pil().convert('RGBA').save(buffer, format='PNG')
            image = QImage.fromData(buffer.getvalue(), 'PNG')
            return QPixmap.fromImage(image)
        except Exception:
            self._page_count = 1
            self._page_no = 1
            return QPixmap()
        finally:
            if page is not None:
                page.close()
            if hasattr(bitmap, 'close'):
                bitmap.close()
            if document is not None:
                document.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and not self._base_pixmap.isNull():
            self._drag_origin = event.position().toPoint()
            self._current_rect = QRect(self._drag_origin, self._drag_origin)
            self._apply_overlay()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_origin is not None:
            self._current_rect = QRect(self._drag_origin, event.position().toPoint()).normalized()
            self._apply_overlay()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_origin is not None:
            self._current_rect = QRect(self._drag_origin, event.position().toPoint()).normalized()
            self._drag_origin = None
            self._apply_overlay()
            if not self._base_pixmap.isNull():
                self.regionChanged.emit(
                    {
                        'x': self._current_rect.x(),
                        'y': self._current_rect.y(),
                        'width': self._current_rect.width(),
                        'height': self._current_rect.height(),
                        'page_no': self._page_no,
                        'zoom': self._zoom,
                    }
                )
        super().mouseReleaseEvent(event)

    def _apply_overlay(self) -> None:
        if self._base_pixmap.isNull():
            return
        pixmap = self._base_pixmap.copy()
        if not self._current_rect.isNull():
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(Qt.GlobalColor.red)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(self._current_rect)
            painter.end()
        self.setPixmap(pixmap)
        self.resize(pixmap.size())
        self.setText('')


class DocumentPreviewWidget(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setProperty('previewPanel', True)
        self._label = QLabel('Vyberte doklad pro náhled')
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setMinimumSize(QSize(240, 180))
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setWidget(self._label)
        self._path: Path | None = None
        self._page = 1
        self._page_count = 1
        self._zoom = 100
        self._fit_to_width = False

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_count(self) -> int:
        return self._page_count

    @property
    def zoom(self) -> int:
        return self._zoom

    def load_document(self, path: str | None, *, page: int = 1) -> None:
        self._path = Path(path) if path else None
        self._page = max(1, int(page or 1))
        self._render_current_page()

    def set_zoom(self, zoom: int) -> int:
        self._fit_to_width = False
        self._zoom = max(20, min(int(zoom), 400))
        self._render_current_page()
        return self._zoom

    def fit_width(self) -> int:
        self._fit_to_width = True
        self._render_current_page()
        return self._zoom

    def set_page(self, page: int) -> None:
        self._page = max(1, int(page or 1))
        self._render_current_page()

    def _render_current_page(self) -> None:
        if self._path is None or not self._path.exists():
            self._label.setPixmap(QPixmap())
            self._label.setText('Vyberte doklad pro náhled')
            return
        suffix = self._path.suffix.lower()
        pixmap = QPixmap()
        if suffix == '.pdf' and pdfium is not None:
            pixmap = self._render_pdf_page(self._path)
        elif suffix in {'.png', '.jpg', '.jpeg', '.bmp'}:
            pixmap = QPixmap(str(self._path))
            self._page = 1
            self._page_count = 1
        if pixmap.isNull():
            self._label.setPixmap(QPixmap())
            self._label.setText(f'Náhled souboru {self._path.name} není dostupný.')
            return
        self._label.setText('')
        self._label.setPixmap(pixmap)
        self._label.resize(pixmap.size())

    def _render_pdf_page(self, path: Path) -> QPixmap:
        document = None
        page = None
        bitmap = None
        try:
            document = pdfium.PdfDocument(path.read_bytes())
            self._page_count = max(1, len(document))
            self._page = max(1, min(self._page, self._page_count))
            page = document.get_page(self._page - 1)
            if self._fit_to_width and self.viewport().width() > 0:
                self._zoom = max(20, int(round((self.viewport().width() / max(page.get_width(), 1)) * 100)))
            bitmap = page.render(scale=max(self._zoom / 100.0, 0.25))
            if Image is None:
                return QPixmap()
            buffer = BytesIO()
            bitmap.to_pil().convert('RGBA').save(buffer, format='PNG')
            image = QImage.fromData(buffer.getvalue(), 'PNG')
            return QPixmap.fromImage(image)
        except Exception:
            self._page_count = 1
            self._page = 1
            return QPixmap()
        finally:
            if page is not None:
                page.close()
            if hasattr(bitmap, 'close'):
                bitmap.close()
            if document is not None:
                document.close()
