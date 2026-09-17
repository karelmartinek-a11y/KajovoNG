from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from ..openai_client import OpenAIClient
from ..openai_transport import SubmissionOutcomeUnknown
from ..utils import ensure_dir, ts_code

if TYPE_CHECKING:
    from .context import RunContext

def _build_diag_text(self: RunContext, files: list[str]) -> str:
    allowed_exts = {
        ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".md", ".csv",
        ".ini", ".cfg", ".conf", ".ps1", ".bat", ".cmd", ".sh"
    }
    max_total = 120_000
    max_per_file = 20_000
    parts: list[str] = []
    total = 0
    for fp in files:
        if total >= max_total:
            break
        ext = os.path.splitext(fp)[1].lower()
        if ext and ext not in allowed_exts:
            continue
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                content = f.read(max_per_file)
        except OSError as exc:
            self.log.exception("diagnostics.read", exc)
            continue
        if not content.strip():
            continue
        header = f"\n# {os.path.basename(fp)}\n"
        if total + len(header) >= max_total:
            break
        parts.append(header)
        total += len(header)
        if total + len(content) > max_total:
            content = content[: max_total - total]
        parts.append(content)
        total += len(content)
    return "".join(parts).strip()


def _write_diagnostics_json(self: RunContext, root: str, files: list[str]) -> str | None:
    if not root or not os.path.isdir(root):
        return None
    ensure_dir(self.log.paths.files_dir)
    json_path = os.path.join(self.log.paths.files_dir, f"diagnostics_{ts_code()}.json")
    try:
        text_exts = {
            ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".md", ".csv",
            ".ini", ".cfg", ".conf", ".ps1", ".bat", ".cmd", ".sh", ".reg"
        }
        total_size = 0
        payload: dict[str, Any] = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_root": root,
            "file_count": 0,
            "total_size_bytes": 0,
            "files": [],
        }
        for fp in files:
            rel = os.path.relpath(fp, root)
            ext = os.path.splitext(fp)[1].lower()
            try:
                size = os.path.getsize(fp)
            except OSError as exc:
                self.log.exception("diagnostics.stat", exc)
                size = None
            try:
                if ext in text_exts:
                    with open(fp, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    payload["files"].append({"path": rel, "encoding": "utf-8", "content": content, "bytes": size})
                else:
                    with open(fp, "rb") as f:
                        data = f.read()
                    b64 = base64.b64encode(data).decode("ascii")
                    payload["files"].append({"path": rel, "encoding": "base64", "content": b64, "bytes": size})
                payload["file_count"] += 1
                if size:
                    total_size += int(size)
            except OSError as exc:
                self.log.exception("diagnostics.archive_read", exc)
                continue
        payload["total_size_bytes"] = total_size
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return json_path
    except (OSError, ValueError, TypeError) as exc:
        self.log.exception("diagnostics.archive_write", exc)
        return None


def _maybe_collect_diagnostics(self: RunContext, client: OpenAIClient) -> tuple[list[str], str]:
    diag_file_ids: list[str] = []
    diag_text = ""
    if not (self.cfg.diag_windows_in or self.cfg.diag_ssh_in):
        return diag_file_ids, diag_text

    self._set(2, 0, "Sbírám diagnostická data…", stage="Diagnostika")
    diag_root = os.path.join(self.log.paths.manifests_dir, "diagnostics")
    ensure_dir(diag_root)
    diag_files: list[str] = []

    if self.cfg.diag_windows_in:
        from ..diagnostics.windows import collect_windows_diagnostics
        try:
            self._log_debug("Diagnostics IN: Windows collect...")
            folder, files = collect_windows_diagnostics(diag_root, on_line=self._log_debug)
            diag_files.extend(files)
            try:
                self.log.event("diagnostics.windows.collected", {"folder": folder, "count": len(files)})
            except Exception as evidence_error:
                logging.getLogger(__name__).warning(
                    "Zápis pomocné evidence selhal: %s", evidence_error
                )
        except Exception as e:
            try:
                self.log.exception("diagnostics.windows.failed", e)
            except Exception as evidence_error:
                logging.getLogger(__name__).warning(
                    "Zápis pomocné evidence selhal: %s", evidence_error
                )
            raise RuntimeError(f"Diagnostics Windows failed: {e}") from e

    if self.cfg.diag_ssh_in:
        from ..diagnostics.ssh import collect_ssh_diagnostics
        if not (self.cfg.ssh_host and self.cfg.ssh_user):
            raise RuntimeError("Diagnostics SSH failed: missing SSH host/user.")
        try:
            self._log_debug("Diagnostics IN: SSH collect...")
            folder, files = collect_ssh_diagnostics(
                diag_root,
                self.cfg.ssh_host,
                self.cfg.ssh_user,
                self.cfg.ssh_key,
                self.cfg.ssh_password,
                on_line=self._log_debug,
                timeout_s=900,
                pin=self.cfg.ssh_pin,
                pin_required=self.cfg.ssh_pin_required,
            )
            diag_files.extend(files)
            try:
                self.log.event("diagnostics.ssh.collected", {"folder": folder, "count": len(files)})
            except Exception as evidence_error:
                logging.getLogger(__name__).warning(
                    "Zápis pomocné evidence selhal: %s", evidence_error
                )
        except Exception as e:
            try:
                self.log.exception("diagnostics.ssh.failed", e)
            except Exception as evidence_error:
                logging.getLogger(__name__).warning(
                    "Zápis pomocné evidence selhal: %s", evidence_error
                )
            raise RuntimeError(f"Diagnostics SSH failed: {e}") from e

    diag_text = self._build_diag_text(diag_files)

    self._log_debug("Diagnostics IN: write JSON bundle...")
    json_path = self._write_diagnostics_json(diag_root, diag_files)
    self._diag_zip_path = json_path or ""
    if json_path:
        try:
            self._log_debug("Diagnostics IN: upload JSON to Files API...")
            up = client.upload_file(json_path, purpose='user_data')
            diag_file_ids.append(up["id"])
            self._remember_file_name(up["id"], os.path.basename(json_path))
            self.log.event("upload.diagnostics", {"local": json_path, "file_id": up["id"], "purpose": "user_data", "bytes": os.path.getsize(json_path)})
        except SubmissionOutcomeUnknown:
            raise
        except Exception as e:
            try:
                self.log.exception("upload.diagnostics", e)
                self.log.update_state({"diagnostics_delivery": {
                    "requested": True, "delivered": False, "error": str(e)
                }})
            except Exception as evidence_error:
                logging.getLogger(__name__).warning("Zápis evidence diagnostiky selhal: %s", type(evidence_error).__name__)
            raise RuntimeError(f"Diagnostická data se nepodařilo doručit do Files API: {e}") from e
    if not diag_file_ids:
        self.log.update_state({"diagnostics_delivery": {
            "requested": True, "delivered": False, "error": "Nevznikl diagnostický JSON bundle."
        }})
        raise RuntimeError("Požadovaná diagnostika nebyla doručena; pracovní požadavek nebyl odeslán.")
    self.log.update_state({"diagnostics_delivery": {
        "requested": True, "delivered": True, "file_ids": list(diag_file_ids)
    }})
    return diag_file_ids, diag_text


def _should_inline_diag_text(self: RunContext) -> bool:
    return False


def _with_diag_text(self: RunContext, text: str) -> str:
    if not self._should_inline_diag_text():
        return text
    if "DIAGNOSTICS (PARSED):" in text:
        return text
    return f"{text}\n\nDIAGNOSTICS (PARSED):\n{self._diag_text}"
