import unittest

from kajovo.core.model_filters import filter_models_for_generate


class FakeCaps:
    def __init__(self, supports_previous_response_id=None):
        self.supports_previous_response_id = supports_previous_response_id


class FakeCache:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, mid):
        return self.mapping.get(mid)


class ModelFilterTests(unittest.TestCase):
    def test_filters_explicit_prev_false(self):
        cache = FakeCache({"bad": FakeCaps(supports_previous_response_id=False)})
        models = ["ok", "bad", "unknown"]
        out = filter_models_for_generate(models, cache)
        self.assertIn("ok", out)
        self.assertIn("unknown", out)
        self.assertNotIn("bad", out)

    def test_allows_unknown_caps(self):
        cache = FakeCache({})
        models = ["m1", "m2"]
        out = filter_models_for_generate(models, cache)
        self.assertEqual(out, models)


if __name__ == "__main__":
    unittest.main()
