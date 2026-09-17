from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "kajovo/core/runs/runtime_base.py"
text = path.read_text(encoding="utf-8")
old = '''class RuntimeMixinBase:
    """MRO základ: konkrétní stav a porty vlastní RunExecutor."""

    def __getattr__(self, name: str) -> Any:
'''
new = '''class RuntimeMixinBase:
    """MRO základ: konkrétní stav a porty vlastní RunExecutor."""

    _last_prev_id_error: str | None
    _final_response_id: str | None
    _fs_tools: list[dict[str, Any]] | None
    _in_dir_info: dict[str, Any] | None
    _vector_store_ids: list[str]
    _diag_vector_store_ids: list[str]
    _diag_text: str
    _diag_zip_path: str
    _input_kind_cache: dict[str, str]
    _file_name_cache: dict[str, str]
    _response_file_ids: dict[str, str]
    resume_generate_batch: dict[str, Any] | None
    _delivery_snapshot: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
'''
if old not in text:
    raise RuntimeError("Generated RuntimeMixinBase contract changed unexpectedly")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Runtime mixin shared types stabilized")
