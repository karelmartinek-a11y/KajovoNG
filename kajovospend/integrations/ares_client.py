from __future__ import annotations

from dataclasses import dataclass

import requests


class AresError(RuntimeError):
    pass


@dataclass(slots=True)
class AresSupplier:
    ico: str
    name: str
    dic: str
    vat_payer: bool
    address: str
    raw_payload: dict


class AresClient:
    def __init__(self, base_url: str = 'https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty', timeout: int = 20) -> None:
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def get_supplier(self, ico: str) -> AresSupplier:
        normalized = ''.join(ch for ch in ico if ch.isdigit())
        if len(normalized) != 8:
            raise AresError('IČO musí mít 8 číslic.')
        response = requests.get(f'{self.base_url}/{normalized}', timeout=self.timeout)
        if response.status_code == 404:
            raise AresError('Dodavatel v ARES neexistuje.')
        if response.status_code >= 400:
            raise AresError(f'ARES vrátil chybu HTTP {response.status_code}.')
        payload = response.json()
        sidlo = payload.get('sidlo') or {}
        registrace = payload.get('seznamRegistraci') or {}
        dic = ''
        if payload.get('dic'):
            dic = str(payload.get('dic'))
        elif normalized:
            dic = f'CZ{normalized}' if registrace.get('stavZdrojeDph') == 'AKTIVNI' else ''
        return AresSupplier(
            ico=normalized,
            name=str(payload.get('obchodniJmeno') or ''),
            dic=dic,
            vat_payer=registrace.get('stavZdrojeDph') == 'AKTIVNI',
            address=str(sidlo.get('nazevUlice') or sidlo.get('textovaAdresa') or ''),
            raw_payload=payload,
        )
