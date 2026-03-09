
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from kajovo.core.pipeline import RunWorker, UiRunConfig

class DummySettings:
    def __init__(self):
        self.retry = SimpleNamespace(circuit_breaker_failures=1, circuit_breaker_cooldown_s=1, max_attempts=1)
        self.model_cap_cache = SimpleNamespace(get=lambda _m: None)

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
            json.dump(obj, f)

class ContextQualityTests(unittest.TestCase):
    def _make_worker(self):
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
        cfg = UiRunConfig(
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
            temperature=0.0,
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
        tmp = tempfile.mkdtemp()
        log = DummyLog(tmp)
        worker = RunWorker(cfg, DummySettings(), api_key="x", run_logger=log, receipt_db=None, price_table=None)
        worker.log = log
        return worker

    def test_context_bundle_quality(self):
        w = self._make_worker()
        structure = {
            "files": [
                {"path": "services/user_service.py", "purpose": "service user management"},
                {"path": "models/user.py", "purpose": "user model"},
                {"path": "repositories/user_repo.py", "purpose": "persist users"},
                {"path": "controllers/user_controller.py", "purpose": "http api"},
                {"path": "services/order_service.py", "purpose": "service order handling"},
                {"path": "models/order.py", "purpose": "order model"},
                {"path": "repositories/order_repo.py", "purpose": "persist orders"},
            ]
        }
        modules_plan = w._generate_modules_plan(structure["files"])
        bundle = w._build_context_bundle(modules_plan, structure)

        self.assertNotEqual(bundle["project_summary"], "Software project")
        self.assertTrue(all(m["purpose"] != "module scaffold" for m in bundle["modules"]))
        self.assertTrue(bundle["shared_types"])
        self.assertTrue(bundle["public_interfaces"])

    def test_interface_contract_methods(self):
        w = self._make_worker()
        structure = {
            "files": [
                {"path": "services/billing_service.py", "purpose": "billing"},
                {"path": "controllers/billing_controller.py", "purpose": "billing api"},
            ]
        }
        contracts = w._build_interface_contracts(structure)
        self.assertTrue(any(i["methods"] for i in contracts["interfaces"]))
        contracts2 = w._build_interface_contracts(structure)
        self.assertEqual(contracts, contracts2)
