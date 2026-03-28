from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional dependency load
    pdfium = None


class OcrLabelingBatchGenerator:
    def __init__(self, *, preview_scale: float = 144 / 72) -> None:
        self.preview_scale = preview_scale

    def prepare_from_corpus(
        self,
        corpus_dir: Path,
        review_pack_path: Path,
        output_dir: Path,
        *,
        limit: int = 50,
    ) -> dict[str, Path]:
        rows = self._load_review_pack(review_pack_path)
        selected = rows[: max(0, int(limit))]
        return self._build_batch(corpus_dir, selected, output_dir)

    def prepare_from_zip(
        self,
        zip_path: Path,
        review_pack_path: Path,
        output_dir: Path,
        *,
        limit: int = 50,
    ) -> dict[str, Path]:
        rows = self._load_review_pack(review_pack_path)
        selected = rows[: max(0, int(limit))]
        with TemporaryDirectory(prefix='kajovospend-labeling-batch-') as temp_dir:
            extracted = Path(temp_dir) / 'corpus'
            with zipfile.ZipFile(zip_path) as archive:
                for row in selected:
                    relative_path = str(row.get('relative_path') or '').replace('\\', '/')
                    if not relative_path:
                        continue
                    try:
                        archive.extract(relative_path, extracted)
                    except KeyError:
                        continue
            return self._build_batch(extracted, selected, output_dir)

    def _build_batch(self, corpus_dir: Path, selected: list[dict[str, str]], output_dir: Path) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        previews_dir = output_dir / 'previews'
        previews_dir.mkdir(parents=True, exist_ok=True)
        batch_rows: list[dict[str, Any]] = []
        for index, row in enumerate(selected, start=1):
            relative_path = str(row.get('relative_path') or '').replace('\\', '/')
            source_path = corpus_dir / relative_path
            preview_path = self._create_preview(source_path, previews_dir, index=index)
            batch_rows.append(
                {
                    'batch_index': index,
                    'relative_path': relative_path,
                    'preview_path': preview_path.as_posix() if preview_path else '',
                    'review_priority': row.get('review_priority', ''),
                    'review_reasons': row.get('review_reasons', ''),
                    'file_type': row.get('file_type', ''),
                    'page_count': row.get('page_count', ''),
                    'predicted_should_finalize': row.get('predicted_should_finalize', ''),
                    'incomplete_reason': row.get('incomplete_reason', ''),
                    'predicted_ico': row.get('predicted_ico', ''),
                    'predicted_document_number': row.get('predicted_document_number', ''),
                    'predicted_issued_at': row.get('predicted_issued_at', ''),
                    'predicted_total_with_vat': row.get('predicted_total_with_vat', ''),
                    'ocr_preview': row.get('ocr_preview', ''),
                    'expected_ico': row.get('expected_ico', ''),
                    'expected_document_number': row.get('expected_document_number', ''),
                    'expected_issued_at': row.get('expected_issued_at', ''),
                    'expected_total_with_vat': row.get('expected_total_with_vat', ''),
                    'expected_should_finalize': row.get('expected_should_finalize', ''),
                    'label_status': '',
                    'review_note': '',
                }
            )
        batch_path = output_dir / 'batch.csv'
        self._write_csv(batch_path, batch_rows)
        return {'batch': batch_path, 'previews_dir': previews_dir}

    def _load_review_pack(self, review_pack_path: Path) -> list[dict[str, str]]:
        with review_pack_path.open('r', encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        return sorted(
            rows,
            key=lambda row: (-int(row.get('review_priority') or 0), str(row.get('relative_path') or '')),
        )

    def _create_preview(self, source_path: Path, previews_dir: Path, *, index: int) -> Path | None:
        if not source_path.exists():
            return None
        slug = self._slugify(f'{index:03d}_{source_path.stem}')
        suffix = source_path.suffix.lower()
        if suffix == '.pdf':
            return self._render_pdf_preview(source_path, previews_dir / f'{slug}.png')
        if suffix in {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}:
            return self._render_image_preview(source_path, previews_dir / f'{slug}.png')
        return self._write_text_preview(source_path, previews_dir / f'{slug}.txt')

    def _render_pdf_preview(self, source_path: Path, destination: Path) -> Path | None:
        if pdfium is None:
            return self._write_text_preview(source_path, destination.with_suffix('.txt'))
        document = None
        try:
            document = pdfium.PdfDocument(str(source_path))
            page = document[0]
            bitmap = page.render(scale=self.preview_scale)
            image = bitmap.to_pil().convert('RGB')
            image.thumbnail((1400, 1400))
            image.save(destination, format='PNG')
            return destination
        except Exception:
            return self._write_text_preview(source_path, destination.with_suffix('.txt'))
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    pass

    def _render_image_preview(self, source_path: Path, destination: Path) -> Path | None:
        try:
            with Image.open(source_path) as image:
                preview = image.convert('RGB')
                preview.thumbnail((1400, 1400))
                preview.save(destination, format='PNG')
            return destination
        except Exception:
            return self._write_text_preview(source_path, destination.with_suffix('.txt'))

    def _write_text_preview(self, source_path: Path, destination: Path) -> Path | None:
        try:
            if source_path.suffix.lower() == '.txt':
                text = source_path.read_text(encoding='utf-8', errors='replace')
            else:
                text = f'Preview nebylo mozne vyrenderovat pro {source_path.name}'
            destination.write_text(text[:4000], encoding='utf-8')
            return destination
        except Exception:
            return None

    def _slugify(self, value: str) -> str:
        value = value.strip().replace('\\', '_').replace('/', '_')
        value = re.sub(r'[^A-Za-z0-9._-]+', '_', value)
        return value[:120].strip('._-') or 'preview'

    def _write_csv(self, destination: Path, rows: list[dict[str, Any]]) -> None:
        fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ['empty']
        with destination.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        key: json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (list, dict))
                        else value
                        for key, value in row.items()
                    }
                )


