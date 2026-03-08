import concurrent.futures
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kajovo.core.pipeline import RunWorker, UiRunConfig


class DummyLog:
    def __init__(self, root):
        self.paths = SimpleNamespace(
            run_dir=root,
            files_dir=os.path.join(root, "files"),
            requests_dir=os.path.join(root, "requests"),
            responses_dir=os.path.join(root, "responses"),
            manifests_dir=os.path.join(root, "manifests"),
            misc_dir=os.path.join(root, "misc"),
        )
        for p in vars(self.paths).values():
            os.makedirs(p, exist_ok=True)

    def event(self, *_args, **_kwargs):
        pass

    def update_state(self, *_args, **_kwargs):
        pass

    def save_json(self, kind, name, obj):
        folder = getattr(self.paths, f"{kind}s_dir", self.paths.responses_dir)
        path = os.path.join(folder, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path


class DummySettings:
    def __init__(self):
        self.retry = SimpleNamespace(circuit_breaker_failures=1, circuit_breaker_cooldown_s=1, max_attempts=1)
        self.model_cap_cache = SimpleNamespace(get=lambda _m: None)


class DummyClient:
    def __init__(self):
        self.last_payload = None

    def create_response(self, payload):
        self.last_payload = payload
        text = json.dumps(
            {
                "contract": "A3_FILE",
                "path": "services/a.py",
                "chunking": {
                    "max_lines": 500,
                    "chunk_index": 0,
                    "chunk_count": 1,
                    "has_more": False,
                    "next_chunk_index": None,
                },
                "content": "print('hello')\n",
            }
        )
        return {"id": "resp-1", "output_text": text, "usage": {"input_tokens": 1, "output_tokens": 1}}


class GenerateArtifactTests(unittest.TestCase):
    def make_worker(self):
        caps = SimpleNamespace(
            to_dict=lambda: {
                "supports_previous_response_id": True,
                "supports_temperature": True,
                "supports_file_search": False,
                "supports_vector_store": False,
                "supports_input_file": True,
            }
        )
        cfg = UiRunConfig(
            project="artifact-project",
            prompt="prompt",
            mode="GENERATE",
            send_as_c=False,
            model_default="gpt-main",
            model_a1="gpt-a1",
            model_a2="gpt-a2",
            model_a3="gpt-a3",
            response_id="resp-base",
            attached_file_ids=[],
            input_file_ids=[],
            attached_vector_store_ids=[],
            in_dir="",
            out_dir="",
            in_equals_out=False,
            versing=False,
            temperature=0.2,
            use_file_search=False,
            diag_windows_in=False,
            diag_windows_out=False,
            diag_ssh_in=False,
            diag_ssh_out=False,
            ssh_user="",
            ssh_host="",
            ssh_key="",
            ssh_password="",
            skip_paths=[],
            skip_exts=[],
            model_caps_default=caps,
            model_caps_a1=caps,
            model_caps_a2=caps,
            model_caps_a3=caps,
            resume_files=None,
            resume_prev_id=None,
        )
        base = os.path.join(os.getcwd(), "tmp_tests")
        os.makedirs(base, exist_ok=True)
        log = DummyLog(tempfile.mkdtemp(dir=base))
        w = RunWorker(cfg, DummySettings(), api_key="x", run_logger=log, receipt_db=None, price_table=None)
        w.log = log
        return w

    def test_artifacts_written_modules_structure_context_contracts(self):
        w = self.make_worker()
        files = [
            {"path": "services/user_service.py", "purpose": "users"},
            {"path": "models/user.py", "purpose": "entity"},
            {"path": "services/user_service.py", "purpose": "dup"},
        ]
        out = w._build_generate_artifacts(files)
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "modules.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "modules", "models.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "modules", "services.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "structure.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "context_bundle.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "interface_contracts.json")))
        self.assertIn("modules", out)
        self.assertIn("structure", out)

    def test_merge_deduped_stable_ordering(self):
        w = self.make_worker()
        merged = w._merge_module_structures(
            [
                {"module": "m2", "files": [{"path": "z/z.py", "purpose": "z"}, {"path": "a/a.py", "purpose": "a"}]},
                {"module": "m1", "files": [{"path": "a/a.py", "purpose": "dup"}, {"path": "b/b.py", "purpose": "b"}]},
            ]
        )
        self.assertEqual([f["path"] for f in merged["files"]], ["a/a.py", "b/b.py", "z/z.py"])

    def test_build_generate_artifacts_safe_fallback(self):
        w = self.make_worker()

        def boom(_files):
            raise RuntimeError("artifact-write-failed")

        w._build_generate_artifacts = boom
        out = w._build_generate_artifacts_safe([{"path": "services/a.py", "purpose": "a"}])
        self.assertEqual(out, {})

    def test_choose_handoff_when_artifacts_exist(self):
        w = self.make_worker()
        w._save_json_artifact("structure", {"files": []})
        w._save_json_artifact("context_bundle", {"project_summary": "x"})
        w._save_json_artifact("interface_contracts", {"interfaces": [], "types": []})
        loaded = w._load_a3_handoff_artifacts()
        self.assertEqual(w._choose_a3_generation_mode(loaded), "handoff")

    def test_choose_prev_chain_when_artifacts_missing(self):
        w = self.make_worker()
        loaded = w._load_a3_handoff_artifacts()
        self.assertEqual(w._choose_a3_generation_mode(loaded), "prev_chain")

    def test_parallel_generation_uses_thread_pool_executor(self):
        w = self.make_worker()
        w._generate_full_file = lambda *_args, **_kwargs: ("content", None, None)
        files = [{"path": "services/a.py", "purpose": "a"}, {"path": "models/b.py", "purpose": "b"}]
        with patch("kajovo.core.pipeline.concurrent.futures.ThreadPoolExecutor", wraps=concurrent.futures.ThreadPoolExecutor) as patched:
            out, errors = w._generate_full_files_parallel(
                client=object(),
                files=files,
                diag_file_ids=[],
                structure={"files": files},
                context_bundle={"project_summary": "x"},
                contracts_bundle={"interfaces": [], "types": []},
            )
        self.assertTrue(patched.called)
        self.assertEqual(len(out), 2)
        self.assertEqual(errors, [])

    def test_full_file_generation_contract_stays_non_diff(self):
        w = self.make_worker()
        client = DummyClient()
        captured = {}
        w._files_with_in_dir = lambda *_args, **_kwargs: []
        w._build_input_attachments = lambda *_args, **_kwargs: ([], [])
        w._append_io_reference_instructions = lambda instructions, *_args, **_kwargs: instructions
        w._append_io_reference = lambda prompt, *_args, **_kwargs: prompt
        w._with_diag_text = lambda txt: txt
        w._log_request_attachments = lambda *_args, **_kwargs: None
        w._log_api_action = lambda *_args, **_kwargs: None
        w._caps_for_step = lambda *_args, **_kwargs: {"supports_temperature": False}
        w.log.save_json = lambda *_args, **_kwargs: ""

        def fake_payload_base(stage, instructions, input_parts, prev_id):
            captured["instructions"] = instructions
            return {"model": "gpt-a3", "instructions": instructions, "input": input_parts}

        w._payload_base = fake_payload_base
        content, _resp = w._gen_file_chunks(client, prev_id="", contract="A3_FILE", path="services/a.py", action=None, diag_file_ids=[])
        self.assertIn("kompletní výsledné znění souboru (ne diff/patch)", captured["instructions"])
        self.assertEqual(content, "print('hello')\n")


if __name__ == "__main__":
    unittest.main()
