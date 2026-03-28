from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests


class OpenAIClientError(RuntimeError):
    pass


@dataclass(slots=True)
class OpenAIAuditRecord:
    endpoint: str
    method: str
    model: str = ''
    request_fingerprint: str = ''
    duration_ms: int = 0
    status_code: int = 0
    response_kind: str = ''
    validation_status: str = 'unknown'
    error_kind: str = ''
    response_id: str = ''


@dataclass(slots=True)
class OpenAIExtractionResult:
    supplier_ico: str = ''
    supplier_name: str = ''
    supplier_dic: str = ''
    document_number: str = ''
    total_with_vat: float = 0.0
    issued_at: str = ''
    vat_rate: str = ''
    items: list[dict[str, Any]] | None = None
    valid: bool = False
    raw_text: str = ''
    audit: OpenAIAuditRecord | None = None
    validation_errors: list[str] = field(default_factory=list)


class OpenAIClient:
    BASE_URL = 'https://api.openai.com/v1'
    MODELS_URL = f'{BASE_URL}/models'
    RESPONSES_URL = f'{BASE_URL}/responses'

    def __init__(self, timeout: int = 30, session: requests.sessions.Session | None = None, audit_hook: Callable[[OpenAIAuditRecord], None] | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.audit_hook = audit_hook

    def list_models(self, api_key: str) -> list[str]:
        audit = OpenAIAuditRecord(endpoint=self.MODELS_URL, method='GET')
        started = time.monotonic()
        try:
            response = self.session.get(self.MODELS_URL, headers=self._headers(api_key), timeout=self.timeout)
        except requests.RequestException as exc:
            audit.error_kind = 'network_error'
            audit.duration_ms = self._duration_ms(started)
            self._emit_audit(audit)
            raise OpenAIClientError(str(exc)) from exc
        audit.status_code = int(response.status_code)
        audit.duration_ms = self._duration_ms(started)
        audit.response_kind = 'models'
        if response.status_code >= 400:
            audit.error_kind = 'http_error'
            self._emit_audit(audit)
            raise OpenAIClientError(self._extract_error(response))
        payload = response.json()
        audit.validation_status = 'verified'
        self._emit_audit(audit)
        return sorted({item.get('id', '') for item in payload.get('data', []) if item.get('id')})

    def extract_document(
        self,
        api_key: str,
        model: str,
        file_name: str,
        text_hint: str,
        *,
        image_inputs: list[dict[str, Any]] | None = None,
        source_descriptor: dict[str, Any] | None = None,
    ) -> OpenAIExtractionResult:
        developer_prompt = (
            'Extrahuj data z účetního dokladu nebo účtenky. Vrať pouze jeden JSON objekt bez komentářů a bez markdownu se schématem '
            '{"supplier_ico":"","supplier_name":"","supplier_dic":"","document_number":"",'
            '"total_with_vat":0,"issued_at":"","vat_rate":"","items":[{"name":"","quantity":0,"unit_price":0,"total_price":0,"vat_rate":""}]}. '
            'Pole supplier_* patří výhradně dodavateli nebo prodejci, nikdy odběrateli, příjemci ani bill-to kontaktu. '
            'supplier_ico vrať jako 8 číslic bez mezer, issued_at jako YYYY-MM-DD, total_with_vat jako číslo bez měny a vat_rate jen jako sazbu DPH typu 0, 10, 12, 15 nebo 21. '
            'Pokud údaj není jistý, vrať prázdnou hodnotu nebo 0. Pokud jsou na dokladu položky, vyplň items a každá položka musí mít total_price včetně DPH.'
        )
        source_payload = self._build_source_payload(
            file_name=file_name,
            text_hint=text_hint,
            source_descriptor=source_descriptor or {},
            image_inputs=image_inputs or [],
        )
        content: list[dict[str, Any]] = [{'type': 'input_text', 'text': source_payload}]
        for image_input in image_inputs or []:
            image_url = str(image_input.get('image_url') or '').strip()
            if not image_url:
                continue
            content.append(
                {
                    'type': 'input_image',
                    'image_url': image_url,
                    'detail': str(image_input.get('detail') or 'high'),
                }
            )
        body: dict[str, Any] = {
            'model': model,
            'input': [
                {'role': 'developer', 'content': [{'type': 'input_text', 'text': developer_prompt}]},
                {'role': 'user', 'content': content},
            ],
            'text': {'format': {'type': 'json_object'}},
        }
        audit = OpenAIAuditRecord(
            endpoint=self.RESPONSES_URL,
            method='POST',
            model=model,
            request_fingerprint=self._request_fingerprint(
                model=model,
                file_name=file_name,
                text_hint=text_hint,
                image_inputs=image_inputs or [],
                source_descriptor=source_descriptor or {},
            ),
        )
        started = time.monotonic()
        try:
            response = self.session.post(
                self.RESPONSES_URL,
                headers=self._headers(api_key),
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            audit.error_kind = 'network_error'
            audit.duration_ms = self._duration_ms(started)
            self._emit_audit(audit)
            raise OpenAIClientError(str(exc)) from exc
        audit.status_code = int(response.status_code)
        audit.duration_ms = self._duration_ms(started)
        audit.response_kind = 'responses_json'
        if response.status_code >= 400:
            audit.error_kind = 'http_error'
            self._emit_audit(audit)
            raise OpenAIClientError(self._extract_error(response))
        payload = response.json()
        audit.response_id = str(payload.get('id') or '')
        output_text = self._extract_output_text(payload)
        output_text = self._strip_code_fences(output_text)
        try:
            data = json.loads(output_text)
        except json.JSONDecodeError as exc:
            data = self._extract_embedded_json(output_text)
            if data is None:
                audit.validation_status = 'failed'
                audit.error_kind = 'json_validation_error'
                self._emit_audit(audit)
                raise OpenAIClientError('OpenAI nevrátil validní JSON výstup.') from exc
        result = OpenAIExtractionResult(
            supplier_ico=self._digits_only(data.get('supplier_ico', ''))[:8],
            supplier_name=str(data.get('supplier_name', '') or '').strip(),
            supplier_dic=str(data.get('supplier_dic', '') or '').strip(),
            document_number=str(data.get('document_number', '') or '').strip(),
            total_with_vat=self._coerce_amount(data.get('total_with_vat', 0)),
            issued_at=str(data.get('issued_at', '') or '').strip(),
            vat_rate=self._coerce_vat_rate(data.get('vat_rate', '')),
            items=self._coerce_items(data.get('items')),
            raw_text=output_text,
            audit=audit,
        )
        result.validation_errors = self._validate_result(result)
        result.valid = not result.validation_errors
        audit.validation_status = 'verified' if result.valid else 'failed'
        audit.error_kind = '' if result.valid else 'content_validation_error'
        self._emit_audit(audit)
        return result

    def _build_source_payload(self, *, file_name: str, text_hint: str, source_descriptor: dict[str, Any], image_inputs: list[dict[str, Any]]) -> str:
        descriptor = {
            'source_name': file_name,
            'source_sha256': str(source_descriptor.get('source_sha256') or ''),
            'source_path': str(source_descriptor.get('source_path') or ''),
            'page_count': int(source_descriptor.get('page_count') or 0),
            'selected_page_numbers': list(source_descriptor.get('selected_page_numbers') or []),
            'image_input_count': len(image_inputs),
        }
        return '\n'.join([
            'ZDROJ_SOUBORU',
            json.dumps(descriptor, ensure_ascii=False),
            'ZDROJ_TEXTU',
            str(text_hint or '')[:12000],
        ])

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        output_text = str(payload.get('output_text') or '').strip()
        if output_text:
            return output_text
        collected: list[str] = []
        for item in payload.get('output', []) or []:
            for content_item in item.get('content', []) or []:
                text_value = content_item.get('text')
                if isinstance(text_value, str) and text_value.strip():
                    collected.append(text_value)
                    continue
                if isinstance(text_value, dict):
                    candidate = str(text_value.get('value') or '').strip()
                    if candidate:
                        collected.append(candidate)
                        continue
                candidate = str(content_item.get('value') or '').strip()
                if candidate:
                    collected.append(candidate)
        return '\n'.join(collected)

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'OpenAI-Beta': 'responses=v1',
        }

    def _strip_code_fences(self, value: str) -> str:
        text = str(value or '').strip()
        if text.startswith('```'):
            text = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
        return text.strip()

    def _extract_embedded_json(self, value: str) -> dict[str, Any] | None:
        text = str(value or '').strip()
        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _digits_only(self, value: Any) -> str:
        return ''.join(ch for ch in str(value or '').strip() if ch.isdigit())

    def _coerce_amount(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or '').strip().replace('\xa0', ' ')
        text = re.sub(r'[^0-9,.\-]', '', text)
        if not text:
            return 0.0
        if ',' in text and '.' in text:
            if text.rfind(',') > text.rfind('.'):
                text = text.replace('.', '').replace(',', '.')
            else:
                text = text.replace(',', '')
        else:
            text = text.replace(',', '.')
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _coerce_vat_rate(self, value: Any) -> str:
        text = str(value or '').strip().replace('%', '').replace(',', '.')
        if not text:
            return ''
        try:
            parsed = float(text)
        except ValueError:
            return ''
        return str(int(parsed)) if parsed.is_integer() else str(parsed)

    def _coerce_items(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for raw_item in value:
            if not isinstance(raw_item, dict):
                continue
            name = str(raw_item.get('name', '') or '').strip()
            quantity = self._coerce_amount(raw_item.get('quantity', 0))
            unit_price = self._coerce_amount(raw_item.get('unit_price', 0))
            total_price = self._coerce_amount(raw_item.get('total_price', 0))
            vat_rate = self._coerce_vat_rate(raw_item.get('vat_rate', ''))
            if not name or total_price <= 0:
                continue
            if quantity <= 0:
                quantity = 1.0
            if unit_price <= 0:
                unit_price = total_price
            items.append(
                {
                    'name': name,
                    'quantity': quantity,
                    'unit_price': unit_price,
                    'total_price': total_price,
                    'vat_rate': vat_rate,
                }
            )
        return items

    def _validate_result(self, result: OpenAIExtractionResult) -> list[str]:
        errors: list[str] = []
        if len(result.supplier_ico) != 8:
            errors.append('supplier_ico')
        if not result.supplier_name.strip():
            errors.append('supplier_name')
        if not result.document_number:
            errors.append('document_number')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', result.issued_at or ''):
            errors.append('issued_at')
        if float(result.total_with_vat or 0) <= 0:
            errors.append('total_with_vat')
        if result.items:
            total = round(sum(float(item.get('total_price') or 0) for item in result.items), 2)
            expected = round(float(result.total_with_vat or 0), 2)
            if total != expected:
                errors.append('items_total_mismatch')
        return errors

    def _extract_error(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except Exception:
            return f'HTTP {response.status_code}'
        error = payload.get('error', {})
        message = error.get('message') or payload.get('message') or f'HTTP {response.status_code}'
        return str(message)

    def _request_fingerprint(self, *, model: str, file_name: str, text_hint: str, image_inputs: list[dict[str, Any]], source_descriptor: dict[str, Any]) -> str:
        joined = json.dumps(
            {
                'endpoint': self.RESPONSES_URL,
                'model': model,
                'file_name': file_name,
                'text_hint_preview': str(text_hint or '')[:512],
                'image_pages': [str(item.get('page_no') or '') for item in image_inputs],
                'image_count': len(image_inputs),
                'source_descriptor': source_descriptor,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(joined.encode('utf-8')).hexdigest()[:24]

    def _duration_ms(self, started: float) -> int:
        return int(round((time.monotonic() - started) * 1000))

    def _emit_audit(self, audit: OpenAIAuditRecord) -> None:
        if self.audit_hook is not None:
            self.audit_hook(audit)