class OcrLabelingMergeService:
    MASTER_FIELDS = [
        'relative_path',
        'doc_type',
        'quality_band',
        'expected_ico',
        'expected_document_number',
        'expected_issued_at',
        'expected_total_with_vat',
        'expected_vat_summary',
        'expected_items_count',
        'expected_should_finalize',
        'observed_category',
        'observed_file_type',
        'observed_readable_text',
    ]

    def merge_batch_into_master(self, master_labels_path: Path, batch_path: Path, output_path: Path | None = None) -> Path:
        master_rows = self._load_csv(master_labels_path)
        batch_rows = self._load_csv(batch_path)
        master_by_path = {
            str(row.get('relative_path') or '').replace('\\', '/'): dict(row)
            for row in master_rows
            if str(row.get('relative_path') or '').strip()
        }
        changed = 0
        for batch_row in batch_rows:
            relative_path = str(batch_row.get('relative_path') or '').replace('\\', '/')
            if not relative_path:
                continue
            target = master_by_path.get(relative_path)
            if target is None:
                target = {'relative_path': relative_path}
                master_by_path[relative_path] = target
            before = {key: str(target.get(key) or '') for key in self.MASTER_FIELDS}
            self._apply_batch_row(target, batch_row)
            after = {key: str(target.get(key) or '') for key in self.MASTER_FIELDS}
            if before != after:
                changed += 1
        merged_rows = sorted(master_by_path.values(), key=lambda row: str(row.get('relative_path') or ''))
        destination = output_path or master_labels_path
        self._write_master_csv(destination, merged_rows)
        return destination

    def summarize_master_labels(self, master_labels_path: Path) -> dict[str, int]:
        rows = self._load_csv(master_labels_path)
        labeled = 0
        finalize = 0
        field_counts = {
            'expected_ico': 0,
            'expected_document_number': 0,
            'expected_issued_at': 0,
            'expected_total_with_vat': 0,
        }
        for row in rows:
            if any(str(row.get(field) or '').strip() for field in field_counts) or str(row.get('expected_should_finalize') or '').strip():
                labeled += 1
            if str(row.get('expected_should_finalize') or '').strip():
                finalize += 1
            for field in field_counts:
                if str(row.get(field) or '').strip():
                    field_counts[field] += 1
        return {
            'rows': len(rows),
            'labeled_rows': labeled,
            'expected_finalize_rows': finalize,
            **field_counts,
        }

    def _apply_batch_row(self, target: dict[str, Any], batch_row: dict[str, str]) -> None:
        target.setdefault('doc_type', '')
        target.setdefault('quality_band', '')
        target['expected_ico'] = str(batch_row.get('expected_ico') or '').strip()
        target['expected_document_number'] = str(batch_row.get('expected_document_number') or '').strip()
        target['expected_issued_at'] = str(batch_row.get('expected_issued_at') or '').strip()
        target['expected_total_with_vat'] = str(batch_row.get('expected_total_with_vat') or '').strip()
        target.setdefault('expected_vat_summary', '')
        target.setdefault('expected_items_count', '')
        target['expected_should_finalize'] = str(batch_row.get('expected_should_finalize') or '').strip()
        target.setdefault('observed_category', '')
        target['observed_file_type'] = str(batch_row.get('file_type') or target.get('observed_file_type') or '').strip()
        target['observed_readable_text'] = str(target.get('observed_readable_text') or '')

    def _load_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))

    def _write_master_csv(self, destination: Path, rows: list[dict[str, Any]]) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=self.MASTER_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, '') for field in self.MASTER_FIELDS})


