import unittest

from kajovo.core.model_capabilities import split_text as caps_split_text
from kajovo.core.pipeline import split_text as pipeline_split_text


class SplitTextTests(unittest.TestCase):
    def test_split_text_pipeline(self):
        self.assertEqual(pipeline_split_text("abcdef", 2), ["ab", "cd", "ef"])

    def test_split_text_model_caps_empty(self):
        self.assertEqual(caps_split_text("", 10), [""])
