import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

journal = ROOT / "tests/test_response_journal.py"
text = journal.read_text(encoding="utf-8")
old = 'results[:pending_index] + [{"id": results[pending_index]["id"], "status": "queued"}]'
new = '[*results[:pending_index], {"id": results[pending_index]["id"], "status": "queued"}]'
if old not in text:
    raise RuntimeError("Expected response journal list concatenation not found")
text = text.replace(old, new, 1)
pattern = (
    r'@pytest\.mark\.parametrize\("state", \["response_pending", "submission_unknown", "cancelled"\]\)'
    r'\s+(?=@pytest\.mark\.parametrize\("mode", \["GENERATE", "MODIFY"\]\))'
)
text, removed = re.subn(pattern, "", text, count=1)
if removed != 1:
    raise RuntimeError("Expected orphan response-journal decorator not found")
if "StudioContext(settings, Operations(), api_key=\"\")" not in text:
    raise RuntimeError("Expected Studio settings Operations constructor not found")
text = text.replace(
    'StudioContext(settings, Operations(), api_key="")',
    'StudioContext(settings, Operations(None), api_key="")',
    1,
)
journal.write_text(text, encoding="utf-8")

bundle = ROOT / "tests/test_run_bundle.py"
text = bundle.read_text(encoding="utf-8")
old = 'match="(?i)artefakt"'
new = 'match=r"(?i)artefakt"'
if old not in text:
    raise RuntimeError("Expected run bundle regex not found")
bundle.write_text(text.replace(old, new, 1), encoding="utf-8")

progress = ROOT / "tests/test_ui_progress.py"
text = progress.read_text(encoding="utf-8")
old = 'assert errors[0].message == "fixture"'
new = 'assert errors[0].message == "Operaci se nepodařilo dokončit a přesná příčina není doložena."'
if old not in text:
    raise RuntimeError("Expected raw Task failure assertion not found")
progress.write_text(text.replace(old, new, 1), encoding="utf-8")

for relative in ("tests/test_photo_studio.py", "tests/test_process_audit_regressions.py"):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if "Operations()," not in text:
        raise RuntimeError(f"Expected Operations constructor not found in {relative}")
    path.write_text(text.replace("Operations(),", "Operations(None),", 1), encoding="utf-8")

print("R05-C strict-test postfix applied")
