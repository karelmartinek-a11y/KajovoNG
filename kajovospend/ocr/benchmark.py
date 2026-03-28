from __future__ import annotations

import csv
import json
import statistics
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kajovospend.ocr.document_loader import DocumentLoader, LoadedDocument


SUPPORTED_SUFFIXES = {
    '.pdf',
    '.txt',
    '.png',
    '.jpg',
    '.jpeg',
    '.bmp',
    '.tif',
    '.tiff',
}


@dataclass(slots=True)
class BenchmarkSample:
    relative_path: str
    suffix: str
    size_bytes: int
    file_type: str
    category: str
    page_count: int
    readable_text: bool
    text_length: int
    pages_with_text: int
    pages_without_text: int
    text_layer_pages: int
    duration_ms: float
    warnings: list[str]
    source_kinds: list[str]
    ocr_statuses: list[str]


class OcrBenchmarkRunner:
    def __init__(self, loader: DocumentLoader | None = None) -> None:
        self.loader = loader or DocumentLoader()

    def run_corpus(self, corpus_dir: Path, *, max_files: int | None = None) -> dict[str, Any]:
        files = self._collect_files(corpus_dir)
        if max_files is not None:
            files = files[: max(0, int(max_files))]
        samples = [self._run_file(corpus_dir, path) for path in files]
        return self._build_report(corpus_dir, samples)

    def run_zip(self, zip_path: Path, *, max_files: int | None = None) -> dict[str, Any]:
        with TemporaryDirectory(prefix='kajovospend-ocr-benchmark-') as temp_dir:
            extracted = Path(temp_dir) / 'corpus'
            self._extract_zip(zip_path, extracted)
            report = self.run_corpus(extracted, max_files=max_files)
        report['zip_path'] = str(zip_path)
        return report

    def write_report(self, report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / 'summary.json'
        samples_path = output_dir / 'samples.csv'
        labels_path = output_dir / 'labels_template.csv'

        summary_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        self._write_csv(samples_path, report.get('samples', []))
        self._write_csv(labels_path, report.get('label_template', []))
        return {
            'summary': summary_path,
            'samples': samples_path,
            'labels': labels_path,
        }

    def _collect_files(self, corpus_dir: Path) -> list[Path]:
        return sorted(
            [
                path
                for path in corpus_dir.rglob('*')
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
            ],
            key=lambda path: path.as_posix().lower(),
        )

    def _run_file(self, corpus_dir: Path, source_path: Path) -> BenchmarkSample:
        started = time.perf_counter()
        loaded = self.loader.load(source_path)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        relative_path = source_path.relative_to(corpus_dir).as_posix()
        warnings = [str(warning) for warning in loaded.warnings]
        page_texts = [page.text.strip() for page in loaded.pages]
        source_kinds = self._unique_preserve([str(page.source_kind or '') for page in loaded.pages if str(page.source_kind or '').strip()])
        ocr_statuses = self._unique_preserve([str(page.ocr_status or '') for page in loaded.pages if str(page.ocr_status or '').strip()])
        pages_with_text = sum(1 for value in page_texts if value)
        text_layer_pages = sum(1 for page in loaded.pages if page.text_layer_present)
        return BenchmarkSample(
            relative_path=relative_path,
            suffix=source_path.suffix.lower(),
            size_bytes=source_path.stat().st_size,
            file_type=loaded.file_type,
            category=self._categorize_document(loaded),
            page_count=loaded.page_count,
            readable_text=loaded.has_readable_text,
            text_length=len(loaded.merged_text),
            pages_with_text=pages_with_text,
            pages_without_text=max(0, loaded.page_count - pages_with_text),
            text_layer_pages=text_layer_pages,
            duration_ms=duration_ms,
            warnings=warnings,
            source_kinds=source_kinds,
            ocr_statuses=ocr_statuses,
        )

    def _categorize_document(self, loaded: LoadedDocument) -> str:
        if loaded.file_type == 'pdf':
            text_pages = sum(1 for page in loaded.pages if page.text_layer_present)
            if text_pages == 0:
                return 'pdf_scan'
            if text_pages == loaded.page_count:
                return 'pdf_text_layer'
            return 'pdf_mixed'
        if loaded.file_type == 'image':
            return 'image_ocr'
        if loaded.file_type == 'text':
            return 'text'
        return loaded.file_type or 'unknown'

    def _build_report(self, corpus_dir: Path, samples: list[BenchmarkSample]) -> dict[str, Any]:
        serialized_samples = [asdict(sample) for sample in samples]
        durations = [sample.duration_ms for sample in samples]
        category_counts = self._count_by(samples, 'category')
        file_type_counts = self._count_by(samples, 'file_type')
        warning_counts = self._count_warnings(samples)
        metrics = {
            'corpus_path': str(corpus_dir),
            'sample_count': len(samples),
            'total_pages': sum(sample.page_count for sample in samples),
            'readable_documents': sum(1 for sample in samples if sample.readable_text),
            'unreadable_documents': sum(1 for sample in samples if not sample.readable_text),
            'pdf_scan_documents': category_counts.get('pdf_scan', 0),
            'pdf_text_layer_documents': category_counts.get('pdf_text_layer', 0),
            'pdf_mixed_documents': category_counts.get('pdf_mixed', 0),
            'avg_duration_ms': round(statistics.fmean(durations), 2) if durations else 0.0,
            'p95_duration_ms': self._percentile(durations, 0.95),
            'max_duration_ms': round(max(durations), 2) if durations else 0.0,
        }
        return {
            'metrics': metrics,
            'by_category': category_counts,
            'by_file_type': file_type_counts,
            'warnings': warning_counts,
            'samples': serialized_samples,
            'label_template': self._build_label_template(samples),
        }

    def _build_label_template(self, samples: list[BenchmarkSample]) -> list[dict[str, Any]]:
        template: list[dict[str, Any]] = []
        for sample in samples:
            template.append(
                {
                    'relative_path': sample.relative_path,
                    'doc_type': '',
                    'quality_band': '',
                    'expected_ico': '',
                    'expected_document_number': '',
                    'expected_issued_at': '',
                    'expected_total_with_vat': '',
                    'expected_vat_summary': '',
                    'expected_items_count': '',
                    'expected_should_finalize': '',
                    'observed_category': sample.category,
                    'observed_file_type': sample.file_type,
                    'observed_readable_text': int(sample.readable_text),
                }
            )
        return template

    def _count_by(self, samples: list[BenchmarkSample], attribute: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sample in samples:
            key = str(getattr(sample, attribute) or 'unknown')
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def _count_warnings(self, samples: list[BenchmarkSample]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sample in samples:
            for warning in sample.warnings:
                counts[warning] = counts.get(warning, 0) + 1
        return dict(sorted(counts.items()))

    def _extract_zip(self, zip_path: Path, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(output_dir)

    def _percentile(self, values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio))))
        return round(float(ordered[index]), 2)

    def _unique_preserve(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

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
