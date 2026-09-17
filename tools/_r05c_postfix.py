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
journal.write_text(text, encoding="utf-8")

bundle = ROOT / "tests/test_run_bundle.py"
text = bundle.read_text(encoding="utf-8")
old = 'match="(?i)artefakt"'
new = 'match=r"(?i)artefakt"'
if old not in text:
    raise RuntimeError("Expected run bundle regex not found")
bundle.write_text(text.replace(old, new, 1), encoding="utf-8")

print("R05-C strict-test postfix applied")
