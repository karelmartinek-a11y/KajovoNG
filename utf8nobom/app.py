from __future__ import annotations

import json
import os
import queue
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import END, BOTH, DISABLED, NORMAL, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Iterable, List, Sequence


TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".env",
    ".gitignore",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".log",
    ".md",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

MOJIBAKE_MARKERS = ("Ã", "Å", "Ä", "Â", "Ĺ", "Ă", "â€", "â„", "â€™", "â€“", "�")


@dataclass(frozen=True)
class TargetSpec:
    path: Path


@dataclass(frozen=True)
class FileTask:
    kind: str
    path: Path
    size: int


@dataclass(frozen=True)
class ZipEntryTask:
    zip_path: Path
    entry_name: str
    size: int


@dataclass(frozen=True)
class ScanPlan:
    directories: List[TargetSpec]
    file_tasks: List[FileTask]
    zip_entry_tasks: List[ZipEntryTask]
    total_units: int


def now_code() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def is_zip_path(path: Path) -> bool:
    return path.suffix.lower() == ".zip"


def detect_text_bytes(data: bytes, suffix: str) -> bool:
    if suffix.lower() in TEXT_EXTENSIONS:
        return True
    if not data:
        return True
    if b"\x00" in data:
        return False
    printable = sum((32 <= byte <= 126) or byte in (9, 10, 13) for byte in data)
    return printable / len(data) >= 0.75


def score_text_quality(text: str) -> int:
    score = 0
    score -= text.count("\ufffd") * 40
    score -= sum(text.count(marker) for marker in MOJIBAKE_MARKERS) * 8
    score += sum(text.count(ch) for ch in "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ") * 3
    score += sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    return score


def repair_mojibake_text(text: str) -> str:
    best = text
    best_score = score_text_quality(text)
    queue_texts = [text]
    seen = {text}
    encodings = ("cp1250", "cp1252", "latin1")
    for _ in range(2):
        current_round: List[str] = []
        for item in queue_texts:
            for encoding in encodings:
                try:
                    candidate = item.encode(encoding).decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                current_round.append(candidate)
                score = score_text_quality(candidate)
                if score > best_score:
                    best = candidate
                    best_score = score
        if not current_round:
            break
        queue_texts = current_round
    return best


def normalize_text_bytes(data: bytes) -> tuple[bytes, bool]:
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "cp1252", "latin1"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        decoded = data.decode("utf-8", errors="replace")
    repaired = repair_mojibake_text(decoded)
    normalized = repaired.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    changed = normalized != data
    return normalized, changed


class ProgressTracker:
    def __init__(self, total_units: int, callback: Callable[[dict], None]) -> None:
        self.total_units = max(total_units, 1)
        self._callback = callback
        self._done_units = 0
        self._start = time.time()

    def advance(self, units: int, phase: str, detail: str) -> None:
        self._done_units = min(self.total_units, self._done_units + max(units, 1))
        elapsed = time.time() - self._start
        percent = (self._done_units / self.total_units) * 100
        if self._done_units <= 0:
            eta = 0.0
        else:
            eta = max(0.0, elapsed * (self.total_units / self._done_units - 1))
        self._callback(
            {
                "type": "progress",
                "phase": phase,
                "detail": detail,
                "percent": percent,
                "elapsed": elapsed,
                "eta": eta,
                "done_units": self._done_units,
                "total_units": self.total_units,
            }
        )


class RunLogger:
    def __init__(self, backup_dir: Path, run_id: str) -> None:
        self.run_dir = backup_dir / f"utf8nobom_{run_id}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "run_log.jsonl"

    def write(self, event: str, **payload: object) -> None:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **payload,
        }
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate_input_paths(directory_values: Sequence[str], backup_value: str) -> tuple[List[TargetSpec], Path]:
    targets: List[TargetSpec] = []
    seen: set[Path] = set()
    for raw in directory_values:
        value = raw.strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Adresář neexistuje: {path}")
        if path in seen:
            continue
        seen.add(path)
        targets.append(TargetSpec(path=path))
    if not targets:
        raise ValueError("Zadejte alespoň jeden adresář ke kontrole.")

    backup_dir = Path(backup_value.strip()).expanduser().resolve()
    if not backup_value.strip():
        raise ValueError("Zadejte adresář pro backup.")
    if not backup_dir.exists() or not backup_dir.is_dir():
        raise ValueError(f"Backup adresář neexistuje: {backup_dir}")
    for target in targets:
        try:
            backup_dir.relative_to(target.path)
        except ValueError:
            continue
        raise ValueError("Backup adresář nesmí být uvnitř kontrolovaného adresáře.")
    return targets[:5], backup_dir


