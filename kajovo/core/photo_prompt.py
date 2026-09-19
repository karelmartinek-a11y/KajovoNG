"""Převod lidského zadání do profesionálního image-edit promptu."""
from __future__ import annotations

from dataclasses import dataclass

from .model_registry import model_spec
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


def professionalize_prompt(client, model: str, prompt: str, reporter=None) -> ProfessionalizedPrompt:
    original = prompt.strip()
    payload = professionalize_payload(model, original)
    if reporter:
        reporter("Připravuji pracovní požadavek Responses API.")
        reporter(f"Model: {model}")
        reporter("Odesílám zadání do OpenAI Responses API.")
    response = client.create_response(payload)
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
