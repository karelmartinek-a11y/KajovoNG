from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts import ContractError
from ..openai_client import OpenAIClient
from ..orchestration.work_order import freeze_order
from ..request_rules import uses_reasoning_defaults
from ..structured_output import (
    file_content_format,
    qfile_plan_format,
    validate_output,
)
from ..utils import safe_join_under_root, sha256_file, ts_code, validate_relative_path

if TYPE_CHECKING:
    from .context import RunContext


QFILE_PLAN_INSTRUCTIONS = (
    "Jsi pracovník jednoho přesně vymezeného kroku KájovoNG. Pracuj jen se "
    "schváleným vstupem. Navrhni pouze bezpečnou relativní cestu, formát, účel "
    "a akceptaci jednoho souboru. Nevyráběj jeho obsah. Název není povolením "
    "zapsat na disk. Neznámý formát nebo nejednoznačný cíl označ blocked."
)

QFILE_INSTRUCTIONS = (
    "Jsi pracovník jednoho přesně vymezeného kroku KájovoNG. Vytvoř úplný obsah "
    "jednoho schváleného souboru v daném formátu. Žádný limit 500 řádků, žádné "
    "chunkování a žádná cesta ve výstupu. Wire kontrakt FILE_CONTENT_V1 obsahuje "
    "výhradně pole content. Stav provedení a oprávnění řídí aplikace, ne model."
)

QFILE_FORMATS = {
    "txt", "md", "json", "toml", "yaml", "csv", "html",
    "css", "js", "ts", "py", "svg", "xml",
}


def _evidence() -> dict[str, list[Any]]:
    # Připojené soubory/obrázky se přenášejí jako skutečné Responses input parts.
    # Nevyrábíme zde falešné textové segmenty ani hashe, které jsme nevytěžili.
    return {"segments": [], "image_slots": []}


def _validate_qfile_plan(value: dict[str, Any]) -> dict[str, Any]:
    result = value.get("result")
    if not isinstance(result, dict):
        raise ContractError("QFILE_PLAN_V1: chybí result.")
    if result.get("status") == "blocked":
        questions = result.get("questions") or []
        message = "; ".join(
            str(row.get("question") or row.get("code") or "chybí podklad")
            for row in questions if isinstance(row, dict)
        )
        raise ContractError("QFILE plán je zablokovaný: " + (message or "chybí zásadní podklad."))
    if result.get("status") != "ready" or not isinstance(result.get("data"), dict):
        raise ContractError("QFILE_PLAN_V1: neplatný stav plánu.")
    plan = dict(result["data"])
    path = validate_relative_path(plan.get("proposed_path"))
    fmt = str(plan.get("format") or "")
    if fmt not in QFILE_FORMATS:
        raise ContractError("QFILE_PLAN_V1: nepodporovaný formát.")
    if Path(path).suffix.lower().lstrip(".") != fmt:
        raise ContractError("QFILE_PLAN_V1: přípona navržené cesty neodpovídá formátu.")
    plan["proposed_path"] = path
    return plan


