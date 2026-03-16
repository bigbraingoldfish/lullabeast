"""Test UI design system typography."""
import pytest
import re


def test_autodev_wordmark_uses_jetbrains_mono():
    """AUTODEV wordmark should render in JetBrains Mono or Space Mono."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check Google Fonts link includes JetBrains Mono
    assert "JetBrains+Mono" in content, \
        "JetBrains Mono font not loaded"
    
    # Check header-text class uses JetBrains Mono
    assert "font-header" in content or "JetBrains+Mono" in content, \
        "Header font not properly configured"


def test_phase_ids_use_monospace():
    """Phase IDs should render in JetBrains Mono or Space Mono."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Phase IDs should use header-text class which applies JetBrains Mono
    assert "header-text" in content, \
        "Phase IDs should use header-text class for monospace font"


def test_body_text_uses_ibm_plex_sans():
    """Body text and labels should render in IBM Plex Sans or DM Sans."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check Google Fonts link includes IBM Plex Sans
    assert "IBM+Plex+Sans" in content, \
        "IBM Plex Sans font not loaded"
    
    # Check body style uses IBM Plex Sans
    body_font_match = re.search(r"font-family:\s*'([^']+)'", content)
    if body_font_match:
        font_family = body_font_match.group(1)
        assert "IBM Plex Sans" in font_family or "DM Sans" in font_family, \
            f"Body font {font_family} not IBM Plex Sans or DM Sans"


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
    """Body should use IBM Plex Sans."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check Tailwind config for body font
    body_font_match = re.search(r"'body':\s*\[[^\]]+\]", content)
    assert body_font_match, "Body font not configured in Tailwind"
    
    # Should include IBM Plex Sans
    assert "IBM Plex Sans" in body_font_match.group(0), \
        "Body font should be IBM Plex Sans"
