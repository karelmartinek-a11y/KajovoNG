from __future__ import annotations

import csv
import json
import statistics
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kajovospend.ocr.benchmark import SUPPORTED_SUFFIXES
from kajovospend.ocr.document_loader import DocumentLoader
from kajovospend.processing.service import ProcessingService


KEY_FIELDS = ('ico', 'document_number', 'issued_at', 'total_with_vat')


class OcrEvaluationRunner:
    def __init__(
        self,
        loader: DocumentLoader | None = None,
        processing_service: ProcessingService | None = None,
    ) -> None:
        self.loader = loader or DocumentLoader()
        self.processing_service = processing_service or ProcessingService(document_loader=self.loader)

    def run_corpus(
        self,
        corpus_dir: Path,
        *,
        labels_path: Path | None = None,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        labels = self._load_labels(labels_path)
        files = self._collect_files(corpus_dir, labels)
        if max_files is not None:
            files = files[: max(0, int(max_files))]
        rows = [self._evaluate_file(corpus_dir, file_path, labels.get(file_path.relative_to(corpus_dir).as_posix(), {})) for file_path in files]
        return self._build_report(corpus_dir, rows, labels_path)

    def run_zip(
        self,
        zip_path: Path,
        *,
        labels_path: Path | None = None,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        with TemporaryDirectory(prefix='kajovospend-ocr-eval-') as temp_dir:
            extracted = Path(temp_dir) / 'corpus'
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(extracted)
            report = self.run_corpus(extracted, labels_path=labels_path, max_files=max_files)
        report['zip_path'] = str(zip_path)
        return report

    def write_report(self, report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / 'summary.json'
        predictions_path = output_dir / 'predictions.csv'
        review_pack_path = output_dir / 'review_pack.csv'
        summary_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        self._write_csv(predictions_path, report.get('predictions', []))
        self._write_csv(review_pack_path, report.get('review_pack', []))
        return {'summary': summary_path, 'predictions': predictions_path, 'review_pack': review_pack_path}

    def _collect_files(self, corpus_dir: Path, labels: dict[str, dict[str, str]]) -> list[Path]:
        if labels:
            files = [corpus_dir / relative_path for relative_path in labels.keys()]
            return [path for path in files if path.is_file()]
        return sorted(
            [path for path in corpus_dir.rglob('*') if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES],
            key=lambda path: path.as_posix().lower(),
        )

    def _load_labels(self, labels_path: Path | None) -> dict[str, dict[str, str]]:
        if labels_path is None or not labels_path.exists():
            return {}
        with labels_path.open('r', encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            rows: dict[str, dict[str, str]] = {}
            for row in reader:
                relative_path = str(row.get('relative_path') or '').strip().replace('\\', '/')
                if relative_path:
                    rows[relative_path] = {str(key): str(value or '').strip() for key, value in row.items()}
            return rows

    def _evaluate_file(self, corpus_dir: Path, file_path: Path, label_row: dict[str, str]) -> dict[str, Any]:
        loaded = self.loader.load(file_path)
        extracted = self.processing_service.extract_offline_result(loaded)
        selected = extracted['selected']
        expected_should_finalize = self._parse_bool(label_row.get('expected_should_finalize', ''))
        predicted_should_finalize = bool(extracted['complete'])
        result: dict[str, Any] = {
            'relative_path': file_path.relative_to(corpus_dir).as_posix(),
            'file_type': loaded.file_type,
            'page_count': loaded.page_count,
            'readable_text': int(loaded.has_readable_text),
            'predicted_should_finalize': int(predicted_should_finalize),
            'expected_should_finalize': '' if expected_should_finalize is None else int(expected_should_finalize),
            'incomplete_reason': str(extracted['reason'] or ''),
            'source_kinds': json.dumps(self._source_kinds(loaded), ensure_ascii=False),
            'ocr_preview': self._preview_text(loaded),
        }
        for field_name in KEY_FIELDS:
            expected = self._normalize_expected(field_name, label_row.get(f'expected_{field_name}', ''))
            predicted = self._normalize_expected(field_name, str(selected.get(field_name, '') or ''))
            result[f'expected_{field_name}'] = expected
            result[f'predicted_{field_name}'] = predicted
            result[f'match_{field_name}'] = '' if not expected else int(expected == predicted)
        result['document_exact_match'] = self._document_exact_match(result)
        result['labeled'] = int(self._is_labeled_row(label_row))
        review_priority, review_reasons = self._review_priority(result)
        result['review_priority'] = review_priority
        result['review_reasons'] = json.dumps(review_reasons, ensure_ascii=False)
        return result

    def _build_report(self, corpus_dir: Path, rows: list[dict[str, Any]], labels_path: Path | None) -> dict[str, Any]:
        labeled_rows = [row for row in rows if int(row.get('labeled') or 0)]
        review_pack = sorted(
            [row for row in rows if not int(row.get('labeled') or 0) and int(row.get('review_priority') or 0) > 0],
            key=lambda row: (-int(row.get('review_priority') or 0), str(row.get('relative_path') or '')),
        )
        metrics: dict[str, Any] = {
            'corpus_path': str(corpus_dir),
            'labels_path': str(labels_path) if labels_path else '',
            'sample_count': len(rows),
            'labeled_sample_count': len(labeled_rows),
            'readable_documents': sum(int(row.get('readable_text') or 0) for row in rows),
            'predicted_finalize_count': sum(int(row.get('predicted_should_finalize') or 0) for row in rows),
            'review_pack_count': len(review_pack),
        }
        for field_name in KEY_FIELDS:
            field_rows = [row for row in labeled_rows if str(row.get(f'expected_{field_name}') or '').strip()]
            metrics[f'{field_name}_labeled_count'] = len(field_rows)
            metrics[f'{field_name}_exact_match_rate'] = self._rate(
                [int(row.get(f'match_{field_name}') or 0) for row in field_rows]
            )
        document_rows = [row for row in labeled_rows if self._has_any_expected_field(row)]
        metrics['document_exact_match_rate'] = self._rate([int(row.get('document_exact_match') or 0) for row in document_rows])
        finalize_rows = [row for row in labeled_rows if row.get('expected_should_finalize', '') != '']
        metrics['finalize_accuracy_rate'] = self._rate(
            [
                int(int(row.get('predicted_should_finalize') or 0) == int(row.get('expected_should_finalize') or 0))
                for row in finalize_rows
            ]
        )
        metrics['avg_pages_per_document'] = round(statistics.fmean([int(row.get('page_count') or 0) for row in rows]), 2) if rows else 0.0
        return {'metrics': metrics, 'predictions': rows, 'review_pack': review_pack}

    def _normalize_expected(self, field_name: str, value: str) -> str:
        value = str(value or '').strip()
        if not value:
            return ''
        if field_name == 'issued_at':
            return self.processing_service._normalize_date(value) or value
        if field_name == 'total_with_vat':
            return self.processing_service._normalize_amount(value) or value
        return value

    def _parse_bool(self, value: str) -> bool | None:
        normalized = str(value or '').strip().lower()
        if not normalized:
            return None
        if normalized in {'1', 'true', 'yes', 'ano'}:
            return True
        if normalized in {'0', 'false', 'no', 'ne'}:
            return False
        return None

    def _document_exact_match(self, row: dict[str, Any]) -> int | str:
        if not self._has_any_expected_field(row):
            return ''
        return int(all(int(row.get(f'match_{field_name}') or 0) for field_name in KEY_FIELDS if str(row.get(f'expected_{field_name}') or '').strip()))

    def _has_any_expected_field(self, row: dict[str, Any]) -> bool:
        return any(str(row.get(f'expected_{field_name}') or '').strip() for field_name in KEY_FIELDS)

    def _is_labeled_row(self, row: dict[str, str]) -> bool:
        return any(str(row.get(key) or '').strip() for key in [*(f'expected_{field_name}' for field_name in KEY_FIELDS), 'expected_should_finalize'])

    def _rate(self, values: list[int]) -> float:
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    def _source_kinds(self, loaded) -> list[str]:
        seen: list[str] = []
        for page in loaded.pages:
            source_kind = str(page.source_kind or '').strip()
            if source_kind and source_kind not in seen:
                seen.append(source_kind)
        return seen

    def _preview_text(self, loaded) -> str:
        lines: list[str] = []
        for page in loaded.pages:
            for line in (page.text or '').splitlines():
                line = line.strip()
                if line:
                    lines.append(line)
                if len(lines) >= 6:
                    preview = ' | '.join(lines)
                    return preview[:400]
        return ''

    def _review_priority(self, row: dict[str, Any]) -> tuple[int, list[str]]:
        if int(row.get('labeled') or 0):
            return 0, []
        priority = 0
        reasons: list[str] = []
        relative_path = str(row.get('relative_path') or '').lower()
        source_kinds = str(row.get('source_kinds') or '')
        total_value = self._safe_float(str(row.get('predicted_total_with_vat') or ''))
        if not int(row.get('predicted_should_finalize') or 0):
            priority += 100
            reasons.append('not_finalizable')
        if 'pdf-raster-ocr' in source_kinds:
            priority += 35
            reasons.append('scan_pdf_ocr')
        if not str(row.get('predicted_document_number') or '').strip():
            priority += 30
            reasons.append('missing_document_number')
            if any(token in relative_path for token in ('invoice', 'faktura', 'doklad', 'fa', 'zf')):
                priority += 15
                reasons.append('filename_suggests_document_number')
        if not str(row.get('predicted_ico') or '').strip():
            priority += 20
            reasons.append('missing_ico')
        if not str(row.get('predicted_issued_at') or '').strip():
            priority += 15
            reasons.append('missing_issued_at')
        if not str(row.get('predicted_total_with_vat') or '').strip():
            priority += 15
            reasons.append('missing_total')
        if str(row.get('file_type') or '') == 'pdf-text-secondary':
            priority += 10
            reasons.append('invalid_pdf_header')
        if total_value > 50000:
            priority += 8
            reasons.append('high_total_value')
        if str(row.get('predicted_document_number') or '').strip() and len(str(row.get('predicted_document_number') or '').strip()) <= 4:
            priority += 8
            reasons.append('short_document_number')
        return priority, reasons

    def _safe_float(self, value: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

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
