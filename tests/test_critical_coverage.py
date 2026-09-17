"""Coverage gate nesmí přijmout neúplná nebo podlimitní data."""

import pytest

from tools.check_critical_coverage import check, main


@pytest.fixture
def source(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "run.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def report(path="core/run.py", hit=8, total=10):
    return {"files": {path: {"summary": {"covered_lines": hit, "num_statements": total}}}}


def config(minimum=80, paths=None):
    return {"areas": {"runs": {"minimum": minimum, "paths": paths or ["core/*.py"]}}}


@pytest.mark.parametrize("path", ["core/run.py", "core\\run.py", "./core/run.py"])
def test_normalized_paths_and_exact_threshold(source, path):
    assert check(report(path), config(), source) == []


def test_absolute_path(source):
    assert check(report(str(source / "core" / "run.py")), config(), source) == []


def test_weighted_statements_not_average_of_percentages(source):
    (source / "core" / "other.py").write_text("x=2", encoding="utf-8")
    data = report(hit=1, total=1)
    data["files"].update(report("core/other.py", hit=79, total=99)["files"])
    assert check(data, config(), source) == []
    assert check(data, config(80.001), source)


def test_missing_source_fails(source):
    (source / "core" / "other.py").write_text("x=2", encoding="utf-8")
    assert check(report(), config(), source)


def test_unmatched_pattern_and_empty_statements_fail(source):
    assert check(report(), config(paths=["absent/*.py"]), source)
    assert check(report(hit=0, total=0), config(), source)


@pytest.mark.parametrize("limit", [-1, 101, float("nan"), True, "80"])
def test_invalid_threshold(source, limit):
    with pytest.raises(ValueError):
        check(report(), config(limit), source)


@pytest.mark.parametrize("data", [{}, {"files": {}}, report(hit=11)])
def test_invalid_report(source, data):
    with pytest.raises(ValueError):
        check(data, config(), source)


def test_missing_or_malformed_json_fails(tmp_path):
    path = tmp_path / "coverage.json"
    assert main([str(path)]) == 1
    path.write_text("{", encoding="utf-8")
    assert main([str(path)]) == 1
