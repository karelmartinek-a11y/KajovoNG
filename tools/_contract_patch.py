"""Jednorázová přesně ohraničená editace pracovní větve; nepoužívat na uživatelská data."""
from __future__ import annotations

import ast
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDS = {
    "kajovo/core/orchestration/work_order.py": "5377e38ec405fe3e469e6612257e0a3cf5c79fd1",
    "kajovo/core/orchestration/provider_operations.py": "677686a5d1f734e6d9f3646ccd681eac702c83b4",
    "kajovo/core/orchestration/publish.py": "f9495a415aa2a3975ae676ccb44d8bd09c8d6e6b",
    "kajovo/core/comic_service.py": "790fb97346d68c4ed51beebe6305b46d65c6cc82",
    "kajovo/core/runlog.py": "8ee0dd157d691fdf8b84eccfc0a71d1fe3405b69",
    "kajovo/core/safe_config.py": "506ed3b11e936df47d9f97642e75df77df4f9ecf",
    "tests/test_orchestration_repository.py": "c9957428582b856b0def1f55b35d8fb44960fec3",
}
TEXT = {}
for relative, expected in GUARDS.items():
    actual = subprocess.check_output(["git", "rev-parse", "HEAD:" + relative], cwd=ROOT, text=True).strip()
    if actual != expected:
        raise RuntimeError("Změněný vstup: " + relative)
    TEXT[relative] = (ROOT / relative).read_text(encoding="utf-8")


def replace(relative, old, new):
    value = TEXT[relative]
    if value.count(old) != 1:
        raise RuntimeError("Nejednoznačná kotva: " + relative + " " + old[:90])
    TEXT[relative] = value.replace(old, new, 1)


def replace_node(relative, qualified, source):
    value = TEXT[relative]
    node = ast.parse(value)
    for part in qualified.split("."):
        matches = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == part]
        if len(matches) != 1:
            raise RuntimeError("Nejednoznačná definice: " + qualified)
        node = matches[0]
    lines = value.splitlines(keepends=True)
    start = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])]) - 1
    replacement = textwrap.indent(textwrap.dedent(source).strip() + "\n", " " * node.col_offset)
    TEXT[relative] = "".join(lines[:start]) + replacement + "".join(lines[node.end_lineno:])


work = "kajovo/core/orchestration/work_order.py"
replace(work, "import hashlib\n", "import hashlib\nimport re\n")
replace(work, "from .contracts import canonical_bytes, canonical_sha256\n", "from ..utils import validate_relative_path\nfrom .contracts import canonical_bytes, canonical_sha256\n")
replace(work, '\n\ndef _hash(value: Any) -> str:\n', '''
    for key in ("input_projection_hash", "schema_hash", "prompt_hash", "model_capability_hash", "policy_hash", "source_snapshot_hash"):
        if re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            raise ValueError(f"WORK_ORDER_V2.{key} musí být SHA-256.")
    expected_hash = value["expected_target_hash"]
    if expected_hash is not None and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
        raise ValueError("WORK_ORDER_V2.expected_target_hash musí být SHA-256 nebo explicitní null.")
    path = value["target_path"]
    if path is not None:
        if validate_relative_path(path) != path or "\\\\" in path:
            raise ValueError("WORK_ORDER_V2.target_path musí být kanonická relativní cesta.")
    elif expected_hash is not None:
        raise ValueError("WORK_ORDER_V2: hash cíle bez cílové cesty.")
    if value["contract_name"] == "FILE_CONTENT_V1" and path is None:
        raise ValueError("WORK_ORDER_V2: souborový kontrakt vyžaduje cílovou cestu.")
    if type(value["attempt_no"]) is not int or value["attempt_id"] != attempt_identity(value["run_id"], value["task_id"], value["attempt_no"]):
        raise ValueError("WORK_ORDER_V2.attempt_id neodpovídá běhu, úloze a pokusu.")


def _hash(value: Any) -> str:
''')
replace(work, '    return WorkOrder(**value)\n', '''    order = WorkOrder(**value)
    validate_work_order_v2(order.to_dict())
    supplied = raw_value.get("order_hash")
    if "attempt_id" in raw_value and supplied is not None and supplied != order.order_hash:
        raise ValueError("WORK_ORDER_V2.order_hash neodpovídá obsahu.")
    return order
''')
replace(work, '    target_path = task.get("target_path")\n', '''    target_path = task.get("target_path")
    if target_path is not None and "expected_target_hash" not in task:
        raise ValueError("WORK_ORDER_V2: chybí explicitní původní očekávání cíle.")
''')
replace(work, '    attempt_no = int(task.get("attempt_no", 1))\n', '''    attempt_no = task.get("attempt_no", 1)
    if type(attempt_no) is not int:
        raise ValueError("WORK_ORDER_V2.attempt_no musí být celé číslo.")
''')

