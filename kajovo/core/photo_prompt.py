"""Převod lidského zadání do profesionálního image-edit promptu."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
import uuid

from .model_registry import model_spec, models_for_usage
from .contracts import ContractError
from .orchestration.provider_operations import (
    mark_not_submitted,
    mark_submission,
    mark_submission_started,
    prepare_provider_request,
    record_usage,
)
from .orchestration.work_order import freeze_order
from .runlog import RunLogger
from .structured_output import array, obj, response_format, validate_output

PROFESSIONALIZE_INSTRUCTIONS = """Jsi specialista na prompt engineering pro profesionální editaci reálných fotografií pomocí modelů OpenAI GPT Image.

Tvým jediným úkolem je převést uživatelské laické zadání do přesného, jednoznačného a profesionálního promptu pro úpravu EXISTUJÍCÍ fotografie.

Nesmíš změnit záměr uživatele. Nesmíš přidávat požadavky, které uživatel neuvedl nebo které z jeho zadání jednoznačně nevyplývají. Nesmíš si vymýšlet nové objekty, vybavení, osoby, dekorace, architektonické prvky ani jiné obsahové změny.

Je-li uživatelův požadavek zaměřen na realistickou fotografii, formuluj instrukce tak, aby výsledek zachoval fotorealistický vzhled, přirozené materiály, realistické světlo, geometrii, perspektivu a identitu původního prostoru nebo objektu. Pokud uživatel požaduje odstranění nebo změnu konkrétního prvku, popiš tuto změnu přesně. Pokud požaduje zachování určitého prvku, uveď to explicitně.

Odstraň vágní formulace, opakování, rozpory a konverzační výplň. Nahraď je přesnými vizuálními a fotografickými instrukcemi. Požadavky na světlo, perspektivu, svislice, horizont, barvy, expozici, kontrast, čistotu, ostrost, kompozici, objekty, textury, materiály, odrazy, okna, výhled a postprodukci přepiš do profesionální fotografické terminologie, ale zachovej jejich původní význam.

