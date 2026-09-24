"""Společné rozlišení úspěchu, provider chyby a vadné obálky BATCH řádku."""
from typing import Any


def response_body(row: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    response = row.get("response")
    if row.get("error"):
        return {}, row["error"]
    if not isinstance(response, dict):
        return {}, {"code": "invalid_response", "message": "BATCH řádek nemá response objekt."}
    status = response.get("status_code")
    body = response.get("body")
    if type(status) is not int or not 100 <= status <= 599 or not isinstance(body, dict):
        return {}, {"code": "invalid_response", "message": "Neplatný BATCH status_code nebo body."}
    if not 200 <= status < 300:
        return body, body.get("error") or {"code": "http_error", "status_code": status}
    return body, None