def _run_qfile(
    self: RunContext,
    client: OpenAIClient,
    diag_file_ids: list[str],
    base_prev_id: str | None,
) -> dict[str, Any]:
    del base_prev_id  # QFILE podle CHANGE nepoužívá konverzační návaznost.
    self._check_stop()
    prompt = (self.cfg.prompt or "").strip()
    if not prompt:
        raise RuntimeError("QFILE: Zadání je prázdné.")
    if self.cfg.recovery_instruction:
        prompt += self._recovery_suffix()
    note = self._in_dir_fallback_note()
    if note:
        prompt = f"{prompt}\n\n{note}"
    qfile_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
    input_files, input_images = self._build_input_attachments(client, self._input_file_ids())
    prompt = self._append_io_reference(prompt, qfile_ref_files)
    prompt = self._with_diag_text(prompt)

    target_path = str(self.cfg.qfile_output_path or "").strip()
    if not target_path:
        if not self.cfg.qfile_suggest_path:
            raise ContractError("QFILE: chybí schválená výstupní cesta.")
        self._set(10, 0, "QFILE_PLAN: navrhuji cestu bez výroby obsahu…", stage="QFILE_PLAN")
        plan_input = {"request": prompt, "evidence": _evidence()}
        payload = self._payload_base(
            model=self.cfg.model,
            instructions=QFILE_PLAN_INSTRUCTIONS,
            input_parts=self._input_parts(
                json.dumps(plan_input, ensure_ascii=False), input_files, input_images
            ),
            prev_id=None,
        )
        payload["text"] = qfile_plan_format()
        if (
            self.cfg.model_caps.get("supports_temperature", True)
            and not uses_reasoning_defaults(self.cfg.model)
        ):
            payload["temperature"] = 0.0
        self._log_request_attachments(
            "QFILE_PLAN", qfile_ref_files, input_files, input_images, [], None
        )
        step_id = self.log.begin_validated_step(
            "QFILE_PLAN", kind="preparation", model=self.cfg.model
        )
        self.log.save_json(
            "requests",
            f"QFILE_PLAN_request_{ts_code()}",
            {"payload": payload, "ui_state": self.cfg.__dict__},
            step_id=step_id,
        )
        self._log_api_action(
            "QFILE_PLAN", "send",
            {"model": self.cfg.model, "files": len(qfile_ref_files), "previous_response_id": None},
        )
        response = self._create_response(client, payload)
        parsed = validate_output(response, payload)
        plan = _validate_qfile_plan(parsed)
        self.log.save_json(
            "responses",
            f"QFILE_PLAN_response_{response.get('id', 'NOID')}_{ts_code()}",
            response,
            step_id=step_id,
        )
        validation = self.log.record_validation(
            step_id=step_id,
            target_type="qfile_plan",
            target_id=plan["proposed_path"],
            validator="QFILE_PLAN_V1",
            status="passed",
            evidence={
                "proposed_path": plan["proposed_path"],
                "format": plan["format"],
                "approval_required": True,
            },
        )
        self.log.bundle.update_step(
            step_id, status="completed", progress=100,
            finished_at=validation["timestamp"],
            human_summary="Návrh cesty je validní; výroba čeká na nové potvrzení uživatele.",
        )
        self.log.update_state(
            {
                "qfile_plan": plan,
                "qfile_plan_response_id": str(response.get("id") or ""),
                "status": "qfile_plan_ready",
            }
        )
        return {
            "mode": "QFILE",
            "status": "qfile_plan_ready",
            "qfile_plan": plan,
            "response_id": str(response.get("id") or ""),
            "requires_user_confirmation": True,
        }

    target_path = validate_relative_path(target_path)
    fmt = str(self.cfg.qfile_output_format or "")
    if fmt not in QFILE_FORMATS or Path(target_path).suffix.lower().lstrip(".") != fmt:
        raise ContractError("QFILE: schválená cesta a formát si neodpovídají.")

    plan = dict(self.cfg.qfile_plan or {})
    if plan:
        proposed = validate_relative_path(plan.get("proposed_path"))
        if proposed != target_path or str(plan.get("format") or "") != fmt:
            # Uživatel smí návrh ručně změnit; tím vzniká nové explicitní schválení,
            # nikoli tiché převzetí modelové cesty.
            plan = {}
    if not plan:
        plan = {
            "proposed_path": target_path,
            "format": fmt,
            "purpose": "Výstup QFILE podle explicitního zadání uživatele.",
            "allow_empty": False,
            "acceptance": [],
        }

    self._set(10, 0, f"QFILE: připravuji {target_path}…", stage="QFILE")
    step_id = self.log.begin_validated_step("QFILE", kind="file_delivery", model=self.cfg.model)
    qfile_input = {"plan": plan, "request": prompt, "evidence": _evidence()}
    payload = self._payload_base(
        model=self.cfg.model,
        instructions=QFILE_INSTRUCTIONS,
        input_parts=self._input_parts(
            json.dumps(qfile_input, ensure_ascii=False), input_files, input_images
        ),
        prev_id=None,
    )
    payload["text"] = file_content_format()
    if (
        self.cfg.model_caps.get("supports_temperature", True)
        and not uses_reasoning_defaults(self.cfg.model)
    ):
        payload["temperature"] = 0.0
    self._log_request_attachments("QFILE", qfile_ref_files, input_files, input_images, [], None)

    expected_target_hash = None
    target = safe_join_under_root(self.cfg.out_dir, target_path)
    if Path(target).is_file():
        expected_target_hash = sha256_file(target)
    projection = {
        "plan": plan,
        "request": prompt,
        "attachment_ids": list(self.cfg.input_file_ids or []),
    }
    order = freeze_order(
        self.cfg,
        {
            "run_id": self.log.run_id,
            "step_id": step_id,
            "stage": "QFILE",
            "route": "responses_live",
            "target_id": target_path,
            "target_path": target_path,
            "expected_target_hash": expected_target_hash,
            "contract_name": "FILE_CONTENT_V1",
            "schema": payload["text"]["format"]["schema"],
            "prompt": QFILE_INSTRUCTIONS + "\n" + json.dumps(qfile_input, ensure_ascii=False),
            "model": self.cfg.model,
            "model_capability": self.cfg.model_caps,
            "source_snapshot": {
                "prompt": prompt,
                "attached_file_ids": list(self.cfg.attached_file_ids or []),
                "plan": plan,
            },
            "attempt_no": 0,
            "approval_id": f"user-qfile-path:{self.log.run_id}",
        },
        projection,
    )
    self.log.save_json(
        "manifests", "qfile_work_order",
        {**order.to_dict(), "order_hash": order.order_hash},
        step_id=step_id,
    )
    self.log.save_json(
        "requests",
        f"QFILE_request_{ts_code()}",
        {
            "payload": payload,
            "ui_state": self.cfg.__dict__,
            "work_order_hash": order.order_hash,
        },
        step_id=step_id,
    )
    self._log_api_action(
        "QFILE", "send",
        {
            "model": self.cfg.model,
            "files": len(qfile_ref_files),
            "contract": "FILE_CONTENT_V1",
            "work_order_hash": order.order_hash,
            "previous_response_id": None,
        },
    )
    response = self._create_response(client, payload)
    parsed = validate_output(response, payload)
    content = parsed.get("content")
    if not isinstance(content, str):
        raise ContractError("FILE_CONTENT_V1: content musí být text.")
    if not content.strip() and not bool(plan.get("allow_empty")):
        raise ContractError("QFILE: prázdný soubor nebyl schválen.")

    self.log.save_json(
        "responses",
        f"QFILE_response_{response.get('id', 'NOID')}_{ts_code()}",
        response,
        step_id=step_id,
    )
    self._log_api_action(
        "QFILE", "receive",
        {
            "response_id": response.get("id"),
            "status": response.get("status"),
            "contract": "FILE_CONTENT_V1",
            "work_order_hash": order.order_hash,
        },
    )
    out_files = [{"path": target_path, "content": content, "purpose": "QFILE"}]
    self._set(70, 0, f"QFILE: ukládám {target_path}…", stage="QFILE")
    saved_map = self._save_out_files(out_files)
    validation = self.log.record_validation(
        step_id=step_id,
        target_type="file_contract",
        target_id=target_path,
        validator="FILE_CONTENT_V1",
        status="passed",
        evidence={
            "path": target_path,
            "work_order_hash": order.order_hash,
            "content_acceptance": "unverified",
            "written": True,
        },
    )
    self.log.bundle.update_step(
        step_id, status="completed", progress=100,
        finished_at=validation["timestamp"],
        human_summary="Soubor prošel wire a lokální technickou validací; obsahová akceptace není tvrzena.",
    )
    self.log.update_state(
        {
            "file_contract_valid": True,
            "human_verified": False,
            "qfile_plan": plan,
            "qfile_work_order_hash": order.order_hash,
        }
    )
    return {
        "mode": "QFILE",
        "status": "files_complete_unverified",
        "response_id": str(response.get("id") or ""),
        "saved": saved_map,
        "contract": parsed,
        "qfile_plan": plan,
        "work_order_hash": order.order_hash,
    }


class QfileExecutor:
    """QFILE uses QFILE_PLAN_V1 only for an explicit name proposal, then FILE_CONTENT_V1."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_qfile(context, context.client, context.diag_file_ids, context.base_prev_id)