class OcrAiPrefillService:
    def prefill_batch(self, batch_path: Path, output_path: Path) -> Path:
        rows = self._load_csv(batch_path)
        prefilled: list[dict[str, Any]] = []
        for row in rows:
            updated = dict(row)
            if not str(updated.get('expected_ico') or '').strip():
                updated['expected_ico'] = str(updated.get('predicted_ico') or '').strip()
            if not str(updated.get('expected_document_number') or '').strip():
                updated['expected_document_number'] = str(updated.get('predicted_document_number') or '').strip()
            if not str(updated.get('expected_issued_at') or '').strip():
                updated['expected_issued_at'] = str(updated.get('predicted_issued_at') or '').strip()
            if not str(updated.get('expected_total_with_vat') or '').strip():
                updated['expected_total_with_vat'] = str(updated.get('predicted_total_with_vat') or '').strip()
            if not str(updated.get('expected_should_finalize') or '').strip():
                updated['expected_should_finalize'] = str(updated.get('predicted_should_finalize') or '').strip()
            updated['label_status'] = 'ai_prefilled_unverified'
            note_parts = ['AI prefill z predicted_*']
            review_reasons = str(updated.get('review_reasons') or '')
            if review_reasons:
                note_parts.append(f'duvody: {review_reasons}')
            existing_note = str(updated.get('review_note') or '').strip()
            if existing_note:
                note_parts.append(existing_note)
            updated['review_note'] = ' | '.join(note_parts)
            prefilled.append(updated)
        self._write_csv(output_path, prefilled)
        return output_path

    def summarize_prefill(self, batch_path: Path) -> dict[str, int]:
        rows = self._load_csv(batch_path)
        return {
            'rows': len(rows),
            'prefilled_ico': sum(1 for row in rows if str(row.get('expected_ico') or '').strip()),
            'prefilled_document_number': sum(1 for row in rows if str(row.get('expected_document_number') or '').strip()),
            'prefilled_issued_at': sum(1 for row in rows if str(row.get('expected_issued_at') or '').strip()),
            'prefilled_total': sum(1 for row in rows if str(row.get('expected_total_with_vat') or '').strip()),
            'prefilled_finalize': sum(1 for row in rows if str(row.get('expected_should_finalize') or '').strip()),
        }

    def _load_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))

    def _write_csv(self, destination: Path, rows: list[dict[str, Any]]) -> None:
        fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ['empty']
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, '') for key in fieldnames})
