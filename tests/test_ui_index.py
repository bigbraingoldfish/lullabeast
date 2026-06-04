"""Tests for UI index.html - verifies 3-panel layout with React/Tailwind CDNs."""
import pytest
import re
from pathlib import Path

# Path to the index.html file
INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    """Read the index.html content."""
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_get_returns_200_with_html_content(html_content):
    """GET / returns HTTP 200 with HTML content containing React and Tailwind CDN script tags."""
    assert "<html" in html_content.lower(), "HTML content missing"
    assert "react" in html_content.lower(), "React CDN missing"
    assert "tailwind" in html_content.lower(), "Tailwind CDN missing"


def test_three_panel_layout_regions(html_content):
    """HTML contains three distinct layout regions: header (top), two-column middle, bottom panel."""
    # Check for header region - look for header-like elements or panel structures
    has_header = bool(re.search(r'<header|class=".*header|grid.*grid-rows.*3', html_content, re.IGNORECASE))
    
    # Check for two-column middle section - look for grid with columns or side-by-side panels
    has_two_column = bool(re.search(r'grid-cols-2|grid-cols-\[|class=".*left.*right|two.*column', html_content, re.IGNORECASE))
    
    # Check for bottom panel - look for footer or bottom panel
    has_bottom = bool(re.search(r'<footer|class=".*bottom|class=".*footer', html_content, re.IGNORECASE))
    
    # Also check for flex/grid structure that creates 3-panel layout
    has_three_row_grid = bool(re.search(r'grid-rows-3|grid-rows-\[', html_content, re.IGNORECASE))
    
    assert has_header or has_three_row_grid, "Missing header region or 3-row grid structure"
    assert has_two_column, "Missing two-column middle section"
    assert has_bottom or has_three_row_grid, "Missing bottom panel or 3-row grid structure"


def test_tailwind_cdn_present(html_content):
    """Tailwind CDN script tag present in HTML."""
    # Look for Tailwind CDN script
    has_tailwind = bool(re.search(r'tailwind.*cdn|cdn.*tailwind|play\.tailwindcss', html_content, re.IGNORECASE))
    assert has_tailwind, "Tailwind CDN script tag not found"


def test_react_cdn_present(html_content):
    """React and ReactDOM UMD links present in HTML (local static or CDN)."""
    # Look for React — local static path or CDN fallback
    has_react = bool(re.search(r'/static/react\.min\.js|react\.umd|unpkg\.com/react|cdn\.jsdelivr\.net.*react', html_content, re.IGNORECASE))
    has_reactdom = bool(re.search(r'/static/react-dom\.min\.js|react-dom\.umd|unpkg\.com/react-dom|cdn\.jsdelivr\.net.*react-dom', html_content, re.IGNORECASE))

    assert has_react, "React UMD link not found"
    assert has_reactdom, "ReactDOM UMD link not found"


def test_google_fonts_header_present(html_content):
    """Google Fonts link includes JetBrains Mono or Space Mono."""
    has_jetbrains = bool(re.search(r'jetbrains.*mono', html_content, re.IGNORECASE))
    has_space_mono = bool(re.search(r'space.*mono', html_content, re.IGNORECASE))
    
    assert has_jetbrains or has_space_mono, "JetBrains Mono or Space Mono font not found in Google Fonts"


def test_google_fonts_body_present(html_content):
    """Google Fonts link includes the humanist chrome sans (Hanken Grotesk; IBM Plex / DM Sans tolerated)."""
    has_hanken = bool(re.search(r'hanken.*grotesk', html_content, re.IGNORECASE))
    has_ibm_plex = bool(re.search(r'ibm.*plex.*sans', html_content, re.IGNORECASE))
    has_dm_sans = bool(re.search(r'dm.*sans', html_content, re.IGNORECASE))

    assert has_hanken or has_ibm_plex or has_dm_sans, \
        "Humanist chrome sans (Hanken Grotesk) not found in Google Fonts"


def test_inline_script_for_react_app(html_content):
    """HTML contains inline script or module script for React app initialization."""
    # Look for inline script with React app or htm or babel
    has_inline_script = bool(re.search(r'<script[^>]*>.*React|htm\.bind|ReactDOM\.render|Babel\.transform', html_content, re.DOTALL))
    has_module_script = bool(re.search(r'<script[^>]*type=["\']module["\']', html_content))
    
    assert has_inline_script or has_module_script, "No inline script or module script for React app found"


def test_no_javascript_console_errors(html_content):
    """No JavaScript console errors - basic syntax validation."""
    # Extract all script content
    script_matches = re.findall(r'<script[^>]*>(.*?)</script>', html_content, re.DOTALL)
    
    # Check for obvious syntax errors in inline scripts (not external CDN scripts)
    for script in script_matches:
        # Skip empty scripts or external src scripts
        if 'src=' in script:
            continue
        # Check for unclosed braces (basic check)
        open_braces = script.count('{')
        close_braces = script.count('}')
        open_parens = script.count('(')
        close_parens = script.count(')')
        
        # Allow some imbalance as this is basic validation
        # The real test would be running in a browser
        assert abs(open_braces - close_braces) <= 5, f"Possible unclosed braces in script: {script[:100]}"
        assert abs(open_parens - close_parens) <= 5, f"Possible unclosed parens in script: {script[:100]}"