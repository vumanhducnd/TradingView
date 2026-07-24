import importlib


def test_groq_model_config_uses_supported_default(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    config = importlib.import_module("scanner.config")
    importlib.reload(config)
    assert config.GROQ_MODEL == "llama-3.3-70b-versatile"


def test_groq_model_config_uses_vision_default(monkeypatch):
    monkeypatch.delenv("GROQ_VISION_MODEL", raising=False)
    config = importlib.import_module("scanner.config")
    importlib.reload(config)
    # Default vision model should now be Qwen per request
    assert config.GROQ_VISION_MODEL == "qwen/qwen3.6-27b"


def test_groq_model_config_falls_back_for_unsupported_model(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    monkeypatch.setenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    config = importlib.import_module("scanner.config")
    importlib.reload(config)
    assert config.GROQ_MODEL == "llama-3.3-70b-versatile"
    # If an explicitly decommissioned vision model is set, we disable Groq vision so the code will
    # fall back to OpenAI or other configured fallback at runtime.
    assert config.GROQ_VISION_MODEL == ""
