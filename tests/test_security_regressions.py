import os
import unittest
from unittest.mock import Mock

from kajovo.core.utils import safe_join_under_root
from kajovo.core.openai_client import OpenAIClient


class SafeJoinUnderRootTests(unittest.TestCase):
    def test_blocks_path_traversal(self):
        with self.assertRaises(ValueError):
            safe_join_under_root('/tmp/root', '../etc/passwd')

    def test_allows_regular_relative_path(self):
        out = safe_join_under_root('/tmp/root', 'nested/file.txt')
        expected_prefix = os.path.abspath('/tmp/root')
        self.assertTrue(out.startswith(expected_prefix))
        expected_suffix = os.path.join('nested', 'file.txt')
        self.assertTrue(out.endswith(expected_suffix))


class OpenAIClientRequestTests(unittest.TestCase):
    def test_file_upload_uses_session_request_with_timeout(self):
        client = OpenAIClient('k')
        client._sdk = None
        mock_response = Mock(status_code=200, headers={'content-type': 'application/json'})
        mock_response.json.return_value = {'id': 'ok'}
        client.session.request = Mock(return_value=mock_response)

        client._req('POST', '/files', json_body={'purpose': 'user_data'}, files={'file': ('x', b'1')}, timeout=12.0)

        _, kwargs = client.session.request.call_args
        self.assertEqual(kwargs['timeout'], 12.0)


if __name__ == '__main__':
    unittest.main()
