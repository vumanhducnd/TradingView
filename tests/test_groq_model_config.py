import importlib


def test_groq_model_config_uses_supported_default(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    config = importlib.import_module("scanner.config")
    importlib.reload(config)
    assert config.GROQ_MODEL == "llama-3.3-70b-versatile"


def test_groq_model_config_falls_back_for_unsupported_model(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    monkeypatch.setenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    config = importlib.import_module("scanner.config")
    importlib.reload(config)
    assert config.GROQ_MODEL == "llama-3.3-70b-versatile"
    assert config.GROQ_VISION_MODEL == "llama-3.3-70b-versatile"
