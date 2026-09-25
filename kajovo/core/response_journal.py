"""Trvalá evidence pracovních odpovědí a obnovitelné sledování generace."""

import copy
import json
import time
from pathlib import Path

from .contracts import ContractError, parse_json_strict
from .delivery_preparation import digest
from .openai_client import OpenAIError


class ResponsePending(RuntimeError):
    """Sledování skončilo, vzdálená odpověď zůstává dohledatelná."""


class SubmissionUnknown(RuntimeError):
    """Odeslání nemá potvrzené ID a nesmí být automaticky opakováno."""


class ResponseCancelled(RuntimeError):
    """API potvrdilo zrušení vzdálené generace."""


def _replay_identity(payload):
    """Pouze pořadí klíčů JSON podkladů není změnou pracovní fáze."""
    body = copy.deepcopy(payload)
    messages = body.get("input")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for part in message["content"]:
                if isinstance(part, dict) and part.get("type") == "input_text" and isinstance(part.get("text"), str):
                    try:
                        value = parse_json_strict(part["text"])
                    except (ContractError, ValueError, TypeError):
                        continue
                    part["text"] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return digest(body)


class ResponseJournal:
    def __init__(self, logger, timeout_s=3600, *, clock=time.monotonic, sleep=time.sleep):
        self.log = logger
        self.timeout_s = timeout_s
        self.clock, self.sleep = clock, sleep
        self.entries = {}
        self.continuation = None
        self.replay_required = None
        continuation_path = logger.find_json("manifests", "live_continuation")
        if continuation_path:
            from .runs.live_continuation import validate_live_continuation
            self.continuation = validate_live_continuation(
                parse_json_strict(Path(continuation_path).read_text(encoding="utf-8")), target_run_id=logger.run_id,
            )
            self.replay_required = self.continuation["pending_hash"]
            replay_path = logger.find_json("manifests", "live_replay_complete")
            if replay_path:
                replay = parse_json_strict(Path(replay_path).read_text(encoding="utf-8"))
                if replay.get("hash") == self.replay_required:
                    self.replay_required = None
        path = logger.find_json("manifests", "response_journal")
        if path:
            data = parse_json_strict(Path(path).read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("entries"), dict):
                raise ValueError("Neplatná evidence odpovědí; nelze bezpečně pokračovat.")
            self.entries = data["entries"]
            for key, entry in self.entries.items():
                if not isinstance(entry, dict) or digest(entry.get("payload")) != key:
                    raise ValueError(
                        "Evidence požadavku má neplatný hash; automatické pokračování je zablokováno."
                    )
        if self.continuation:
            original = self.continuation["journal"]["entries"]
            for key, item in original.items():
                current = self.entries.get(key) or {}
                if current.get("payload") != item.get("payload") or current.get("id") != item.get("id"):
                    raise ValueError("Continue LIVE: ztracený nebo změněný zděděný journal.")

    def frozen_payload(self, payload):
        """Vybere původní bajtově přesný payload; nikdy nepřekládá nový submit."""
        if not self.continuation:
            return payload
        body = copy.deepcopy(payload)
        body.update(background=True, store=True)
        inherited = self.continuation["journal"]["entries"]
        if digest(body) in inherited:
            return copy.deepcopy(inherited[digest(body)]["payload"])
        identity = _replay_identity(body)
        matches = [entry["payload"] for entry in inherited.values()
                   if _replay_identity(entry["payload"]) == identity]
        if len(matches) > 1:
            raise SubmissionUnknown("Continue LIVE: nejednoznačná identita zmrazené pracovní fáze.")
        return copy.deepcopy(matches[0]) if matches else payload

    def inherited_order(self, payload):
        if not self.continuation:
            return None
        body = copy.deepcopy(self.frozen_payload(payload))
        body.update(background=True, store=True)
        key = digest(body)
        raw = self.continuation["work_orders"].get(key)
        if raw is None and self.replay_required:
            raise SubmissionUnknown(
                "Continue LIVE: obnovená fáze neodpovídá zmrazenému požadavku; nový POST je zakázán."
            )
        if raw is not None:
            from .orchestration.work_order import work_order_from_mapping
            return work_order_from_mapping(raw)
        return None

    def frozen_preparation_payload(self, payload):
        """Obnova přípravy čte původní masku pouze při jinak totožném požadavku."""
        body = copy.deepcopy(payload)
        body.update(background=True, store=True)
        name = ((body.get("text") or {}).get("format") or {}).get("name")
        matches = []
        for entry in self.entries.values():
            original = entry.get("payload") or {}
            original_format = ((original.get("text") or {}).get("format") or {})
            if not name or original_format.get("name") != name:
                continue
            candidate = copy.deepcopy(body)
            candidate["text"] = copy.deepcopy(original["text"])
            if _replay_identity(candidate) == _replay_identity(original):
                matches.append(original)
        if len(matches) > 1:
            raise SubmissionUnknown("Obnova přípravy: nejednoznačný původní požadavek.")
        return copy.deepcopy(matches[0]) if matches else payload

    def has_entry(self, payload) -> bool:
        """Return whether this exact background payload already has journal state."""
        body = copy.deepcopy(payload)
        body.update(background=True, store=True)
        return digest(body) in self.entries

    def confirmed_id(self, payload) -> str:
        """Return provider ID only when this exact background payload has one."""
        body = copy.deepcopy(payload)
        body.update(background=True, store=True)
        entry = self.entries.get(digest(body))
        if not isinstance(entry, dict):
            return ""
        return str(entry.get("id") or "")

    def save(self):
        # Čas pollingu a identita GET patří provozní evidenci; nesmějí vytvářet
        # další kopii všech pracovních payloadů při každé kontrole stavu.
        entries = copy.deepcopy(self.entries)
        for entry in entries.values():
            entry.pop("checked_at", None)
            if isinstance(entry.get("response"), dict):
                entry["response"].pop("_request_id", None)
        self.log.save_json(
            "manifests",
            "response_journal",
            {"version": 1, "entries": entries},
        )

    def execute(self, client, payload, *, stopped, cancelled, progress):
        body = copy.deepcopy(payload)
        body.update(background=True, store=True)
        key = digest(body)
        entry = self.entries.get(key)
        reused = entry is not None
        if entry is None:
            if self.replay_required:
                raise SubmissionUnknown("Continue LIVE: původní odpověď nebyla převzata; nový POST je zakázán.")
            if stopped() or cancelled():
                raise RuntimeError("STOP_REQUESTED")
            if any(
                item.get("status") in ("submitting", "queued", "in_progress")
                for item in self.entries.values()
            ):
                raise SubmissionUnknown(
                    "Rozpracovaný požadavek se liší od obnoveného zadání. Nové generování nebylo odesláno."
                )
            entry = {"payload": body, "status": "submitting", "created_at": time.time()}
            self.entries[key] = entry
            self.save()
            # Kanonická request evidence vzniká před samotným placeným odesláním.
            # Obsah se ukládá beze změny; Run Bundle k němu přidá hash a provenance.
            self.log.save_json(
                "requests",
                "background_response_" + key,
                {"payload": body, "request_role": "background_work"},
            )
            self.log.update_state({"response_pending": {"hash": key, **entry}})
            self.log.event(
                "response.submit",
                {"hash": key, "model": body.get("model")},
            )
            try:
                response = client.create_response(body)
            except Exception as exc:
                if (
                    getattr(exc, "request_sent", None) is False
                    or getattr(exc, "status_code", None) in (400, 401, 403, 404, 422, 429)
                ):
                    entry["status"] = "rejected"
                    self.save()
                    self.log.clear_state_keys("response_pending")
                    raise
                raise SubmissionUnknown(
                    "Výsledek odeslání není známý; API nepotvrdilo ID odpovědi. Požadavek se automaticky neopakuje."
                ) from exc
            if not isinstance(response, dict) or not response.get("id"):
                raise SubmissionUnknown(
                    "API nepotvrdilo ID odpovědi; výsledek odeslání není známý."
                )
            entry["id"] = response["id"]
            self._record(key, entry, response)
        elif entry.get("status") == "submitting":
            raise SubmissionUnknown(
                "Předchozí odeslání nemá potvrzené ID. Automatické opakování je zablokováno."
            )
        elif entry.get("status") == "rejected":
            raise RuntimeError(
                "API tento požadavek odmítlo. Opravte zadání nebo nastavení a spusťte nový běh."
            )
        from .runs.live_continuation import response_claim
        with response_claim(Path(self.log.paths.run_dir).parent, entry["id"], self.log.run_id,
                            require_owner=bool(self.continuation and key == self.continuation["pending_hash"])):
            return self._poll(client, key, entry, reused=reused, stopped=stopped,
                              cancelled=cancelled, progress=progress)

    def _poll(self, client, key, entry, *, reused, stopped, cancelled, progress):
        response = entry.get("response", {})
        if reused and entry["status"] == "completed":
            self.log.event(
                "response.reuse",
                {"hash": key, "response_id": entry["id"]},
            )
        start = self.clock()
        failures = 0
        cancel_sent = entry.get("cancel_requested", False)
        while entry["status"] in ("queued", "in_progress"):
            if stopped():
                raise ResponsePending(
                    "Sledování zastaveno. Vzdálená generace může pokračovat; použijte Continue."
                )
            if self.clock() - start >= self.timeout_s:
                raise ResponsePending(
                    "Vypršel limit sledování generace. Odpověď zůstává uložená pod svým ID; pokračujte přes Continue."
                )
            progress(
                "cancelling" if cancel_sent else entry["status"],
                int(self.clock() - start),
            )
            try:
                if cancelled() and not cancel_sent:
                    response = client.cancel_response(entry["id"])
                    cancel_sent = True
                    entry["cancel_requested"] = True
                else:
                    self._wait(
                        2,
                        stopped,
                        lambda sent=cancel_sent: cancelled() and not sent,
                    )
                    if stopped() or cancelled() and not cancel_sent:
                        continue
                    response = client.retrieve_response(entry["id"])
                if response.get("id") != entry["id"]:
                    raise OpenAIError("API vrátilo jiné ID odpovědi.")
                self._record(key, entry, response)
                if failures:
                    self.log.event("response.poll_recovered", {
                        "response_id": entry["id"], "failed_attempts": failures,
                        "message": "Spojení obnoveno; pokračuje sledování původní odpovědi.",
                    })
                failures = 0
            except OpenAIError as exc:
                failures += 1
                progress("connection_error", int(self.clock() - start))
                code = getattr(exc, "status_code", None)
                self.log.event(
                    "response.poll_error",
                    {
                        "response_id": entry["id"],
                        "status_code": code,
                        "code": getattr(exc, "code", None),
                        "message": str(exc),
                        "request_id": getattr(exc, "request_id", None),
                        "attempt": failures,
                        "error_type": type(exc.__cause__ or exc).__name__,
                    },
                )
                if (
                    failures >= 4
                    or code is not None
                    and code != 429
                    and not 500 <= code < 600
                ):
                    raise ResponsePending(
                        f"Stav odpovědi nelze ověřit: {exc}. ID je zachováno; nové generování se neodeslalo."
                    ) from exc
                self._wait(
                    min(8, 0.8 * 2 ** (failures - 1)),
                    stopped,
                    lambda: False,
                )
        if self.continuation and entry["status"] == "completed":
            self.log.update_state({"live_replay_pending": {"hash": key, "id": entry["id"]},
                                   "live_replay_complete": False})
        self.log.clear_state_keys("response_pending")
        if entry["status"] == "cancelled":
            raise ResponseCancelled("Vzdálená generace byla zrušena (cancelled).")
        if entry["status"] != "completed":
            from .contracts import RemoteResponseError
            raise RemoteResponseError(response, request_id=entry.get("last_request_id") or "")
        client._known_responses.add(entry["id"])
        return copy.deepcopy(response)

    def _record(self, key, entry, response):
        # GET request ID není změna pracovního výsledku. Opakovaná stejná
        # odpověď nesmí při pollingu přepisovat celý deník, stav a jeho přílohy.
        previous = dict(entry.get("response") or {})
        current = dict(response)
        previous.pop("_request_id", None)
        current.pop("_request_id", None)
        if previous == current:
            entry["checked_at"] = time.time()
            return

        if response.get("status") not in ("queued", "in_progress"):
            self.log.save_json(
                "responses",
                "provider_" + entry["id"],
                response,
            )
        old_status = entry.get("status")
        entry.update(
            status=response.get("status", "unknown"),
            response=response,
            last_request_id=response.get("_request_id") or entry.get("last_request_id") or "",
            checked_at=time.time(),
        )
        self.save()
        self.log.update_state(
            {
                "response_pending": {
                    "hash": key,
                    "id": entry["id"],
                    "status": entry["status"],
                    "created_at": entry["created_at"],
                    "checked_at": entry["checked_at"],
                }
            }
        )
        if old_status != entry["status"]:
            self.log.event(
                "response.status",
                {
                    "response_id": entry["id"],
                    "status": entry["status"],
                    "request_id": response.get("_request_id"),
                },
            )

    def _wait(self, seconds, stopped, cancelled):
        end = self.clock() + seconds
        while self.clock() < end and not stopped() and not cancelled():
            self.sleep(min(0.1, max(0, end - self.clock())))