provider = "kajovo/core/orchestration/provider_operations.py"
replace(provider, 'from .repository import repository_for_logger\n', 'from .repository import repository_for_logger\nfrom .request_binding import validate_response_work_order\n')
replace(provider, '    if measurement is None:\n', '    validate_response_work_order(work_order, payload)\n    if measurement is None:\n')
replace(provider, '    repo = repository_for_logger(logger)\n    for row, supplied in zip(requests, measurements, strict=True):\n', '''    # Celá sada musí být platná před prvním zápisem či zahájením operace.
    identifiers = [str(row.get("custom_id") or "") for row in requests]
    if not identifiers or "" in identifiers or len(set(identifiers)) != len(identifiers) or set(identifiers) != set(work_orders):
        raise ContractError("BATCH custom_id a WorkOrder mapování nejsou vzájemně jednoznačné.")
    for row, supplied in zip(requests, measurements, strict=True):
        raw_order = work_orders[row["custom_id"]]
        order = raw_order if isinstance(raw_order, WorkOrder) else work_order_from_mapping(raw_order)
        if row.get("method") != "POST" or row.get("url") != "/v1/responses" or order.route != "responses_batch":
            raise ContractError("BATCH řádek neodpovídá transportní cestě WorkOrderu.")
        validate_response_work_order(order, row["body"])
        ensure_technical_limits(copy.deepcopy(supplied))
    repo = repository_for_logger(logger)
    for row, supplied in zip(requests, measurements, strict=True):
''')
replace_node(provider, "_endpoint", '''
def _endpoint(order: WorkOrder) -> str:
    if order.route in {"responses_batch", "image_batch"}:
        return "/v1/batches"
    if order.route == "responses_live":
        return "/v1/responses"
    raise ContractError("Obrazová/local operace vyžaduje vlastní explicitní endpoint.")
''')

comic = "kajovo/core/comic_service.py"
replace(comic, '''        prepare_provider_request(
            log, cfg, self.client, body, work_order=order
        )
        journal = ResponseJournal(
            log, self.settings.response_poll_timeout_s
        )
        if not journal.has_entry(body):
''', '''        journal = ResponseJournal(log, self.settings.response_poll_timeout_s)
        resume_existing = journal.has_entry(body)
        transport_body = {**body, "background": True, "store": True}
        prepare_provider_request(
            log, cfg, self.client, transport_body, work_order=order,
            allow_existing=resume_existing,
        )
        if not resume_existing:
''')

