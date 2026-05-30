"""Static contract tests: Ideas chat scroll-to-bottom UX.

Two behaviors:
  1. Opening an existing chat lands you at the BOTTOM (newest turn) instantly —
     not at the top. A deferred (one-frame) instant scroll runs in the
     session-load path so the freshly-set messages have painted first.
  2. A subtle scroll-to-bottom button appears when the user is NOT pinned to the
     bottom; clicking it smooth-scrolls to the latest message.

Both reuse the existing bottom spacer `messagesEndRef`. The scroll container
gets a `chatScrollRef` + `onScroll` handler that tracks `isAtBottom` via the
distance-from-bottom (`scrollHeight - scrollTop - clientHeight`).

Pattern mirrors the other ui_ideas contract suites: read the single-file React
frontend as text and regex the required code shapes. (Exact visual styling of
the button is reviewed by eye per project convention; these tests pin the
behavioral wiring only.)
"""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_chat_scroll_container_has_ref_and_onscroll():
    """The chat message list must carry `chatScrollRef` and an `onScroll`
    handler so the UI can tell whether the user is pinned to the bottom."""
    html = load_index_html()
    assert "chatScrollRef" in html, "Expected a dedicated chatScrollRef for the chat list"
    assert (
        re.search(r"ref=\{chatScrollRef\}[\s\S]{0,160}?onScroll=\{", html)
        or re.search(r"onScroll=\{[\s\S]{0,160}?ref=\{chatScrollRef\}", html)
    ), "Expected the chat list <div> to wire both ref={chatScrollRef} and onScroll"


def test_is_at_bottom_state_and_distance_compute():
    """`isAtBottom` state + a distance-from-bottom computation drive the button's
    visibility."""
    html = load_index_html()
    assert re.search(r"\[\s*isAtBottom\s*,\s*setIsAtBottom\s*\]\s*=\s*useState", html), (
        "Expected an isAtBottom useState"
    )
    assert re.search(r"scrollHeight\s*-\s*scrollTop\s*-\s*clientHeight", html), (
        "Expected a (scrollHeight - scrollTop - clientHeight) distance-from-bottom check"
    )


def test_scroll_to_bottom_button_present_and_gated():
    """A scroll-to-bottom button with a stable test id appears only when NOT at
    the bottom, and its click scrolls to the latest message."""
    html = load_index_html()
    assert 'data-testid="ideas-chat-scroll-to-bottom"' in html, (
        "Expected a stable data-testid on the scroll-to-bottom button"
    )
    assert re.search(r"!\s*isAtBottom[\s\S]{0,200}?ideas-chat-scroll-to-bottom", html), (
        "Expected the button gated behind !isAtBottom"
    )
    assert re.search(
        r"ideas-chat-scroll-to-bottom[\s\S]{0,300}?onClick=\{[\s\S]{0,80}?(scrollChatToBottom|scrollIntoView)",
        html,
    ), "Expected the button's onClick to scroll to the bottom"


def test_open_session_scrolls_to_bottom_instantly():
    """Opening a session scrolls to the bottom INSTANTLY ('auto', no smooth
    animation through history), deferred a frame so the new rows have painted."""
    html = load_index_html()
    assert re.search(r"scrollChatToBottom\(\s*[\"']auto[\"']\s*\)", html), (
        "Expected an instant scrollChatToBottom('auto') on session open"
    )
    assert re.search(r"requestAnimationFrame\([\s\S]{0,80}?scrollChatToBottom", html), (
        "Expected the open-session scroll deferred one frame via requestAnimationFrame"
    )


def test_scroll_helper_reuses_messages_end_ref():
    """`scrollChatToBottom` reuses the existing bottom spacer `messagesEndRef`
    (the nearest scrollable ancestor is the chat list, so this scrolls only the
    chat column)."""
    html = load_index_html()
    assert re.search(
        r"scrollChatToBottom\s*=\s*\([^)]*\)\s*=>[\s\S]{0,160}?messagesEndRef\.current\??\.scrollIntoView",
        html,
    ), "Expected scrollChatToBottom to scrollIntoView via messagesEndRef"


# ── existing reply auto-scroll effect (unchanged behavior — keep it pinned) ───
# Moved here from the retired test_ui_ideas_timeout_recovery_contract suite. The
# `useEffect([messages])` smooth-scrolls to the newest reply when it arrives, and
# must NOT fire while the last message is the pending "Working…" bubble (so the
# user's own send doesn't bump them while the request is in flight). The new
# open-session instant scroll above is a SEPARATE mechanism and must not cause a
# refactor that breaks this guard.

def test_messages_end_ref_scrolled_into_view_on_messages_change():
    """A useEffect watching `messages` calls messagesEndRef.current?.scrollIntoView
    so the chat auto-scrolls when the agent's reply replaces the pending bubble."""
    html = load_index_html()
    assert re.search(
        r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{"
        r"[\s\S]{0,400}?messages\s*\[\s*messages\.length\s*-\s*1\s*\]"
        r"[\s\S]{0,400}?messagesEndRef\.current\??\.scrollIntoView"
        r"[\s\S]{0,200}?\}\s*,\s*\[\s*messages\s*\]\s*\)",
        html,
    ), (
        "Expected a useEffect watching `messages` that calls "
        "messagesEndRef.current?.scrollIntoView(...) on a non-pending assistant reply"
    )


def test_scroll_effect_guards_against_pending_last_message():
    """The messages-watch effect must guard scrollIntoView with a `!last.pending`
    check so it only scrolls on non-pending assistant arrivals, not on every send."""
    html = load_index_html()
    assert re.search(
        r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{"
        r"[\s\S]{0,600}?!\s*\w+\.pending"
        r"[\s\S]{0,200}?scrollIntoView"
        r"[\s\S]{0,200}?\}\s*,\s*\[\s*messages\s*\]\s*\)",
        html,
    ), (
        "Expected the messages-watch useEffect to guard scrollIntoView with a "
        "`!last.pending` check (only scroll on non-pending assistant arrivals)"
    )
