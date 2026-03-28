from kajovospend.app.settings import AppSettings


def test_default_settings_are_realistic() -> None:
    settings = AppSettings()
    assert settings.openai_enabled is False
    assert settings.openai_model == ''
    assert settings.automatic_retry_limit >= 1
    assert settings.manual_retry_limit >= 1
    assert settings.openai_retry_limit >= 1