safe = "kajovo/core/safe_config.py"
TEXT[safe] += '''

# Kanonická data jsou již vybraná vstupní politikou a typovaným konfigurátorem.
# Diagnostická redakce nesmí pozměnit jejich obsah až po výpočtu hashů.
_CANONICAL_FIELDS = frozenset({
    "payload", "schema", "file_context", "projection", "source_snapshot",
    "preparation_snapshot", "graph", "plan", "requirements", "response",
    "prompt", "instructions", "input", "output", "output_text", "content",
    "text", "recovery_instruction", "repair_instruction",
})
_DIAGNOSTIC_FIELDS = frozenset({
    "error", "failure_detail", "last_error", "trace", "exception", "headers", "http_headers",
})


def persist_evidence(value: Any) -> Any:
    """Bezpečný metadatový zápis, který zachová kanonické kontrakty bitově.

    Runtime credentials nepatří do provider payloadu ani do schémat. ui_state
    se vždy znovu promítá explicitním whitelistem; diagnostika používá oddělenou
    ztrátovou redakci. Odvozené historické exporty zůstávají redigované.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key).strip().casefold().replace("-", "_")
            if name == "ui_state" and isinstance(item, dict):
                result[key] = {field: copy.deepcopy(item[field]) for field in SAFE_UI_FIELDS if field in item}
                result[key]["runtime_credentials"] = {"ssh_password": "runtime-only"}
            elif name in {secret.replace("-", "_") for secret in _SECRET_KEYS}:
                result[key] = "[REDACTED]"
            elif name in _DIAGNOSTIC_FIELDS:
                result[key] = redact_evidence(item)
            elif name in _CANONICAL_FIELDS:
                if name == "payload" and isinstance(item, dict):
                    forbidden = {str(k).casefold().replace("-", "_") for k in item} & {"authorization", "api_key", "ssh_password", "password", "headers"}
                    if forbidden:
                        raise ValueError("Runtime credentials/HTTP hlavičky nesmějí být v kanonickém payloadu.")
                result[key] = copy.deepcopy(item)
            else:
                result[key] = persist_evidence(item)
        return result
    if isinstance(value, list):
        return [persist_evidence(item) for item in value]
    if isinstance(value, tuple):
        return tuple(persist_evidence(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value
'''
log = "kajovo/core/runlog.py"
replace(log, 'from .safe_config import redact_evidence\n', 'from .safe_config import persist_evidence, redact_evidence\n')
replace(log, 'json.dump(self._redact(payload), stream, ensure_ascii=False, indent=2, default=str)', 'json.dump(persist_evidence(payload), stream, ensure_ascii=False, indent=2, default=str)')
replace(log, '        patch = self._redact(patch)\n', '        patch = persist_evidence(patch)\n')
replace(log, '        obj = self._redact(obj)\n', '        obj = persist_evidence(obj)\n')

