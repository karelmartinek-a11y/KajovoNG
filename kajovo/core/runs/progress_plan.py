"""Plán etap známých při vstupu do executorů; větvení může přidat další etapy."""


def run_progress_plan(cfg):
    steps = ["RUN_CHECK", "RUN_INPUT", "Lokální validace", "RUN_RUNTIME"]
    if cfg.mode in {"GENERATE", "MODIFY"}:
        prefix = "A" if cfg.mode == "GENERATE" else "B"
        steps.extend(prefix + suffix for suffix in ("0R", "1", "2_SPINE", "2_DETAIL", "2"))
        if cfg.maximum_quality:
            steps.append(prefix + "2Q")
        if not cfg.stop_after_plan:
            steps.append("BATCH_SUBMIT" if cfg.send_as_c else prefix + "3")
            if not cfg.send_as_c:
                steps.append("Ukládání")
    elif cfg.mode == "QA":
        steps.extend(("QA_INPUT", "QA_RESPONSE", "QA_VALIDATION"))
    elif cfg.mode == "QFILE":
        steps.append("QFILE" if cfg.qfile_output_path else "QFILE_PLAN")
        if cfg.qfile_output_path:
            steps.append("Ukládání")
    return tuple([*steps, "RUN_FINALIZE"])
