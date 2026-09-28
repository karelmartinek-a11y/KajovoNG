"""Plán etap známých při vstupu do executorů; větvení může přidat další etapy."""


def run_progress_plan(cfg):
    steps = [
        "UI_VALIDATE",
        "RUN_START",
        "RUN_CHECK",
        "RUN_CONFIG",
        "RUN_INPUT",
        "Lokální validace",
        "RUN_RUNTIME",
    ]
    if cfg.mode in {"GENERATE", "MODIFY"}:
        prefix = "A" if cfg.mode == "GENERATE" else "B"
        steps.extend(prefix + suffix for suffix in ("0R", "1", "2_SPINE", "2_DETAIL", "2"))
        if cfg.maximum_quality:
            steps.append(prefix + "2Q")
        if not cfg.stop_after_plan:
            steps.extend(("TARGET_SCOPE", "TARGET_READY"))
            if cfg.send_as_c:
                steps.extend(
                    (
                        "RESOURCE_TARGET",
                        "OUTPUT_VALIDATE",
                        "BATCH_PREPARE",
                        "BATCH_VALIDATE",
                        "BATCH_UPLOAD",
                        "BATCH_SUBMIT",
                        "BATCH_STATE",
                    )
                )
            else:
                steps.extend(
                    (
                        prefix + "3",
                        "RESOURCE_TARGET",
                        "OUTPUT_VALIDATE",
                        "Ukládání",
                    )
                )
    elif cfg.mode == "QA":
        steps.extend(("QA_INPUT", "QA_RESPONSE", "QA_VALIDATION"))
    elif cfg.mode == "QFILE":
        steps.append("QFILE" if cfg.qfile_output_path else "QFILE_PLAN")
        if cfg.qfile_output_path:
            steps.append("Ukládání")
    return (*steps, "RUN_FINALIZE")
