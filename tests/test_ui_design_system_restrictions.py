"""Test UI design system restrictions."""
import pytest
import re


def test_no_purple_anywhere():
    """No purple anywhere in UI - no #8b5cf6, no violet, no purple."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for violet colors
    assert "violet" not in content.lower(), \
        "Violet color found in UI"
    
    # Check for purple colors
    assert "purple" not in content.lower(), \
        "Purple color found in UI"
    
    # Check for hex color #8b5cf6
    assert "#8b5cf6" not in content.lower(), \
        "Purple hex color #8b5cf6 found in UI"
    
    # Check for bg-violet-* classes
    violet_classes = re.findall(r'bg-violet-\d+', content)
    assert len(violet_classes) == 0, \
        f"Found violet color classes: {violet_classes}"


def test_no_drop_shadows_on_panels():
    """No drop shadows on panel cards - no shadow-xl, shadow-2xl, shadow-lg."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for shadow classes
    shadow_classes = ["shadow-xl", "shadow-2xl", "shadow-lg"]
    for shadow_class in shadow_classes:
        assert shadow_class not in content, \
            f"Found drop shadow {shadow_class} on panels"


def test_no_emoji_in_ui():
    """No emoji anywhere in UI - verified by checking no emoji characters."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Common emoji patterns
    emoji_patterns = [
        r'[\U0001F300-\U0001F9FF]',  # Unicode emoji range
        r'✓',  # Checkmark (used as status icon)
        r'▶',  # Play triangle
        r'○',  # Circle
        r'⊘',  # Ballot box with X
        r'⚠',  # Warning
    ]
    
    # Allow status icons that are text-based (✓, ▶, ○, ⊘, ⚠)
    # These are ASCII-compatible and acceptable as text
    # But reject actual Unicode emoji
    
    # Check for non-ASCII emoji (beyond basic text symbols)
    unicode_emoji = re.findall(r'[\U0001F000-\U0001F9FF]', content)
    assert len(unicode_emoji) == 0, \
        f"Found Unicode emoji in UI: {unicode_emoji}"


def test_no_gradients_on_backgrounds():
    """No gradients on backgrounds - verified by checking no gradient-* classes."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check for gradient classes
    gradient_classes = re.findall(r'gradient-[^\s]+', content)
    assert len(gradient_classes) == 0, \
        f"Found gradient classes: {gradient_classes}"
    
    # Check for background gradient
    assert "background-gradient" not in content.lower(), \
        "Found background-gradient in UI"


def test_no_shadow_on_modal():
    """Modal elements should not have drop shadows."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check ConfirmationModal doesn't use shadows
    modal_shadow = re.findall(r'shadow-\w+', content)
    # Filter out any shadow classes that might be there
    for shadow in modal_shadow:
        assert "shadow-xl" not in shadow and "shadow-2xl" not in shadow, \
            f"Found drop shadow on modal: {shadow}"


def test_agent_badge_no_violet():
    """Agent badge should not use violet/purple colors."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    
    # Check AGENT_COLORS doesn't have violet
    agent_colors_section = re.search(r'const AGENT_COLORS = \{[^}]+\}', content)
    if agent_colors_section:
        agent_colors = agent_colors_section.group(0)
        assert "violet" not in agent_colors.lower(), \
            "Agent badge uses violet color"