Vrať výhradně JSON objekt PHOTO_PLAN_V1 podle předepsaného schématu, bez Markdownu, úvodu a závěru.
Pole professional_prompt musí obsahovat hotový profesionální prompt v angličtině, který lze bez další úpravy předat image-edit modelu.
Pole edit_actions musí obsahovat alespoň jednu konkrétní požadovanou změnu. Pole preserve_invariants musí obsahovat výslovné požadavky na zachování; pokud žádné nejsou, vrať prázdné pole.
Pole acceptance_criteria musí obsahovat alespoň jedno konkrétní kritérium kontroly výsledku podle zadání. Nevracej prázdné položky ani nedoplňuj nový záměr uživatele. Text všech tří seznamů musí být v souladu s professional_prompt."""


@dataclass(frozen=True)
class ProfessionalizedPrompt:
    original_prompt: str
    professional_prompt: str
    model: str
    response_id: str
    request_id: str
    response: dict
    photo_plan: dict


def _photo_prompt_limit() -> int:
    limits = [
        spec["image_capabilities"]["max_prompt_chars"]
        for model in models_for_usage(None, "photo_edit_batch")
        for spec in [model_spec(model)]
        if spec.get("image_capabilities")
    ]
    if not limits:
        raise ValueError("PHOTO_PLAN_V1 nemá žádný kompatibilní cílový obrazový model.")
    return min(limits)


def _validated_photo_prompt(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PHOTO_PLAN_V1 vyžaduje neprázdný textový prompt.")
    value = value.strip()
    limit = _photo_prompt_limit()
    if len(value) > limit:
        raise ValueError(f"PHOTO_PLAN_V1 prompt překračuje limit obrazového kroku {limit} znaků.")
    return value


def professionalize_payload(model: str, prompt: str) -> dict:
    if not isinstance(prompt, str):
        raise ValueError("Zadání úpravy fotografie musí být text.")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Nejprve napište zadání úpravy fotografie.")
    if len(prompt) > 30000:
        raise ValueError("Zadání je příliš dlouhé; maximálně 30 000 znaků.")
    limit = _photo_prompt_limit()
    text = {"type": "string", "pattern": r"\S"}
    schema = obj({
        "professional_prompt": {**text, "description": f"Hotový anglický prompt, nejvýše {limit} znaků."},
        "edit_actions": {**array(text), "minItems": 1},
        "preserve_invariants": array(text),
        "acceptance_criteria": {**array(text), "minItems": 1},
    })
    payload = {
        "model": model,
        "instructions": PROFESSIONALIZE_INSTRUCTIONS + f"\nprofessional_prompt smí mít nejvýše {limit} znaků.",
        "input": "Převeď následující uživatelské zadání na profesionální prompt:\n\n<USER_PROMPT>\n" + prompt + "\n</USER_PROMPT>",
        "text": response_format("PHOTO_PLAN_V1", schema),
        "store": False,
    }
    spec = model_spec(model)
    if spec.get("reasoning_supported") and "low" in spec.get("reasoning", []):
        payload["reasoning"] = {"effort": "low"}
    max_output = spec.get("max_output_tokens")
    if isinstance(max_output, int) and max_output > 0:
        payload["max_output_tokens"] = min(4096, max_output)
    return payload


def professionalize_prompt(
    client,
    model: str,
    prompt: str,
    log_dir: str | Path,
    reporter=None,
) -> ProfessionalizedPrompt:
    payload = professionalize_payload(model, prompt)
    original = prompt.strip()
    run_id = "RUN_PHOTO_PROMPT_" + uuid.uuid4().hex
    log = RunLogger(str(log_dir), run_id, "Photo Studio")
    cfg = SimpleNamespace(
        mode="PHOTO",
        model=model,
        send_as_c=False,
        maximum_quality=False,
        auto_repair="off",
        verification_profile_ids=[],
        stop_after_plan=False,
        dry_run=False,
        execution_approval_id=f"user-start:{run_id}",
        project="Photo Studio",
        prompt=original,
        in_dir="",
        out_dir="",
        attached_file_ids=[],
        input_file_ids=[],
        attached_vector_store_ids=[],
        qfile_output_path="",
        qfile_output_format="",
        qfile_suggest_path=False,
        qa_continue_conversation=False,
        response_id="",
    )
    schema = payload["text"]["format"]["schema"]
    projection = {
        "human_prompt": original,
        "contract": "PHOTO_PLAN_V1",
    }
    order = freeze_order(
        cfg,
        {
            "run_id": run_id,
            "step_id": "PHOTO_PLAN",
            "task_id": "PHOTO_PLAN",
            "stage": "PHOTO",
            "route": "responses_live",
            "provider_endpoint": "/v1/responses",
            "target_id": "PHOTO_PLAN",
            "target_path": None,
            "expected_target_hash": None,
            "contract_name": "PHOTO_PLAN_V1",
            "schema": schema,
            "prompt": payload["instructions"],
            "model": model,
            "model_capability": model_spec(model),
            "source_snapshot": projection,
            "attempt_no": 1,
            "approval_id": cfg.execution_approval_id,
        },
        projection,
    )
    prepare_provider_request(
        log, cfg, client, payload, work_order=order
    )
    log.save_json(
        "requests",
        "photo_plan_request",
        {"payload": payload, "work_order": {**order.to_dict(), "order_hash": order.order_hash}},
    )
    if reporter:
        reporter("Připravuji pracovní požadavek Responses API.")
        reporter(f"Model: {model}")
        reporter("Odesílám zadání do OpenAI Responses API.")
    mark_submission_started(log, order)
    try:
        response = client.create_response(payload)
    except ContractError as exc:
        rejected_response = getattr(exc, "response", None)
        rejected_id = rejected_response.get("id") if isinstance(rejected_response, dict) else None
        if isinstance(rejected_id, str) and rejected_id.strip():
            mark_submission(log, order, rejected_id, unknown=False)
            record_usage(log, order, rejected_response)
            log.save_json("responses", "photo_plan_response", rejected_response)
            log.update_state({"status": "failed", "error_code": "OUTPUT_CONTRACT"})
        elif getattr(exc, "request_sent", None) is False:
            mark_not_submitted(log, order)
        else:
            mark_submission(log, order, None, unknown=True)
            log.update_state({"status": "submission_unknown"})
        raise
    except Exception as exc:
        definite_reject = (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None)
            in {400, 401, 403, 404, 422, 429}
        )
        if definite_reject:
            mark_not_submitted(log, order)
        else:
            mark_submission(log, order, None, unknown=True)
            log.update_state({"status": "submission_unknown"})
        raise
    provider_id = str(response.get("id") or "")
    if not provider_id:
        mark_submission(log, order, None, unknown=True)
        log.update_state({"status": "submission_unknown"})
        raise ValueError(
            "PHOTO_PLAN Responses submit nemá potvrzené response ID; nový submit je zablokován."
        )
    mark_submission(log, order, provider_id, unknown=False)
    record_usage(log, order, response)
    log.save_json("responses", "photo_plan_response", response)
    if reporter:
        reporter("Odpověď Responses API přijata; ověřuji výstupní kontrakt.")
    value = validate_output(response, payload)
    final = _validated_photo_prompt(value["professional_prompt"])
    plan = {
        "version": 1,
        "professional_prompt": final,
        "edit_actions": [
            str(item).strip() for item in value.get("edit_actions", [])
            if str(item).strip()
        ],
        "preserve_invariants": [
            str(item).strip() for item in value.get("preserve_invariants", [])
            if str(item).strip()
        ],
        "acceptance_criteria": [
            str(item).strip() for item in value.get("acceptance_criteria", [])
            if str(item).strip()
        ],
    }
    if not plan["edit_actions"] or not plan["acceptance_criteria"]:
        raise ValueError(
            "PHOTO_PLAN_V1 musí obsahovat konkrétní edit_actions a acceptance_criteria."
        )
    log.update_state({
        "status": "completed",
        "photo_plan": plan,
        "photo_plan_response_id": provider_id,
    })
    log.bundle.seal()
    if reporter:
        reporter("Profesionální PHOTO_PLAN_V1 je připraven.")
    return ProfessionalizedPrompt(
        original,
        final,
        model,
        str(response.get("id") or ""),
        str(response.get("_request_id") or ""),
        response,
        plan,
    )



def manual_photo_plan(prompt: str) -> dict:
    """Deterministic plan for a user-authored prompt; adds no new edit intent."""
    value = _validated_photo_prompt(prompt)
    return {
        "version": 1,
        "professional_prompt": value,
        "edit_actions": [value],
        "preserve_invariants": [],
        "acceptance_criteria": [
            "Výsledek musí splnit přesně finální uživatelský prompt bez nesouvisející změny."
        ],
    }
