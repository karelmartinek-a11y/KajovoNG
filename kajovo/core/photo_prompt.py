"""Převod lidského zadání do profesionálního image-edit promptu."""
from __future__ import annotations

from dataclasses import dataclass

from .model_registry import model_spec
from .structured_output import obj, response_format, validate_output

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


def professionalize_payload(model: str, prompt: str) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Nejprve napište zadání úpravy fotografie.")
    if len(prompt) > 30000:
        raise ValueError("Zadání je příliš dlouhé; maximálně 30 000 znaků.")
    schema = obj({"professional_prompt": {"type": "string"}})
    payload = {
        "model": model,
        "instructions": PROFESSIONALIZE_INSTRUCTIONS,
        "input": "Převeď následující uživatelské zadání na profesionální prompt:\n\n<USER_PROMPT>\n" + prompt + "\n</USER_PROMPT>",
        "text": response_format("PHOTO_PROFESSIONAL_PROMPT", schema),
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
    if reporter:
        reporter("Profesionální prompt je připraven.")
    return ProfessionalizedPrompt(
        original,
        final,
        model,
        str(response.get("id") or ""),
        str(response.get("_request_id") or ""),
        response,
    )
