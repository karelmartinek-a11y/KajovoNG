import unittest
import os
import tempfile
from types import SimpleNamespace

from kajovo.core.pipeline import UiRunConfig, RunWorker


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
        # Best-effort create; ignore permission errors in sandboxed envs.
        for p in vars(self.paths).values():
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                pass

    def save_json(self, kind, name, obj):
        folder = getattr(self.paths, f"{kind}s_dir", self.paths.responses_dir) if hasattr(self.paths, f"{kind}s_dir") else self.paths.responses_dir
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}.json")
        import json

        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
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
        self.model_cap_cache = SimpleNamespace(get=lambda m: None)


class PipelineMultiModelTests(unittest.TestCase):
    def make_cfg(self):
        caps = SimpleNamespace(
            to_dict=lambda: {"supports_previous_response_id": True, "supports_temperature": True, "supports_file_search": False, "supports_vector_store": False, "supports_input_file": True},
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

    def test_model_for_stage(self):
        cfg = self.make_cfg()
        self.assertEqual(cfg.model_for_stage("A1"), "gpt-a1")
        self.assertEqual(cfg.model_for_stage("A2"), "gpt-a2")
        self.assertEqual(cfg.model_for_stage("A3"), "gpt-a3")
        self.assertEqual(cfg.model_for_stage("X"), "gpt-main")
        self.assertEqual(cfg.model, "gpt-main")
        self.assertIsInstance(cfg.model_caps, dict)

    def test_log_api_action_includes_model(self):
        w = self.make_worker()
        captured = {}

        def fake_event(_typ, data):
            captured["data"] = data

        w.log.event = fake_event
        w._log_api_action("A1", "send", {"foo": "bar"})
        self.assertIn("model", captured.get("data", {}))
        self.assertEqual(captured["data"]["model"], "gpt-a1")

    def test_attachments_snapshot_stage_caps(self):
        w = self.make_worker()

        def fake_caps(step):
            return {"supports_file_search": step == "A1", "supports_vector_store": False}

        w._caps_for_step = fake_caps
        snap = w._attachments_snapshot("A1", ["f1"], ["f2"], [], [], None)
        self.assertTrue(snap["supports_file_search"])
        snap2 = w._attachments_snapshot("A2", ["f1"], ["f2"], [], [], None)
        self.assertFalse(snap2["supports_file_search"])

    def test_modules_plan_and_merge(self):
        w = self.make_worker()
        files = [
            {"path": "services/a.py", "purpose": "s"},
            {"path": "models/b.py", "purpose": "m"},
            {"path": "services/c.py", "purpose": "s2"},
        ]
        plan = w._generate_modules_plan(files)
        self.assertEqual({m["name"] for m in plan["modules"]}, {"models", "services"})
        mod_struct = w._generate_module_structure("services", files)
        merged = w._merge_module_structures([mod_struct, w._generate_module_structure("models", files)])
        self.assertEqual(len(merged["files"]), 3)


if __name__ == "__main__":
    unittest.main()
