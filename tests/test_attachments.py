from unittest.mock import Mock

import pytest

from kajovo.core.compat import MAX_INPUT_FILE_BYTES, is_compatible_path, validate_input_file_sizes
from kajovo.core.openai_client import OpenAIClient


@pytest.mark.parametrize("name,accepted", [("code.go", True), ("slides.pptx", True),
                                         ("code.CPP", True), ("data.csv", False),
                                         ("config.yaml", False), ("archive.zip", False)])
def test_file_search_formats(name, accepted):
    assert is_compatible_path(name) is accepted


@pytest.mark.parametrize("metadata", [[{}], [{"bytes": -1}], [{"bytes": True}],
                                     [{"bytes": MAX_INPUT_FILE_BYTES}],
                                     [{"bytes": MAX_INPUT_FILE_BYTES - 1}, {"bytes": 2}]])
def test_input_size_rejects_invalid_or_excessive_total(metadata):
    with pytest.raises(ValueError):
        validate_input_file_sizes(metadata)


def test_input_size_boundary():
    validate_input_file_sizes([{"bytes": MAX_INPUT_FILE_BYTES - 1}, {"bytes": 1}])


def test_unsupported_file_is_not_indexed():
    client = OpenAIClient("test")
    client.retrieve_file = Mock(return_value={"filename": "data.csv"})
    client._req = Mock()
    with pytest.raises(ValueError):
        client.add_file_to_vector_store("vs_test", "file-test")
    client._req.assert_not_called()
