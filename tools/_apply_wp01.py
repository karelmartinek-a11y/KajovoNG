from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: ocekavan 1 vyskyt, nalezeno {count}")
    return text.replace(old, new, 1)


def migrate_openai_client() -> None:
    path = ROOT / "kajovo/core/openai_client.py"
    text = path.read_text(encoding="utf-8")
    if "from .openai_transport import (" not in text:
        anchor = "from .compat import is_compatible_path\n"
        block = """from .openai_transport import (\n    OpenAIError,\n    OpenAITransport,\n    SubmissionOutcomeUnknown,\n    operation_spec,\n)\n"""
        text = replace_once(text, anchor, anchor + block, label="transport import")

    text, removed = re.subn(
        r"\n\nclass OpenAIError\(Exception\):.*?\n\n\nclass OpenAIClient:",
        "\n\nclass OpenAIClient:",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if removed not in (0, 1):
        raise RuntimeError("OpenAIError migrace je nejednoznacna")

    session_anchor = '        self.session.headers.update({"Authorization": f"Bearer {api_key}"})\n'
    if "self._transport = OpenAITransport(" not in text:
        transport_init = """        self._transport = OpenAITransport(\n            base_url=self.base_url,\n            api_key=api_key,\n            timeout_s=self.timeout_s,\n            session=self.session,\n            backoff_base_s=self.backoff_base_s,\n            backoff_cap_s=self.backoff_cap_s,\n        )\n"""
        text = replace_once(
            text,
            session_anchor,
            session_anchor + transport_init,
            label="transport init",
        )

    replacement = '''    def _req(\n        self,\n        method: str,\n        path: str,\n        json_body: Optional[Dict[str, Any]] = None,\n        files=None,\n        timeout: Optional[float] = None,\n        max_attempts=None,\n    ) -> Any:\n        """Kompatibilitni fasada; retry rozhoduje vyhradne OpenAITransport."""\n        spec = operation_spec(method, path)\n        return self._transport.request(\n            spec,\n            method,\n            path,\n            json_body=json_body,\n            files=files,\n            timeout=timeout,\n            max_attempts=max_attempts,\n        )\n\n'''
    text, changed = re.subn(
        r"    def _should_retry\(.*?\n(?=    def create_image)",
        replacement,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if changed != 1 and "retry rozhoduje vyhradne OpenAITransport" not in text:
        raise RuntimeError("Nepodarilo se nahradit legacy _req retry logiku")

    path.write_text(text, encoding="utf-8")


def _find_call_end(text: str, start: int) -> int:
    depth = 0
    quote = None
    triple = False
    escaped = False
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif triple and text.startswith(quote * 3, i):
                i += 2
                quote = None
                triple = False
            elif not triple and ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            if text.startswith(ch * 3, i):
                quote = ch
                triple = True
                i += 3
                continue
            quote = ch
            triple = False
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise RuntimeError("Neuzavreny with_retry call")


class _Defaults(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, ast.expr]) -> None:
        self.mapping = mapping

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id in self.mapping:
            return ast.copy_location(self.mapping[node.id], node)
        return node


def unwrap_side_effect_retries(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    cursor = 0
    changed = 0
    while True:
        start = text.find("with_retry(", cursor)
        if start < 0:
            break
        end = _find_call_end(text, start + len("with_retry"))
        snippet = text[start:end]
        try:
            call = ast.parse(snippet, mode="eval").body
        except SyntaxError:
            cursor = end
            continue
        if not isinstance(call, ast.Call) or not call.args or not isinstance(call.args[0], ast.Lambda):
            cursor = end
            continue
        lambda_node = call.args[0]
        body_text = ast.unparse(lambda_node.body)
        if not (
            "client.upload_file(" in body_text
            or "client.add_file_to_vector_store(" in body_text
        ):
            cursor = end
            continue
        params = lambda_node.args.args
        defaults = lambda_node.args.defaults
        mapping: dict[str, ast.expr] = {}
        if defaults:
            for param, default in zip(params[-len(defaults):], defaults):
                mapping[param.arg] = default
        body = _Defaults(mapping).visit(lambda_node.body)
        ast.fix_missing_locations(body)
        replacement = ast.unparse(body)
        text = text[:start] + replacement + text[end:]
        cursor = start + len(replacement)
        changed += 1
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def harden_generic_retry() -> None:
    path = ROOT / "kajovo/core/retry.py"
    text = path.read_text(encoding="utf-8")
    if "SubmissionOutcomeUnknown" not in text:
        text = replace_once(
            text,
            "from .openai_client import OpenAIError\n",
            "from .openai_client import OpenAIError\nfrom .openai_transport import SubmissionOutcomeUnknown\n",
            label="retry import",
        )
        text = replace_once(
            text,
            "        except OpenAIError as e:\n",
            "        except SubmissionOutcomeUnknown:\n            raise\n        except OpenAIError as e:\n",
            label="unknown outcome guard",
        )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    migrate_openai_client()
    harden_generic_retry()
    total = 0
    for relative in ("kajovo/core/pipeline.py", "kajovo/core/cascade_pipeline.py"):
        total += unwrap_side_effect_retries(ROOT / relative)
    if total < 4:
        raise RuntimeError(f"Ocekavany alespon 4 legacy side-effect retry obalky, nalezeno {total}")
    print(f"WP-01 migrace hotova; odstraneno side-effect retry obalek: {total}")


if __name__ == "__main__":
    main()
