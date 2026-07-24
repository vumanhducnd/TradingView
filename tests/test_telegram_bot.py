import importlib


def test_sanitize_telegram_text_escapes_html_special_chars():
    telegram_bot = importlib.import_module("scanner.telegram_bot")

    caption = "Giá < 1000 và > 500 & cần <b>đọc</b>"
    sanitized = telegram_bot._sanitize_telegram_text(caption, parse_mode="HTML")

    assert sanitized == "Giá &lt; 1000 và &gt; 500 &amp; cần <b>đọc</b>"