publish = "kajovo/core/orchestration/publish.py"
replace(publish, 'from ..utils import ensure_dir, safe_join_under_root, sha256_file\n', 'from ..utils import ensure_dir, safe_join_under_root, sha256_file, validate_relative_path\n')
replace(publish, '    for part in relative.split("/")[:-1]:\n', '    validate_relative_path(relative)\n    for part in relative.replace("\\\\", "/").split("/"):\n')
replace(publish, '        if current.exists() and (current.is_symlink() or is_junction(str(current))):\n', '        if current.is_symlink() or is_junction(str(current)):\n')
replace(publish, '''            backup_path = undo_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup_path)
            with backup_path.open("rb") as handle:
                os.fsync(handle.fileno())
            _fsync_directory(backup_path.parent)
            backup_hash = sha256_file(str(backup_path))
            if backup_hash != current_hash:
                raise OrchestrationError("PUBLISH_BACKUP_HASH", relative)
            backup_ref = backup_path.relative_to(run_root).as_posix()
''', '''            # Záloha je obsahově adresovaná a nelze ji přepsat jiným plánem.
            original = destination.read_bytes()
            if hashlib.sha256(original).hexdigest() != current_hash:
                raise OrchestrationError("PUBLISH_CONFLICT", relative)
            backup_path = undo_root / (str(current_hash) + ".bin")
            if not backup_path.exists():
                _write_bytes_atomic(backup_path, original)
            if not backup_path.is_file() or sha256_file(str(backup_path)) != current_hash:
                raise OrchestrationError("PUBLISH_BACKUP_HASH", relative)
            backup_ref = backup_path.relative_to(run_root).as_posix()
''')
replace(publish, '        _write_bytes_atomic(destination, backup.read_bytes())\n', '''        original = backup.read_bytes()
        if hashlib.sha256(original).hexdigest() != row["expected_old_hash"]:
            raise OrchestrationError("PUBLISH_BACKUP_HASH", row["path"])
        _assert_no_link_boundary(target_root, row["path"])
        if _hash_if_file(destination) != row["new_hash"]:
            raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", row["path"])
        _write_bytes_atomic(destination, original)
''')
replace_node(publish, "_TargetPublishLock", '''
class _TargetPublishLock:
    """Procesový zámek OS; pád uvolní handle bez mazání souboru jiného procesu."""
    def __init__(self, target_root: Path):
        self.target_root = target_root.resolve()
        key = hashlib.sha256(os.path.normcase(str(self.target_root)).encode("utf-8")).hexdigest()
        directory = Path(tempfile.gettempdir()) / "kajovo-publish-locks"
        if directory.is_symlink():
            raise OrchestrationError("PUBLISH_LOCK_UNSAFE", str(directory))
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / (key + ".lock")
        self.handle = None

    def acquire(self) -> None:
        if self.path.is_symlink() or getattr(os.path, "isjunction", lambda p: False)(str(self.path)):
            raise OrchestrationError("PUBLISH_LOCK_UNSAFE", str(self.path))
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self.path), flags, 0o600)
        handle = os.fdopen(fd, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise OrchestrationError("PUBLISH_LOCKED", str(self.target_root)) from exc
        self.handle = handle

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
''')
# Starý PID-probe není potřebný; na Windows není os.kill(pid, 0) bezpečný probe.
replace_node(publish, "_pid_alive", '''
def _validate_journal_plan(journal: dict[str, Any], run_root: Path) -> None:
    plan = journal.get("plan")
    entries = journal.get("entries")
    if not isinstance(plan, dict) or not isinstance(entries, list) or not entries:
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(run_root))
    planned = plan.get("entries")
    if not isinstance(planned, list) or len(planned) != len(entries):
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", "Neúplná sada položek.")
    fields = {"path", "staged_path", "expected_old_hash", "new_hash", "backup_ref"}
    normalized = []
    for row, original in zip(entries, planned, strict=True):
        if not isinstance(row, dict) or not isinstance(original, dict) or set(original) != fields:
            raise OrchestrationError("PUBLISH_JOURNAL_INVALID", "Neplatná položka.")
        if {key: row.get(key) for key in fields} != original:
            raise OrchestrationError("PUBLISH_JOURNAL_INVALID", "Položka změnila zmrazený plán.")
        for field in ("path", "staged_path"):
            validate_relative_path(original[field])
        for field in ("staged_path", "backup_ref"):
            if original[field] is not None:
                path = (run_root / original[field]).resolve()
                try:
                    path.relative_to(run_root)
                except ValueError as exc:
                    raise OrchestrationError("PUBLISH_JOURNAL_ESCAPE", str(original[field])) from exc
        normalized.append(os.path.normcase(original["path"].replace("\\\\", "/")))
    if len(normalized) != len(set(normalized)):
        raise OrchestrationError("PUBLISH_PATH_COLLISION", "Journal obsahuje duplicitní cestu.")
    seed = {"target_root": plan["target_root"], "entries": planned}
    if plan.get("plan_id") != "PUBLISH-" + canonical_sha256(seed)[:32]:
        raise OrchestrationError("PUBLISH_JOURNAL_HASH", str(run_root))
''')
replace(publish, '    target_root = Path(journal["plan"]["target_root"]).resolve()\n    state = str(journal.get("state") or "")\n', '    _validate_journal_plan(journal, run_root)\n    target_root = Path(journal["plan"]["target_root"]).resolve()\n    state = str(journal.get("state") or "")\n')
replace(publish, '''    if state in {"committed", "rolled_back"}:
        return _journal_report(journal, journal_path, run_root)
''', '''    if state in {"committed", "rolled_back"}:
        field = "new_hash" if state == "committed" else "expected_old_hash"
        for row in journal["entries"]:
            _assert_no_link_boundary(target_root, row["path"])
            destination = Path(safe_join_under_root(str(target_root), row["path"]))
            if _hash_if_file(destination) != row[field]:
                raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", row["path"])
        return _journal_report(journal, journal_path, run_root)
''')
replace(publish, 'import contextlib\n', '')