def build_scan_plan(targets: Sequence[TargetSpec]) -> ScanPlan:
    file_tasks: List[FileTask] = []
    zip_entry_tasks: List[ZipEntryTask] = []
    total_units = 0

    for target in targets:
        for root, _, files in os.walk(target.path):
            for name in files:
                path = Path(root) / name
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 1
                units = max(size, 1)
                file_tasks.append(FileTask(kind="zip" if is_zip_path(path) else "file", path=path, size=size))
                total_units += units * 2
                if is_zip_path(path):
                    try:
                        with zipfile.ZipFile(path, "r") as archive:
                            for info in archive.infolist():
                                if info.is_dir():
                                    continue
                                zip_entry_tasks.append(
                                    ZipEntryTask(zip_path=path, entry_name=info.filename, size=max(info.file_size, 1))
                                )
                                total_units += max(info.file_size, 1)
                    except zipfile.BadZipFile:
                        total_units += units
    return ScanPlan(list(targets), file_tasks, zip_entry_tasks, max(total_units, 1))


def copy_directory_for_backup(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def make_zip_from_directory(source: Path, target_zip: Path) -> None:
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(source):
            for name in files:
                path = Path(root) / name
                rel_path = path.relative_to(source)
                archive.write(path, rel_path.as_posix())


def safe_zip_members(infos: Iterable[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    safe_items: list[zipfile.ZipInfo] = []
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            continue
        safe_items.append(info)
    return safe_items


def rewrite_zip_if_needed(path: Path, tracker: ProgressTracker, logger: RunLogger) -> tuple[int, int]:
    changed_entries = 0
    processed_entries = 0
    try:
        with zipfile.ZipFile(path, "r") as source:
            infos = safe_zip_members(source.infolist())
            staged: list[tuple[zipfile.ZipInfo, bytes]] = []
            changed = False
            for info in infos:
                if info.is_dir():
                    staged.append((info, b""))
                    continue
                raw = source.read(info.filename)
                processed_entries += 1
                if detect_text_bytes(raw[:4096], Path(info.filename).suffix):
                    normalized, entry_changed = normalize_text_bytes(raw)
                else:
                    normalized, entry_changed = raw, False
                if entry_changed:
                    changed = True
                    changed_entries += 1
                    logger.write("zip_entry_fixed", zip_path=str(path), entry=info.filename)
                staged.append((info, normalized))
                tracker.advance(max(info.file_size, 1), "ZIP", f"{path.name} -> {info.filename}")
    except zipfile.BadZipFile:
        logger.write("zip_invalid", zip_path=str(path))
        return 0, 0

    if not changed:
        return processed_entries, 0

    fd, tmp_name = tempfile.mkstemp(prefix=path.stem + "_", suffix=".zip", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w") as target:
            for info, payload in staged:
                clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.comment = info.comment
                clone.create_system = info.create_system
                clone.create_version = info.create_version
                clone.extract_version = info.extract_version
                clone.external_attr = info.external_attr
                clone.flag_bits = info.flag_bits
                clone.internal_attr = info.internal_attr
                target.writestr(clone, payload)
        os.replace(tmp_path, path)
        logger.write("zip_rewritten", zip_path=str(path), changed_entries=changed_entries)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return processed_entries, changed_entries


def process_regular_file(path: Path, tracker: ProgressTracker, logger: RunLogger) -> bool:
    raw = path.read_bytes()
    tracker.advance(max(len(raw), 1), "Sken", str(path))
    if not detect_text_bytes(raw[:4096], path.suffix):
        tracker.advance(max(len(raw), 1), "Soubor", f"Přeskočen binární soubor: {path}")
        return False
    normalized, changed = normalize_text_bytes(raw)
    if changed:
        path.write_bytes(normalized)
        logger.write("file_fixed", path=str(path), bytes=len(raw))
    tracker.advance(max(len(raw), 1), "Soubor", str(path))
    return changed


def run_job(
    targets: Sequence[TargetSpec],
    backup_dir: Path,
    event_queue: queue.Queue[dict],
) -> None:
    run_id = now_code()
    logger = RunLogger(backup_dir, run_id)

    def emit(payload: dict) -> None:
        event_queue.put(payload)

    try:
        logger.write("run_started", directories=[str(item.path) for item in targets], backup_dir=str(backup_dir))
        emit({"type": "log", "message": f"Run {run_id} zahájen."})
        emit({"type": "status", "message": "Připravuji plán práce..."})

        plan = build_scan_plan(targets)
        tracker = ProgressTracker(plan.total_units, emit)
        logger.write(
            "plan_built",
            total_units=plan.total_units,
            file_count=len(plan.file_tasks),
            zip_entry_count=len(plan.zip_entry_tasks),
        )
        emit(
            {
                "type": "log",
                "message": (
                    f"Nalezeno {len(plan.file_tasks)} souborů a {len(plan.zip_entry_tasks)} položek uvnitř ZIPů."
                ),
            }
        )

        for target in plan.directories:
            emit({"type": "status", "message": f"Vytvářím backup pro {target.path}..."})
            safe_name = target.path.name or "adresar"
            backup_copy = logger.run_dir / f"{safe_name}_copy"
            backup_zip = logger.run_dir / f"{safe_name}.zip"
            copy_directory_for_backup(target.path, backup_copy)
            make_zip_from_directory(backup_copy, backup_zip)
            copied_size = sum(max(task.size, 1) for task in plan.file_tasks if task.path.is_relative_to(target.path))
            tracker.advance(max(copied_size, 1), "Backup", str(target.path))
            logger.write("backup_created", source=str(target.path), copy=str(backup_copy), zip=str(backup_zip))
            emit({"type": "log", "message": f"Backup hotov: {backup_zip}"})

        fixed_files = 0
        fixed_zips = 0
        for task in plan.file_tasks:
            if task.kind == "zip":
                emit({"type": "status", "message": f"Kontroluji ZIP {task.path}..."})
                _, changed_entries = rewrite_zip_if_needed(task.path, tracker, logger)
                if changed_entries:
                    fixed_zips += 1
                    emit({"type": "log", "message": f"Opraven ZIP: {task.path} ({changed_entries} položek)"})
                else:
                    emit({"type": "log", "message": f"ZIP bez změn: {task.path}"})
                tracker.advance(max(task.size, 1), "ZIP", str(task.path))
                continue

            emit({"type": "status", "message": f"Kontroluji soubor {task.path}..."})
            if process_regular_file(task.path, tracker, logger):
                fixed_files += 1
                emit({"type": "log", "message": f"Opraven soubor: {task.path}"})

        logger.write("run_completed", fixed_files=fixed_files, fixed_zips=fixed_zips)
        emit(
            {
                "type": "done",
                "message": (
                    f"Hotovo. Opravené soubory: {fixed_files}, upravené ZIPy: {fixed_zips}. "
                    f"Backup a log: {logger.run_dir}"
                ),
            }
        )
    except Exception as exc:
        logger.write("run_failed", error=str(exc))
        emit({"type": "error", "message": f"Chyba: {exc}"})


class Utf8NoBomApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("UTF-8 bez BOM")
        self.root.geometry("980x720")
        self.root.minsize(860, 620)

        self.directory_vars = [StringVar() for _ in range(5)]
        self.backup_var = StringVar()
        self.status_var = StringVar(value="Připraveno.")
        self.summary_var = StringVar(value="0 % | uplynulo 0 s | ETA 0 s")
        self.queue: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.root.after(150, self._drain_queue)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=BOTH, expand=True)

        ttk.Label(container, text="Adresáře ke kontrole", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            container,
            text="Vyplňte 1 až 5 adresářů. Program je nejdřív zazálohuje a potom opraví textové soubory i ZIPy.",
        ).pack(anchor="w", pady=(4, 12))

        for index, variable in enumerate(self.directory_vars, start=1):
            row = ttk.Frame(container)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=f"Adresář {index}", width=14).pack(side="left")
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(0, 8))
            ttk.Button(row, text="Vybrat", command=lambda var=variable: self._pick_directory(var)).pack(side="left")

        backup_row = ttk.Frame(container)
        backup_row.pack(fill="x", pady=(14, 6))
        ttk.Label(backup_row, text="Backup", width=14).pack(side="left")
        ttk.Entry(backup_row, textvariable=self.backup_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(backup_row, text="Vybrat", command=lambda: self._pick_directory(self.backup_var)).pack(side="left")

        action_row = ttk.Frame(container)
        action_row.pack(fill="x", pady=(14, 8))
        self.start_button = ttk.Button(action_row, text="Spustit kontrolu", command=self._start)
        self.start_button.pack(side="left")

        self.progress = ttk.Progressbar(container, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))

        ttk.Label(container, textvariable=self.status_var).pack(anchor="w")
        ttk.Label(container, textvariable=self.summary_var).pack(anchor="w", pady=(0, 10))

        self.log_widget = ScrolledText(container, height=24, state=DISABLED, font=("Consolas", 10))
        self.log_widget.pack(fill=BOTH, expand=True)

    def _pick_directory(self, variable: StringVar) -> None:
        chosen = filedialog.askdirectory(parent=self.root)
        if chosen:
            variable.set(chosen)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.configure(state=NORMAL)
        self.log_widget.insert(END, f"{timestamp} | {message}\n")
        self.log_widget.see(END)
        self.log_widget.configure(state=DISABLED)

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state=DISABLED if running else NORMAL)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            targets, backup_dir = validate_input_paths(
                [variable.get() for variable in self.directory_vars],
                self.backup_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Neplatné zadání", str(exc), parent=self.root)
            return

        self.progress["value"] = 0
        self.status_var.set("Spouštím úlohu...")
        self.summary_var.set("0 % | uplynulo 0 s | ETA 0 s")
        self._append_log("Zahajuji kontrolu a opravu kódování.")
        self._set_running(True)
        self.worker = threading.Thread(target=run_job, args=(targets, backup_dir, self.queue), daemon=True)
        self.worker.start()

    def _drain_queue(self) -> None:
        try:
            while True:
                event = self.queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_queue)

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            percent = float(event.get("percent", 0.0))
            elapsed = int(event.get("elapsed", 0.0))
            eta = int(event.get("eta", 0.0))
            phase = str(event.get("phase", "Běh"))
            detail = str(event.get("detail", ""))
            self.progress["value"] = max(0.0, min(percent, 100.0))
            self.status_var.set(f"{phase}: {detail}")
            self.summary_var.set(
                f"{percent:.1f} % | hotovo {event.get('done_units', 0)} / {event.get('total_units', 0)} | "
                f"uplynulo {elapsed} s | ETA {eta} s"
            )
            return
        if event_type == "status":
            self.status_var.set(str(event.get("message", "")))
            return
        if event_type == "log":
            self._append_log(str(event.get("message", "")))
            return
        if event_type == "done":
            self._append_log(str(event.get("message", "")))
            self.status_var.set("Hotovo.")
            self.progress["value"] = 100
            self.summary_var.set("100 % | úloha dokončena")
            self._set_running(False)
            messagebox.showinfo("Hotovo", str(event.get("message", "")), parent=self.root)
            return
        if event_type == "error":
            self._append_log(str(event.get("message", "")))
            self.status_var.set("Běh skončil chybou.")
            self._set_running(False)
            messagebox.showerror("Chyba", str(event.get("message", "")), parent=self.root)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = Utf8NoBomApp()
    app.run()
