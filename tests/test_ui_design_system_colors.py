"""Test UI design system colors."""
import pytest
import re


def test_background_color_is_near_black():
    """Background color should be the Lullabeast deep indigo-black #0b0a12 (or near)."""
    with open("ui/index.html", "r") as f:
        content = f.read()

    # Check body background color
    body_bg_match = re.search(r'background-color:\s*(#[0-9a-fA-F]+)', content)
    assert body_bg_match, "No body background-color found"

    bg_color = body_bg_match.group(1).lower()
    # Lullabeast night-sky base (#0b0a12); legacy near-black values still tolerated.
    assert bg_color in ["#0b0a12", "#0d0f12", "#0c0e11", "#0b0d10", "#0a0c10"], \
        f"Background color {bg_color} not the Lullabeast base #0b0a12"


def test_panel_backgrounds():
    """Panel backgrounds should be #141618 or within #141618-#181a1c range."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for panel background colors - look for bg-slate-900 or custom colors
    # Panels should use #141618 or similar
    allowed_panel_colors = ["#141618", "#151719", "#161a1c", "#181a1c"]
    
    # Find all bg-slate-* classes in the main content area
    panel_bg_pattern = r'bg-slate-(?:800|900)'
    matches = re.findall(panel_bg_pattern, content)
    
    # If there are slate-800/900 classes, they should be replaced
    assert len(matches) == 0, f"Found {len(matches)} panel bg-slate-* classes that should be replaced with #141618"


def test_borders_are_subtle():
    """Borders should be subtle - no white or harsh borders."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for harsh border colors
    harsh_borders = ["border-white", "border-slate-400", "border-slate-300", "border-gray-400"]
    
    for harsh in harsh_borders:
        assert harsh not in content, f"Found harsh border: {harsh}"
    
    # Check for hex colors that are too light
    harsh_hex = re.findall(r'border-#([0-9a-fA-F]{6})', content)
    for hex_color in harsh_hex:
        int_val = int(hex_color, 16)
        assert int_val < 0xaaaaaa, f"Found harsh border color #{hex_color}"


def test_amber_for_active_waiting():
    """Amber (#f59e0b) should be used for active/waiting states."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for amber color usage in status states
    # Should have bg-amber-500 or text-amber-*
    assert "bg-amber-500" in content or "text-amber-" in content, \
        "Amber color not found for active/waiting states"


def test_orange_for_human_required():
    """Orange (#f97316) should be used for human-required states."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Should use orange-500 or orange-600 for human required states
    assert "bg-orange-500" in content or "bg-orange-600" in content, \
        "Orange color not found for human-required states"


def test_red_for_error_halted():
    """Red (#dc2626) should be used for error/halted states."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Should use red-600 for error/halted states
    assert "bg-red-600" in content, \
        "Red color not found for error/halted states"


def test_muted_green_for_complete():
    """Muted green (#22c55e) should be used for complete states."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Should use green-600 for complete states
    assert "bg-green-600" in content, \
        "Green color not found for complete states"


def test_primary_accent_is_lamplight_gold():
    """Primary accent is the Lullabeast lamplight gold #e2b14c (cyan was retired)."""
    with open("ui/index.html", "r") as f:
        content = f.read()

    # Check Tailwind config for accent color
    accent_match = re.search(r"'accent':\s*'#([0-9a-fA-F]+)'", content)
    if accent_match:
        accent_color = "#" + accent_match.group(1).lower()
        assert accent_color in ["#e2b14c", "#f0c56b", "#c7973a"], \
            f"Accent color {accent_color} not the Lullabeast lamplight gold #e2b14c"


def test_current_phase_highlight_uses_accent():
    """Current phase highlight border should use accent color."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Should have border-blue-500 or border-cyan-500 or border-[#00b4d8]
    assert "border-blue-500" in content or "border-cyan-500" in content or "border-[" in content, \
        "Current phase highlight border not using accent color"


def test_progress_bar_uses_accent():
    """Progress bar fill should use accent color."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Progress bar should use the accent color. Lullabeast repointed this to the
    # gold `bg-accent` token (cyan/blue were retired); legacy names still tolerated.
    assert "bg-accent" in content or "bg-cyan-500" in content or "bg-blue-500" in content, \
        "Progress bar not using accent color"