fixture = "tests/test_orchestration_repository.py"
replace(fixture, 'from kajovo.core.orchestration.work_order import WorkOrder\n', 'from kajovo.core.orchestration.contracts import canonical_sha256\nfrom kajovo.core.orchestration.work_order import WorkOrder, attempt_identity\n')
for prefix in ("input", "schema", "prompt", "cap", "policy", "source"):
    replace(fixture, '"' + prefix + '-" + task_id,', 'canonical_sha256("' + prefix + '-" + task_id),')
replace(fixture, '        attempt_id=attempt_id,\n', '        attempt_id=attempt_identity(run_id, task_id, 1),\n')

# Změna testu nesnižuje požadavek: dokazuje konkrétní místo tvrdého pádu.
crash = "tests/test_delivery_pipeline.py"
TEXT[crash] = (ROOT / crash).read_text(encoding="utf-8")
replace(crash, '    assert child.returncode != 0\n', '    assert child.returncode == 91, (child.stdout, child.stderr)\n')
replace(crash, '''        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    assert child.returncode == 91''', '''        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 91''')

binding = "kajovo/core/orchestration/request_binding.py"
if (ROOT / binding).exists():
    raise RuntimeError("Nový modul již existuje: " + binding)
TEXT[binding] = '''"""Vazba skutečného Responses payloadu na kanonický WorkOrder před síťovou operací."""
from __future__ import annotations

from typing import Any

from ..contracts import ContractError
from ..structured_output import file_content_format, prepare_payload
from .contracts import canonical_sha256
from .work_order import WorkOrder


def validate_response_work_order(order: WorkOrder, payload: dict[str, Any]) -> None:
    try:
        order.to_dict()
        prepare_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ContractError("REQUEST_WORK_ORDER_INVALID: " + str(exc)) from exc
    if order.route not in {"responses_live", "responses_batch"}:
        raise ContractError("REQUEST_ROUTE_MISMATCH: Responses vyžaduje Responses WorkOrder.")
    if payload.get("model") != order.model:
        raise ContractError("REQUEST_MODEL_MISMATCH: payload změnil zmrazený model.")
    fmt = payload["text"]["format"]
    if fmt["name"] != order.contract_name:
        raise ContractError("REQUEST_CONTRACT_MISMATCH: payload změnil název kontraktu.")
    if canonical_sha256(fmt["schema"]) != order.schema_hash:
        raise ContractError("REQUEST_SCHEMA_MISMATCH: payload změnil JSON masku.")
    if order.contract_name == "FILE_CONTENT_V1" and fmt["schema"] != file_content_format()["format"]["schema"]:
        raise ContractError("REQUEST_FILE_SCHEMA_MISMATCH: model nesmí přepisovat cestu cíle.")
    if {str(key).casefold() for key in payload} & {"authorization", "headers", "api_key", "password", "ssh_password"}:
        raise ContractError("REQUEST_CREDENTIAL_FIELD: runtime tajemství nesmí být částí payloadu.")
'''

for relative, content in TEXT.items():
    ast.parse(content, filename=relative)
for relative, content in TEXT.items():
    (ROOT / relative).write_text(content, encoding="utf-8", newline="\n")
print("APPLIED_FILES", *sorted(TEXT), sep="\n")
