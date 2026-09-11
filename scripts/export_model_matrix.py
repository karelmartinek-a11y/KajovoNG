"""Exportuje čitelnou matici ze stejného pevného katalogu jako validátor, bez sítě."""
from __future__ import annotations

import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kajovo.core.model_registry import model_ids, model_spec, selectable, matrix_version


def rows():
    for model in model_ids():
        spec = model_spec(model)
        yield {
            "model": model, "canonical": spec["canonical"], "app_selectable": selectable(model),
            "responses_api": spec["responses"], "batch_api": spec["batch"],
            "app_live": selectable(model), "app_batch": selectable(model) and spec["batch"],
            "strict_json_schema": "structured_outputs" in spec["features"],
            "reasoning_effort": ",".join(spec["reasoning"]), "reasoning_mode": ",".join(spec["reasoning_modes"]),
            "reasoning_summary": ",".join(spec["reasoning_summaries"]), "temperature_top_p": spec["sampling"],
            "file_id_input": "file_uploads" in spec["features"], "image_input": "image_input" in spec["features"],
            "pdf_input": "file_uploads" in spec["features"] and "image_input" in spec["features"],
            "file_search_vector_stores": "file_search" in spec["features"],
            "image_detail": ",".join(spec["image_details"]), "context_window": spec["context_window"],
            "max_input_tokens": spec["max_input_tokens"], "max_output_tokens": spec["max_output_tokens"],
            "service_tier_live": ",".join(spec["service_tiers"]), "service_tier_batch": "auto,default",
            "prompt_cache_retention": ",".join(spec["cache_retention"]), "prompt_cache_options": spec["cache_options"],
            "all_documented_features": ",".join(spec["features"]),
            "all_documented_endpoints": ",".join(route for _,route in spec["endpoints"]),
            "notes": " ".join(spec["notes"]), "source": spec["source"], "source_sha256": spec["source_sha256"],
        }


def export(root):
    values = list(rows())
    with (root / "MODEL_MATRIX.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)
    lines = ["# Pevná matice modelů OpenAI", "",
        f"Verze {matrix_version()}. {len(values)} přesných identifikátorů a snapshotů; {sum(r['app_live'] for r in values)} povolených pro pracovní Responses.", "",
        "Úplná pole obsahuje [CSV](MODEL_MATRIX.csv); kombinace pracovních postupů [matice požadavků](REQUEST_MATRIX.md). "
        "Pravidla jsou součástí balíčku `kajovo/core/openai_model_matrix.json`. Za běhu se nestahují ani neodvozují z názvů modelů.", "",
        "LIVE/BATCH v této tabulce znamená použitelnost v aplikaci (vždy strict JSON Schema), nikoli podporu všech endpointů OpenAI. "
        "CSV odděluje dokumentovanou podporu API a podporu aplikace. Neznámé ID, fine-tuned ID a budoucí snapshot vyžadují doplnění pevné specifikace; nedědí schopnosti podle prefixu.", "",
        "Sampling: `always` = temperature 0–2 a top_p 0–1; `explicit_none` = stejné rozsahy pouze s explicitním reasoning.effort=none; "
        "`omit` = aplikace parametr nepovoluje. U variant bez jednoznačné dokumentace jde o konzervativní omezení aplikace, nikoli důkaz, že OpenAI odmítá každou hodnotu. "
        "Prázdný seznam effort znamená parametr vynechat. Dostupnost pro konkrétní účet, region a aktuální stav prostředků ověřuje API.", "",
        "Modelová stránka GPT-5.2 Pro a GPT-5.4 Pro nepotvrzuje Batch ani strict Structured Outputs. "
        "Tyto modely aplikace konzervativně blokuje. Přímé soubory vycházejí také z [File inputs](https://developers.openai.com/api/docs/guides/file-inputs); PDF vyžaduje vision. "
        "Ostatní nástroje a endpointy OpenAI uvádí CSV informativně; aplikace implementuje generování pouze přes Responses a nástroj file_search.", "",
        "| Model | LIVE | BATCH | Effort | Sampling | Soubor / obrázek / file search | Maximum výstupu |",
        "|---|---|---|---|---|---|---|"]
    flag = lambda x: "ano" if x else "ne"
    for r in values:
        lines.append(f"| [{r['model']}]({r['source']}) | {flag(r['app_live'])} | {flag(r['app_batch'])} | {r['reasoning_effort'] or '—'} | {r['temperature_top_p']} | "
            f"{flag(r['file_id_input'])} / {flag(r['image_input'])} / {flag(r['file_search_vector_stores'])} | {r['max_output_tokens'] or '—'} |")
    (root / "MODEL_MATRIX.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(f"Exportováno {len(values)} identifikátorů matice {matrix_version()}.")


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1] / "docs")
