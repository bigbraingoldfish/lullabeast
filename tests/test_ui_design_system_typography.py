"""Test UI design system typography."""
import pytest
import re


def test_lullabeast_wordmark_uses_jetbrains_mono():
    """lullabeast wordmark should render in JetBrains Mono or Space Mono."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # JetBrains Mono is self-hosted (woff2 under /static/fonts/) via @font-face
    assert "jetbrains-mono.woff2" in content, \
        "JetBrains Mono font not loaded (self-hosted woff2 missing)"

    # Check header-text class uses JetBrains Mono (the @font-face family name)
    assert "font-header" in content or "JetBrains Mono" in content, \
        "Header font not properly configured"


def test_phase_ids_use_monospace():
    """Phase IDs should render in JetBrains Mono or Space Mono."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Phase IDs should use the font-mono class (JetBrains Mono). Lullabeast:
    # header-text is now the Hanken chrome font, so data IDs opt into font-mono.
    assert "font-mono" in content, \
        "Phase IDs should use font-mono class for monospace font"


def test_body_text_uses_hanken_grotesk():
    """Body text and labels should render in Hanken Grotesk (humanist chrome sans)."""
    with open("ui/index.html", "r") as f:
        content = f.read()

    # Hanken Grotesk is self-hosted (woff2 under /static/fonts/) via @font-face
    assert "hanken-grotesk.woff2" in content, \
        "Hanken Grotesk font not loaded (self-hosted woff2 missing)"

    # Check body style uses Hanken Grotesk
    body_font_match = re.search(r"font-family:\s*'([^']+)'", content)
    if body_font_match:
        font_family = body_font_match.group(1)
        assert "Hanken Grotesk" in font_family, \
            f"Body font {font_family} not Hanken Grotesk"


def test_log_output_uses_monospace():
    """Log output, paths, and JSON values should use monospace font."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for font-mono class usage
    assert "font-mono" in content, \
        "Monospace font not used for log output/paths/JSON"


def test_header_font_configured():
    """Headers should use JetBrains Mono."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check Tailwind config for header font
    header_font_match = re.search(r"'header':\s*\[[^\]]+\]", content)
    assert header_font_match, "Header font not configured in Tailwind"
    
    # Should include JetBrains Mono
    assert "JetBrains Mono" in header_font_match.group(0), \
        "Header font should be JetBrains Mono"


def test_body_font_configured():
    """Body should use Hanken Grotesk."""
    with open("ui/index.html", "r") as f:
        content = f.read()

    # Check Tailwind config for body font
    body_font_match = re.search(r"'body':\s*\[[^\]]+\]", content)
    assert body_font_match, "Body font not configured in Tailwind"

    # Should include Hanken Grotesk
    assert "Hanken Grotesk" in body_font_match.group(0), \
        "Body font should be Hanken Grotesk"
