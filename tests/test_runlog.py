import pytest


def test_logger_rejects_existing_run_and_distinguishes_truncated_names(tmp_path):
    from kajovo.core.runlog import RunLogger
    logger = RunLogger(str(tmp_path), "RUN_090920261200_TEST")
    first = logger.save_json("responses", "x" * 160 + "one", {"value": 1})
    second = logger.save_json("responses", "x" * 160 + "two", {"value": 2})
    assert first != second
    with pytest.raises(FileExistsError):
        RunLogger(str(tmp_path), logger.run_id)
