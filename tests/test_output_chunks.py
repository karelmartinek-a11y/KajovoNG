import json
import pytest
from kajovo.core.contracts import ContractError, validate_chunk_metadata
from kajovo.desktop.batches import import_bundle


def chunk(index, count, more, following):
    return {'chunk_index': index, 'chunk_count': count, 'has_more': more, 'next_chunk_index': following}


def raw(*payloads):
    return '\n'.join(json.dumps({'response': {'body': {'status': 'completed', 'output_text': json.dumps(payload)}}}) for payload in payloads).encode()


@pytest.mark.parametrize('metadata', [chunk(0, 3, False, None), chunk(0, 1, True, 1), chunk(0, True, False, None), chunk(0, 10**12, False, None), chunk(0, 1, False, 1), chunk(0, 0, True, True)])
def test_inconsistent_chunk_metadata_is_rejected(metadata):
    with pytest.raises(ContractError):
        validate_chunk_metadata(metadata)


def test_batch_contradictory_terminal_chunks_preserve_existing_file(tmp_path):
    target = tmp_path/'keep.txt'
    target.write_text('original', encoding='utf-8')
    payloads = [{'contract': 'A3_FILE', 'path': 'keep.txt', 'content': 'partial', 'chunking': chunk(index, 0, False, None)} for index in (0, 1)]
    result = import_bundle(raw(*payloads), str(tmp_path))
    assert result['errors'] and not result['written']
    assert target.read_text(encoding='utf-8') == 'original'


def test_batch_accepts_complete_out_of_order_chunks(tmp_path):
    payloads = [{'contract': 'A3_FILE', 'path': 'result.txt', 'content': str(index), 'chunking': chunk(index, 2, index == 0, 1 if index == 0 else None)} for index in (1, 0)]
    result = import_bundle(raw(*payloads), str(tmp_path))
    assert not result['errors'] and len(result['written']) == 1
    assert (tmp_path/'result.txt').read_text(encoding='utf-8') == '01'


@pytest.mark.parametrize('bundle', [{'files': {}}, {'files': None}, {'files': [], 'root': []}, {}])
def test_invalid_bundle_is_not_an_empty_success(bundle, tmp_path):
    result = import_bundle(raw(dict(bundle, contract='C_FILES_ALL')), str(tmp_path))
    assert result['errors'] and not result['written']


def test_batch_second_bundle_cannot_overwrite_first_result(tmp_path):
    target = tmp_path/'file.txt'
    target.write_text('original', encoding='utf-8')
    first = {'contract': 'C_FILES_ALL', 'files': [{'path': 'file.txt', 'content': 'first'}]}
    second = {'contract': 'C_FILES_ALL', 'files': [{'path': 'FILE.txt', 'content': 'second'}]}
    result = import_bundle(raw(first, second), str(tmp_path))
    assert result['errors'] and not result['written']
    assert target.read_text(encoding='utf-8') == 'original'


def test_batch_case_colliding_chunks_are_rejected_before_any_write(tmp_path):
    payloads = [{'contract': 'A3_FILE', 'path': name, 'content': 'data', 'chunking': chunk(0, 1, False, None)} for name in ('file.txt', 'FILE.txt')]
    result = import_bundle(raw(*payloads), str(tmp_path))
    assert result['errors'] and not result['written']
    assert not (tmp_path/'file.txt').exists()


def test_missing_batch_contract_is_an_error(tmp_path):
    assert import_bundle(raw({}), str(tmp_path))['errors']
