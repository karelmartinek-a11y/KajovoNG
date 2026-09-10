from unittest.mock import Mock


def test_batch_timeout_cleans_partial_output_only_after_terminal_state(monkeypatch):
    from scripts import verify_batch_live
    client = Mock()
    client.upload_file.return_value = {"id": "file-own-input"}
    client.create_batch.return_value = {"id": "batch_own"}
    client.retrieve_batch.return_value = {"status": "cancelled", "output_file_id": "file-own-partial"}
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("sys.argv", ["verify_batch_live.py", "--live"])
    monkeypatch.setattr(verify_batch_live, "OpenAIClient", lambda _: client)
    clock = iter([0, 100, 100, 101])
    monkeypatch.setattr(verify_batch_live.time, "monotonic", lambda: next(clock))
    verify_batch_live.main()
    client.cancel_batch.assert_called_once_with("batch_own")
    removed = {call.args[0] for call in client.delete_file.call_args_list}
    assert removed == {"file-own-input", "file-own-partial"}
    calls = [call[0] for call in client.mock_calls]
    assert calls.index("retrieve_batch") < calls.index("delete_file")
