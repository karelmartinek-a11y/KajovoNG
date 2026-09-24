"""Omezená cache metadat; obsah odpovědí se načítá až při otevření detailu.

Volá se výhradně z lokálních pracovníků Operations. Cache není evidence ani
oprávnění ke spuštění: launcher před použitím znovu validuje zdroj.
"""

from collections import OrderedDict
from stat import S_ISREG
from threading import RLock

from kajovo.core.contracts import ContractError
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.safe_config import redact_evidence


def payload(adapter, *, detail=True):
    value = {
        "summary": adapter.run_record(), "steps": adapter.steps(),
        "artifacts": adapter.artifacts(), "lineage": adapter.lineage(),
        "checkpoints": adapter.checkpoints(),
    }
    if detail:
        value.update(requests=adapter.requests(), responses=adapter.responses(),
                     validations=adapter.validations(), events=adapter.events())
    # Legacy files remain immutable; only the UI/read projection is redacted.
    return redact_evidence(value)


class HistoryData:
    def __init__(self, capacity=12):
        self.capacity = capacity
        self._cache = OrderedDict()
        self._lock = RLock()
        self._bytes = 0
        self._byte_limit = 64 * 1024 * 1024

    def read(self, directory, *, detail=False):
        adapter = LegacyRunAdapter(directory)
        # Kontrola velikosti i času každého souboru zachytí přepsání i odstranění.
        entries = []
        for path in adapter.root.rglob("*"):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if S_ISREG(stat.st_mode):
                entries.append((str(path.relative_to(adapter.root)), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        signature = tuple(sorted(entries))
        key = (str(adapter.root), detail)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == signature:
                self._cache.move_to_end(key)
                return adapter, cached[1], cached[2]
        value = payload(adapter, detail=detail)
        state = redact_evidence(adapter.state())
        if detail and value["summary"].get("mode") == "MODIFY":
            from kajovo.core.runlog import verified_output_evidence
            out_dir = state.get("out_dir") or (state.get("ui_state") or {}).get("out_dir")
            evidence = verified_output_evidence(adapter.root, out_dir)
            claimed = set(state.get("completed_hashes") or {}) | set((state.get("ui_state") or {}).get("skip_paths") or [])
            state["_verified_skip_paths"] = [row["path"] for row in evidence if row.get("path") in claimed]
        with self._lock:
            previous = self._cache.pop(key, None)
            if previous:
                self._bytes -= previous[3]
            # Odhad z velikostí zdrojových JSON souborů je konzervativní;
            # velký detail se může zobrazit, ale nezůstává v cache.
            size = sum(row[1] * 6 for row in signature if row[0].endswith((".json", ".jsonl")))
            if size <= self._byte_limit:
                self._cache[key] = (signature, value, state, size)
                self._bytes += size
                while len(self._cache) > self.capacity or self._bytes > self._byte_limit:
                    _, evicted = self._cache.popitem(last=False)
                    self._bytes -= evicted[3]
        return adapter, value, state


def checked_checkpoints(adapter, records, artifacts):
    # Kontrola integrity pracuje s kanonickými daty, nikoli redigovanou UI kopií.
    artifacts = adapter.artifacts()
    responses = adapter.responses() if any(row.get("required_response_ids") for row in records) else []
    checked = []
    hashes = {}
    for record in records:
        row = dict(record)
        try:
            canonical = adapter.bundle.validate_checkpoint(str(row.get("checkpoint_id") or ""),
                                               artifacts=artifacts, responses=responses, hash_cache=hashes)
            state = canonical.get("state_snapshot") or {}
            ui = state.get("ui_state") or {}
            if ui.get("in_dir") and not (state.get("input_archive") or {}).get("complete"):
                raise ValueError("Není doložen úplný archiv vstupního adresáře.")
            if state.get("preparation_snapshot"):
                from kajovo.core.delivery_preparation import validate_preparation_snapshot
                validate_preparation_snapshot(state["preparation_snapshot"], ui.get("mode") or state.get("mode"),
                                              bool(ui.get("maximum_quality")))
            row["_availability_valid"] = True
        except (ValueError, OSError, KeyError, ContractError) as error:
            row["_availability_valid"] = False
            row["_availability_error"] = str(error)
        checked.append(row)
    return checked


def unique_responses(records):
    """Transportní kopie stejné odpovědi nesmí násobit spotřebu ani lidský text."""
    unique = {}
    for row in records:
        key = row.get("response_id") or row.get("response_record_id")
        if key:
            unique[key] = row
    return list(unique.values())
