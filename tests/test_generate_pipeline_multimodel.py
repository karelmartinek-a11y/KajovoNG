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
        self.root = root
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

    def save_json(self, kind, name, obj):
        folder = getattr(self.paths, f"{kind}s_dir", self.paths.responses_dir)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, default=str)
        return path

    def event(self, *_args, **_kwargs):
        pass

    def update_state(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


class DummySettings:
    def __init__(self):
        self.retry = SimpleNamespace(circuit_breaker_failures=1, circuit_breaker_cooldown_s=1, max_attempts=1)
        self.model_cap_cache = SimpleNamespace(get=lambda _m: None)


class DummyClient:
    def __init__(self):
        self.payloads = []

    def create_response(self, payload):
        self.payloads.append(payload)
        return {
            "id": "resp-file-1",
            "status": "completed",
            "output_text": json.dumps(
                {
                    "contract": "A3_FILE",
                    "path": "services/user_service.py",
                    "chunking": {
                        "max_lines": 500,
                        "chunk_index": 0,
                        "chunk_count": 1,
                        "has_more": False,
                        "next_chunk_index": None,
                    },
                    "content": "# full file\nclass UserService:\n    pass\n",
                }
            ),
        }


class PipelineMultiModelTests(unittest.TestCase):
    def make_cfg(self):
        caps = SimpleNamespace(
            to_dict=lambda: {
                "supports_previous_response_id": True,
                "supports_temperature": True,
                "supports_file_search": False,
                "supports_vector_store": False,
                "supports_input_file": True,
            },
            supports_previous_response_id=True,
            supports_temperature=True,
            supports_file_search=False,
            supports_vector_store=False,
            supports_input_file=True,
        )
        return UiRunConfig(
            project="p",
            prompt="",
            mode="GENERATE",
            send_as_c=False,
            model_default="gpt-main",
            model_a1="gpt-a1",
            model_a2="gpt-a2",
            model_a3="gpt-a3",
            response_id="",
            attached_file_ids=[],
            input_file_ids=[],
            attached_vector_store_ids=[],
            in_dir="",
            out_dir="",
            in_equals_out=False,
            versing=False,
            temperature=0.2,
            use_file_search=True,
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

    def make_worker(self):
        cfg = self.make_cfg()
        base = os.path.join(os.getcwd(), "tmp_tests")
        os.makedirs(base, exist_ok=True)
        log = DummyLog(tempfile.mkdtemp(dir=base))
        worker = RunWorker(cfg, DummySettings(), api_key="x", run_logger=log, receipt_db=None, price_table=None)
        worker.log = log
        return worker

    def test_model_for_stage_and_caps_for_stage(self):
        cfg = self.make_cfg()
        self.assertEqual(cfg.model_for_stage("A1"), "gpt-a1")
        self.assertEqual(cfg.model_for_stage("A2"), "gpt-a2")
        self.assertEqual(cfg.model_for_stage("A3"), "gpt-a3")
        self.assertEqual(cfg.model_for_stage("X"), "gpt-main")
        self.assertEqual(cfg.model, "gpt-main")
        self.assertIsInstance(cfg.model_caps, dict)
        self.assertTrue(cfg.caps_for_stage("A2").supports_previous_response_id)

    def test_attachments_snapshot_caps_object_regression(self):
        w = self.make_worker()
        w.cfg.caps_for_stage = lambda _stage: SimpleNamespace(supports_file_search=False, supports_vector_store=False)
        w._caps_for_step = lambda _stage: {"supports_file_search": True, "supports_vector_store": True}
        snap = w._attachments_snapshot("A2", ["f1"], ["f2"], [], ["vs1"], [{"type": "file_search"}])
        self.assertTrue(snap["supports_file_search"])
        self.assertTrue(snap["supports_vector_store"])

    def test_a2_handoff_artifacts_created(self):
        w = self.make_worker()
        files = [
            {"path": "services/user_service.py", "purpose": "users"},
            {"path": "models/user.py", "purpose": "entity"},
            {"path": "services/user_service.py", "purpose": "dup"},
        ]
        artifacts = w._create_a3_handoff_artifacts(files)

        self.assertIn("modules", artifacts)
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "modules.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "modules", "services.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "modules", "models.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "structure.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "context_bundle.json")))
        self.assertTrue(os.path.isfile(os.path.join(w.log.paths.responses_dir, "interface_contracts.json")))

    def test_merge_module_structures_dedupes_and_sorts(self):
        w = self.make_worker()
        merged = w._merge_module_structures(
            [
                {"module": "services", "files": [{"path": "services/b.py", "purpose": "b"}]},
                {
                    "module": "models",
                    "files": [
                        {"path": "models/a.py", "purpose": "a"},
                        {"path": "services/b.py", "purpose": "dup"},
                    ],
                },
            ]
        )
        self.assertEqual([f["path"] for f in merged["files"]], ["models/a.py", "services/b.py"])

    def test_a3_uses_structure_handoff_when_artifacts_exist(self):
        w = self.make_worker()
        w.cfg.resume_files = [{"path": "services/user_service.py", "purpose": "users"}]
        w.cfg.resume_prev_id = "prev-resp"
        logs = []
        w._log_debug = logs.append
        called = {"parallel": False}

        def fake_parallel(_client, _files, _diag, _ctx, _contracts, _structure):
            called["parallel"] = True
            return ([{"path": "services/user_service.py", "content": "full", "purpose": "users"}], [])

        w._generate_full_files_parallel = fake_parallel
        w._save_out_files = lambda files: {f["path"]: "saved" for f in files}
        w._record_receipt = lambda *args, **kwargs: None

        out = w._run_a_generate(client=SimpleNamespace(), diag_file_ids=[], base_prev_id=None)
        self.assertTrue(called["parallel"])
        self.assertIn("A3 using structure handoff", logs)
        self.assertEqual(out["response_id"], "prev-resp")

    def test_a3_falls_back_to_previous_response_chain_when_artifacts_missing(self):
        w = self.make_worker()
        w.cfg.resume_files = [{"path": "services/user_service.py", "purpose": "users"}]
        w.cfg.resume_prev_id = "prev-resp"
        logs = []
        w._log_debug = logs.append
        w._create_a3_handoff_artifacts = lambda _files: (_ for _ in ()).throw(RuntimeError("disk issue"))
        calls = []

        def fake_gen(*_args, **kwargs):
            calls.append(kwargs.get("prev_id"))
            return "full", "next-resp"

        w._gen_file_chunks = fake_gen
        w._save_out_files = lambda files: {f["path"]: "saved" for f in files}
        w._record_receipt = lambda *args, **kwargs: None

        w._run_a_generate(client=SimpleNamespace(), diag_file_ids=[], base_prev_id=None)
        self.assertEqual(calls[0], "prev-resp")
        self.assertIn("A3 using previous_response chain", logs)

    def test_parallel_path_uses_thread_pool_executor(self):
        w = self.make_worker()
        w._generate_full_file = lambda *_args, **_kwargs: ("content", None, None, "services/a.py", "x")
        files = [{"path": "services/a.py", "purpose": "x"}]

        with patch("kajovo.core.pipeline.concurrent.futures.ThreadPoolExecutor", wraps=concurrent.futures.ThreadPoolExecutor) as patched:
            results, errors = w._generate_full_files_parallel(
                client=SimpleNamespace(),
                files=files,
                diag_file_ids=[],
                context_bundle={},
                contracts_bundle={},
                structure={"files": files},
            )
        self.assertTrue(patched.called)
        self.assertEqual(len(results), 1)
        self.assertEqual(errors, [])

    def test_generate_full_file_handoff_uses_empty_prev_id(self):
        w = self.make_worker()
        seen = {}

        def fake_gen(_client, prev_id, **_kwargs):
            seen["prev_id"] = prev_id
            return "full", "r-1"

        w._gen_file_chunks = fake_gen
        content, _rid, err, path, _purpose = w._generate_full_file(
            client=SimpleNamespace(),
            file_spec={"path": "services/a.py", "purpose": "x"},
            diag_file_ids=[],
            context_bundle={"project_summary": "p"},
            contracts_bundle={"interfaces": []},
            structure_map={"services/a.py": {"path": "services/a.py"}},
        )
        self.assertIsNone(err)
        self.assertEqual(path, "services/a.py")
        self.assertEqual(content, "full")
        self.assertEqual(seen["prev_id"], "")

    def test_file_generation_contract_full_file_only_instruction(self):
        w = self.make_worker()
        client = DummyClient()
        w._build_input_attachments = lambda _client, _ids: ([], [])

        content, _resp = w._gen_file_chunks(
            client=client,
            prev_id="",
            contract="A3_FILE",
            path="services/user_service.py",
            action=None,
            diag_file_ids=[],
            tools=None,
        )
        self.assertIn("full file", content)
        self.assertIn("kompletní výsledné znění souboru", client.payloads[0].get("instructions", ""))


if __name__ == "__main__":
    unittest.main()
