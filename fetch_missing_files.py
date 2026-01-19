import json
import os
from pathlib import Path

from openai import OpenAI


def fetch_file(client, root: Path, path: str, prev_id: str) -> bool:
    """Request a single A3_FILE chunk via previous_response_id and save it."""
    instructions = "Vrať platný JSON dle kontraktu A3_FILE. ŽÁDNÝ markdown ani další text."
    contract = (
        '{"contract":"A3_FILE","path":"%s","chunking":{"max_lines":500,"chunk_index":0,'
        '"chunk_count":1,"has_more":false,"next_chunk_index":null}}'
    ) % path
    payload = {
        "model": "gpt-5.1",
        "instructions": instructions,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": contract
                        + "\nObsah předchozí odpovědi byl nevalidní JSON – pošli celý soubor znovu, kompletní a validní.",
                    }
                ],
            }
        ],
        "previous_response_id": prev_id,
    }

    print(f"Requesting {path} with prev_id={prev_id} ...")
    resp = client.responses.create(timeout=60, **payload)
    out = resp.output or []
    if not out or not out[0].content:
        print(f"[!] Empty output for {path}")
        return False
    txt = out[0].content[0].text
    try:
        data = json.loads(txt)
    except Exception as e:
        print(f"[!] Invalid JSON for {path}: {e}")
        return False
    content = data.get("content")
    out_path = root / path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content or "")
    print(f"[OK] saved {out_path} ({len(content or '')} bytes)")
    return True


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY") or ""
    if not api_key:
        print("OPENAI_API_KEY není nastaven. Přerušeno.")
        return
    client = OpenAI(api_key=api_key)
    root = Path("C:/DAGMAR")

    requests = [
        {
            "path": "app/ai/dagmar_personality_engine.py",
            "prev": "resp_0a572ade9fcd52ab006963163220388191974feff206776933",
        },
        {
            "path": "app/utils/email_utils.py",
            "prev": "resp_0a572ade9fcd52ab00696314dda39c81918bca3005a9466dc1",
        },
    ]

    ok = 0
    for req in requests:
        if fetch_file(client, root, req["path"], req["prev"]):
            ok += 1
    print(f"Hotovo: {ok}/{len(requests)} souborů uloženo do {root}")


if __name__ == "__main__":
    main()
