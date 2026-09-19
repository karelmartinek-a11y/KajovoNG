"""Převod lidského zadání do profesionálního image-edit promptu."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
import uuid

from .model_registry import model_spec
from .orchestration.ledger import (
    mark_submission,
    release_reservation,
    reserve_paid_request,
    settle_usage,
)
from .orchestration.run_config import (
    DEFAULT_MAX_COST_MICROUSD,
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_PAID_REQUESTS,
)
from .orchestration.work_order import freeze_order
from .runlog import RunLogger
from .structured_output import array, obj, response_format, validate_output

PROFESSIONALIZE_INSTRUCTIONS = """Jsi specialista na prompt engineering pro profesionální editaci reálných fotografií pomocí modelů OpenAI GPT Image.

Tvým jediným úkolem je převést uživatelské laické zadání do přesného, jednoznačného a profesionálního promptu pro úpravu EXISTUJÍCÍ fotografie.

Nesmíš změnit záměr uživatele. Nesmíš přidávat požadavky, které uživatel neuvedl nebo které z jeho zadání jednoznačně nevyplývají. Nesmíš si vymýšlet nové objekty, vybavení, osoby, dekorace, architektonické prvky ani jiné obsahové změny.

Je-li uživatelův požadavek zaměřen na realistickou fotografii, formuluj instrukce tak, aby výsledek zachoval fotorealistický vzhled, přirozené materiály, realistické světlo, geometrii, perspektivu a identitu původního prostoru nebo objektu. Pokud uživatel požaduje odstranění nebo změnu konkrétního prvku, popiš tuto změnu přesně. Pokud požaduje zachování určitého prvku, uveď to explicitně.

Odstraň vágní formulace, opakování, rozpory a konverzační výplň. Nahraď je přesnými vizuálními a fotografickými instrukcemi. Požadavky na světlo, perspektivu, svislice, horizont, barvy, expozici, kontrast, čistotu, ostrost, kompozici, objekty, textury, materiály, odrazy, okna, výhled a postprodukci přepiš do profesionální fotografické terminologie, ale zachovej jejich původní význam.

NEVYSVĚTLUJ, co jsi změnil. NEKOMENTUJ zadání. NEPIŠ úvod ani závěr. NEPOUŽÍVEJ Markdown.

Výstupem musí být pouze hotový prompt, který lze bez další úpravy předat image-edit modelu. Výsledný profesionální prompt napiš v angličtině."""


@dataclass(frozen=True)
class ProfessionalizedPrompt:
    original_prompt: str
    professional_prompt: str
    model: str
    response_id: str
    request_id: str
    response: dict
    photo_plan: dict


def professionalize_payload(model: str, prompt: str) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Nejprve napište zadání úpravy fotografie.")
    if len(prompt) > 30000:
        raise ValueError("Zadání je příliš dlouhé; maximálně 30 000 znaků.")
    schema = obj({
        "professional_prompt": {"type": "string"},
        "edit_actions": array({"type": "string"}),
        "preserve_invariants": array({"type": "string"}),
        "acceptance_criteria": array({"type": "string"}),
    })
    payload = {
        "model": model,
        "instructions": PROFESSIONALIZE_INSTRUCTIONS,
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
    original = prompt.strip()
    payload = professionalize_payload(model, original)
    run_id = "RUN_PHOTO_PROMPT_" + uuid.uuid4().hex
    log = RunLogger(str(log_dir), run_id, "Photo Studio")
    cfg = SimpleNamespace(
        mode="PHOTO",
        model=model,
        send_as_c=False,
        maximum_quality=False,
        max_cost_microusd=DEFAULT_MAX_COST_MICROUSD,
        max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        max_paid_requests=DEFAULT_MAX_PAID_REQUESTS,
        unknown_pricing="block",
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
    reserve_paid_request(
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
    try:
        response = client.create_response(payload)
    except Exception as exc:
        definite_reject = (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None)
            in {400, 401, 403, 404, 422, 429}
        )
        if definite_reject:
            release_reservation(log, order)
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
    settle_usage(log, order, response)
    log.save_json("responses", "photo_plan_response", response)
    if reporter:
        reporter("Odpověď Responses API přijata; ověřuji výstupní kontrakt.")
    value = validate_output(response, payload)
    final = str(value.get("professional_prompt") or "").strip()
    if not final:
        raise ValueError("Responses API vrátilo prázdný profesionální prompt.")
    if len(final) > 60000:
        raise ValueError("Výsledný profesionální prompt překračuje interní limit.")
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
    value = str(prompt or "").strip()
    if not value:
        raise ValueError("PHOTO_PLAN_V1 vyžaduje neprázdný prompt.")
    return {
        "version": 1,
        "professional_prompt": value,
        "edit_actions": [value],
        "preserve_invariants": [],
        "acceptance_criteria": [
            "Výsledek musí splnit přesně finální uživatelský prompt bez nesouvisející změny."
        ],
    }
