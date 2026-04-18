"""One-off builder: splices screen components into index.html. Run from repo root."""
from pathlib import Path

MIDDLE = r"""
        const PRD_SECTION_TITLES = [
            "Problem Statement", "Goals & Success Metrics", "User Stories", "Functional Requirements",
            "Edge Cases", "Non-Functional Requirements", "Dependencies & Integrations", "Milestones & Timeline",
            "Risks & Mitigations", "Open Questions", "Glossary & Domain Terms", "Revision History",
        ];

        function IdeasScreen() {
            const ctx = useContext(AppCtx);
            const [messages, setMessages] = useState([]);
            const [prdContent, setPrdContent] = useState("");
            const [roadmapContent, setRoadmapContent] = useState("");
            const [isLoading, setIsLoading] = useState(false);
            const [convertLoading, setConvertLoading] = useState(false);
            const [convertError, setConvertError] = useState("");
            const [currentIdeaId, setCurrentIdeaId] = useState("1");
            const [ideasList, setIdeasList] = useState([]);
            const [inputText, setInputText] = useState("");
            const [readiness, setReadiness] = useState({ ready: false, reason: "" });
            const [chatsRailCollapsed, setChatsRailCollapsed] = useState(false);
            const taRef = useRef(null);

            const refreshIdeas = () => {
                fetch("/api/ideas")
                    .then((r) => r.json())
                    .then((d) => setIdeasList(Array.isArray(d) ? d : []))
                    .catch(() => {});
            };

            useEffect(() => {
                refreshIdeas();
            }, []);

            useEffect(() => {
                if (!currentIdeaId) return;
                fetch(`/api/ideas/${currentIdeaId}/session`)
                    .then((r) => r.json())
                    .then((d) => {
                        setMessages(d.messages || []);
                        setPrdContent(d.prd_content || "");
                        setRoadmapContent(d.roadmap_content || "");
                    })
                    .catch(() => {});
                fetch(`/api/ideas/${currentIdeaId}/readiness`)
                    .then((r) => r.json())
                    .then((d) => setReadiness(d))
                    .catch(() => {});
            }, [currentIdeaId]);

            const adjustTa = () => {
                const el = taRef.current;
                if (!el) return;
                el.style.height = "auto";
                const lh = 22;
                const maxH = lh * 5;
                el.style.height = Math.min(el.scrollHeight, maxH) + "px";
            };

            const submitMessage = () => {
                const t = inputText.trim();
                if (!t || isLoading || !currentIdeaId) return;
                const turn = messages.filter((m) => m.role === "user").length + 1;
                setIsLoading(true);
                fetch(`/api/ideas/${currentIdeaId}/message`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ content: t, turn }),
                })
                    .then((r) => {
                        if (!r.ok) throw new Error(String(r.status));
                        return r.json();
                    })
                    .then((data) => {
                        setMessages((prev) => [
                            ...prev,
                            { role: "user", content: t, ts: new Date().toISOString() },
                            { role: "assistant", content: data.response || "", ts: new Date().toISOString() },
                        ]);
                        setPrdContent(data.prd_content || "");
                        setInputText("");
                        setIsLoading(false);
                        refreshIdeas();
                    })
                    .catch(() => {
                        setIsLoading(false);
                    });
            };

            const onKeyDown = (e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submitMessage();
                }
            };

            const newIdea = () => {
                fetch("/api/ideas", { method: "POST" })
                    .then((r) => r.json())
                    .then((d) => {
                        setCurrentIdeaId(d.id);
                        setMessages([]);
                        setPrdContent("");
                        setRoadmapContent("");
                        setInputText("");
                        setChatsRailCollapsed(false);
                    });
            };

            const selectIdeaFromRail = (id) => {
                setCurrentIdeaId(id);
                setChatsRailCollapsed(true);
            };

            const deleteIdea = (id, ev) => {
                ev.stopPropagation();
                if (!confirm("Delete this idea?")) return;
                fetch(`/api/ideas/${id}`, { method: "DELETE" }).then(() => {
                    refreshIdeas();
                    if (currentIdeaId === id) {
                        setMessages([]);
                        setPrdContent("");
                        setCurrentIdeaId("1");
                    }
                });
            };

            const doConvert = () => {
                if (!currentIdeaId || !prdContent.trim()) return;
                setConvertLoading(true);
                setConvertError("");
                fetch(`/api/ideas/${currentIdeaId}/convert`, { method: "POST" })
                    .then((r) => {
                        if (!r.ok) return r.text().then((t) => { throw new Error(t); });
                        return r.json();
                    })
                    .then((d) => {
                        setRoadmapContent(d.roadmap_content || "");
                        setConvertLoading(false);
                    })
                    .catch((e) => {
                        setConvertError(String(e.message || e));
                        setConvertLoading(false);
                    });
            };

            const continueSetup = () => {
                if (ctx && ctx.navigateToPreflightWithSeed) {
                    ctx.navigateToPreflightWithSeed(roadmapContent || "");
                }
            };

            const listedIds = new Set((ideasList || []).map((it) => it.id));
            const showUnlistedSession = currentIdeaId && !listedIds.has(currentIdeaId);

            return (
                <div className="flex h-full min-w-0">
                    {/* Vertical chat list — collapses after picking a chat so conversation + PRD get space */}
                    <div
                        className={`${
                            chatsRailCollapsed ? "w-11" : "w-56 sm:w-60"
                        } flex-shrink-0 flex flex-col bg-[#0d0f11] border-r border-[#1a1d21] transition-[width] duration-200 ease-out`}
                    >
                        {!chatsRailCollapsed ? (
                            <>
                                <div className="flex-shrink-0 flex items-center justify-between gap-2 px-3 py-2 border-b border-[#1a1d21]">
                                    <span className="header-text text-xs text-slate-400 uppercase tracking-wide">Chats</span>
                                    <button
                                        type="button"
                                        onClick={newIdea}
                                        className="header-text text-xs px-2 py-1 rounded bg-[#1a1d21] text-[#00b4d8] border border-[#1a1d21] hover:border-[#00b4d8]/50"
                                    >
                                        + New
                                    </button>
                                </div>
                                <div className="flex-1 overflow-y-auto py-2 px-2 space-y-1 min-h-0">
                                    {showUnlistedSession ? (
                                        <div className="rounded border border-dashed border-[#2a2d31] bg-[#141618]/50">
                                            <button
                                                type="button"
                                                onClick={() => selectIdeaFromRail(currentIdeaId)}
                                                className={`w-full text-left px-2 py-2 rounded ${
                                                    currentIdeaId && !listedIds.has(currentIdeaId)
                                                        ? "ring-1 ring-[#00b4d8]/40 bg-[#1a1d21]/60"
                                                        : ""
                                                }`}
                                            >
                                                <div className="text-slate-200 truncate text-xs font-medium header-text">Draft</div>
                                                <div className="text-slate-500 truncate text-[11px]">Not listed until first turn completes</div>
                                            </button>
                                        </div>
                                    ) : null}
                                    {ideasList.map((it) => (
                                        <div
                                            key={it.id}
                                            className={`flex rounded border border-transparent overflow-hidden ${
                                                currentIdeaId === it.id ? "border-[#00b4d8]/40 bg-[#1a1d21]" : "hover:bg-[#1a1d21]/70"
                                            }`}
                                        >
                                            <button
                                                type="button"
                                                onClick={() => selectIdeaFromRail(it.id)}
                                                className="flex-1 min-w-0 text-left px-2 py-2"
                                            >
                                                <div className="text-slate-200 truncate text-xs font-medium header-text">{it.name || it.id}</div>
                                                <div className="text-slate-500 truncate text-[11px]">{it.summary || "—"}</div>
                                            </button>
                                            <button
                                                type="button"
                                                className="px-1.5 text-slate-500 hover:text-red-400 hover:bg-[#141618] text-sm shrink-0"
                                                onClick={(e) => deleteIdea(it.id, e)}
                                                aria-label="Delete idea"
                                            >
                                                ×
                                            </button>
                                        </div>
                                    ))}
                                    {ideasList.length === 0 && !showUnlistedSession ? (
                                        <p className="text-slate-600 text-xs italic px-1 py-2">No projects yet. Click + New to start a PRD conversation.</p>
                                    ) : null}
                                </div>
                            </>
                        ) : (
                            <div className="flex-1 flex flex-col items-center justify-center gap-1 min-h-[120px] px-1">
                                <span className="header-text text-[10px] text-slate-600 [writing-mode:vertical-rl] rotate-180">chats</span>
                            </div>
                        )}
                        <div className="flex-shrink-0 border-t border-[#1a1d21]">
                            <button
                                type="button"
                                onClick={() => setChatsRailCollapsed((c) => !c)}
                                className="w-full py-2.5 text-xs text-slate-500 hover:text-[#00b4d8] hover:bg-[#1a1d21]/60 header-text"
                                title={chatsRailCollapsed ? "Expand chat list" : "Collapse chat list"}
                            >
                                {chatsRailCollapsed ? "▶" : "◀"}
                            </button>
                        </div>
                    </div>

                    <div className="flex-1 flex flex-col min-w-0 bg-[#141618] overflow-hidden border-r border-[#1a1d21]">
                        <div className="flex-shrink-0 border-b border-[#1a1d21] px-3 py-2 flex items-center justify-between gap-2">
                            <span className="header-text text-xs text-slate-500">Conversation</span>
                            <button
                                type="button"
                                onClick={(e) => deleteIdea(currentIdeaId, e)}
                                className="header-text text-xs px-2 py-1 rounded border border-[#1a1d21] text-slate-400 hover:text-red-400"
                            >
                                Delete session
                            </button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-4 space-y-2 min-h-0">
                            {messages.length === 0 ? (
                                <p className="text-slate-600 text-xs italic">No messages yet — type below to start.</p>
                            ) : null}
                            {messages.map((msg, i) => (
                                <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                                    <div className={`max-w-[95%] rounded px-3 py-2 text-sm ${msg.role === "user" ? "bg-[#00b4d8]/20 text-slate-100" : "bg-[#1a1d21] text-slate-200"}`}>
                                        {msg.content}
                                    </div>
                                </div>
                            ))}
                        </div>
                        <div className="flex-shrink-0 border-t border-[#1a1d21] p-3 space-y-2 bg-[#141618]">
                            <textarea
                                ref={taRef}
                                rows={1}
                                value={inputText}
                                onChange={(e) => { setInputText(e.target.value); adjustTa(); }}
                                onKeyDown={onKeyDown}
                                placeholder="Type a message…"
                                className="w-full bg-[#1a1d21] border border-[#2a2d31] rounded px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500 resize-none max-h-[110px] overflow-y-auto"
                            />
                        </div>
                    </div>
                    <div className="flex-1 flex flex-col min-w-0 bg-[#141618] overflow-hidden">
                        <div className="flex-shrink-0 border-b border-[#1a1d21] px-4 py-2 flex flex-wrap gap-2 items-center justify-between">
                            <div className="flex flex-wrap gap-2 items-center">
                                {readiness.ready && <span className="text-xs text-slate-500">ready: {readiness.reason}</span>}
                            </div>
                            <div className="flex gap-2">
                                {prdContent.trim().length > 0 && (
                                    <a href={`/api/ideas/${currentIdeaId}/download`} className="header-text text-xs px-2 py-1 rounded bg-[#1a1d21] text-[#00b4d8] border border-[#1a1d21]">
                                        Download PRD
                                    </a>
                                )}
                                <button type="button" disabled={!prdContent.trim() || convertLoading} onClick={doConvert} className="header-text text-xs px-2 py-1 rounded bg-[#1a1d21] text-slate-200 border border-[#1a1d21] disabled:opacity-40">
                                    {convertLoading ? "Generating…" : "Generate Roadmap"}
                                </button>
                                {roadmapContent && (
                                    <a href={`/api/ideas/${currentIdeaId}/download-roadmap`} className="header-text text-xs px-2 py-1 rounded bg-[#1a1d21] text-slate-300 border border-[#1a1d21]">
                                        Download Roadmap
                                    </a>
                                )}
                                {roadmapContent && (
                                    <button type="button" onClick={continueSetup} className="header-text text-xs px-2 py-1 rounded bg-[#00b4d8]/20 text-[#00b4d8] border border-[#00b4d8]/40">
                                        Continue to Setup →
                                    </button>
                                )}
                            </div>
                        </div>
                        {convertError && <div className="px-4 py-2 text-xs text-red-400 border-b border-[#1a1d21]">{convertError}</div>}
                        <div className={`flex-1 overflow-y-auto p-4 bg-[#141618] ${isLoading ? "status-pulse opacity-60" : ""}`}>
                            {prdContent.trim() ? (
                                <div className="text-sm text-slate-300 whitespace-pre-wrap mb-6 border-b border-[#1a1d21] pb-4">{prdContent}</div>
                            ) : null}
                            {roadmapContent ? (
                                <div className="border-b border-[#1a1d21] pb-4 mb-4">
                                    <h3 className="header-text text-slate-400 text-sm mb-2">Roadmap draft</h3>
                                    <pre className="text-xs text-slate-400 whitespace-pre-wrap">{roadmapContent}</pre>
                                </div>
                            ) : null}
                            {PRD_SECTION_TITLES.map((title) => (
                                <div key={title} className="mb-4">
                                    <h2 className="text-slate-400 font-semibold text-lg mb-1">{title}</h2>
                                    <p className="text-slate-600 italic text-sm">*Empty — start a conversation to populate this section.*</p>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            );
        }

        function PreflightScreen(props) {
            const {
                seedRoadmap,
                repoPath,
                repoPathLocked,
                roadmapSeed,
                roadmapSeedLocked,
                onRepoPathChange,
                onRepoPathLockToggle,
                onRoadmapSeedChange,
                onRoadmapSeedLockToggle,
                onBack,
                repoPathError,
                roadmapSeedError,
                onRepoPathConfirm,
                onRoadmapSeedConfirm,
                onRunPreflight,
                preflightChecks,
                onLaunch,
                launchError,
                launchDisabled,
            } = props;

            return (
                <div className="flex-1 overflow-y-auto bg-[#0d0f12] p-6">
                    <div className="max-w-2xl mx-auto space-y-6">
                        <h2 className="header-text text-lg text-slate-300">Setup &amp; Preflight</h2>
                        <div className="space-y-2">
                            <label className="text-xs text-slate-500 header-text">Repository path</label>
                            <input
                                type="text"
                                disabled={repoPathLocked}
                                readOnly={repoPathLocked}
                                value={repoPath}
                                onChange={(e) => onRepoPathChange(e.target.value)}
                                placeholder="Enter the full path to your project directory (e.g. /path/to/your-project/my-project)"
                                className={`w-full rounded border border-[#1a1d21] px-3 py-2 text-sm ${repoPathLocked ? "bg-[#0d0f12] text-slate-300" : "bg-[#1a1d21] text-slate-200"}`}
                            />
                            {repoPathError && <p className="text-red-400 text-xs">{repoPathError}</p>}
                            <div className="flex gap-2 items-center">
                                {!repoPathLocked ? (
                                    <button type="button" onClick={onRepoPathConfirm} className="header-text text-sm px-3 py-1 rounded bg-[#00b4d8] text-[#0d0f12] font-medium">
                                        Confirm
                                    </button>
                                ) : (
                                    <>
                                        <span className="text-emerald-500">✓</span>
                                        <button type="button" onClick={onRepoPathLockToggle} className="header-text text-xs text-slate-500 hover:text-slate-300">
                                            Edit
                                        </button>
                                    </>
                                )}
                            </div>
                        </div>
                        <div className="space-y-2">
                            <label className="text-xs text-slate-500 header-text">Roadmap seed</label>
                            {seedRoadmap ? (
                                <p className="text-xs text-[#00b4d8]">From Project Ideas</p>
                            ) : null}
                            {roadmapSeed ? (
                                <textarea
                                    value={roadmapSeed}
                                    onChange={(e) => onRoadmapSeedChange(e.target.value)}
                                    readOnly={roadmapSeedLocked}
                                    disabled={roadmapSeedLocked}
                                    placeholder="Paste roadmap markdown…"
                                    className={`w-full min-h-[120px] rounded border border-[#1a1d21] px-3 py-2 text-sm ${roadmapSeedLocked ? "bg-[#0d0f12] text-slate-300" : "bg-[#1a1d21] text-slate-200"}`}
                                />
                            ) : (
                                <div className="space-y-2">
                                    {!seedRoadmap ? (
                                        <input
                                            type="file"
                                            accept=".md"
                                            onChange={(e) => {
                                                const file = e.target.files && e.target.files[0];
                                                if (!file) return;
                                                const reader = new FileReader();
                                                reader.onload = () => onRoadmapSeedChange(String(reader.result || ""));
                                                reader.readAsText(file);
                                            }}
                                        />
                                    ) : null}
                                    <textarea
                                        value={roadmapSeed}
                                        onChange={(e) => onRoadmapSeedChange(e.target.value)}
                                        placeholder="Paste roadmap markdown…"
                                        className="w-full min-h-[120px] rounded border border-[#1a1d21] px-3 py-2 text-sm bg-[#1a1d21] text-slate-200"
                                    />
                                </div>
                            )}
                            {roadmapSeedError && roadmapSeedError.length > 0 && (
                                <ul className="text-red-400 text-xs space-y-1">
                                    {roadmapSeedError.map((er, i) => (
                                        <li key={i}>Line {er.line}: {er.message}</li>
                                    ))}
                                </ul>
                            )}
                            <div className="flex gap-2 items-center">
                                {!roadmapSeedLocked ? (
                                    <button type="button" onClick={onRoadmapSeedConfirm} className="header-text text-sm px-3 py-1 rounded bg-[#00b4d8] text-[#0d0f12] font-medium">
                                        Confirm
                                    </button>
                                ) : (
                                    <>
                                        <span className="text-emerald-500">✓</span>
                                        <button type="button" onClick={onRoadmapSeedLockToggle} className="header-text text-xs text-slate-500 hover:text-slate-300">
                                            Edit
                                        </button>
                                    </>
                                )}
                            </div>
                        </div>
                        <div className="flex gap-3 flex-wrap">
                            <button type="button" onClick={onRunPreflight} disabled={!repoPathLocked || !roadmapSeedLocked} className="header-text text-sm px-4 py-2 rounded bg-[#1a1d21] text-slate-200 border border-[#1a1d21] disabled:opacity-40">
                                Run Preflight
                            </button>
                            <button type="button" onClick={onLaunch} disabled={launchDisabled} className="header-text text-sm px-4 py-2 rounded bg-[#00b4d8] text-[#0d0f12] font-medium disabled:opacity-40">
                                Launch
                            </button>
                            <button type="button" onClick={onBack} className="header-text text-sm px-4 py-2 rounded border border-[#1a1d21] text-slate-400">
                                Back
                            </button>
                        </div>
                        {launchError && <p className="text-red-400 text-sm whitespace-pre-wrap">{launchError}</p>}
                        {preflightChecks && preflightChecks.length > 0 && (
                            <ul className="space-y-2 text-sm">
                                {preflightChecks.map((c, i) => (
                                    <li key={i} className={`flex gap-2 ${c.status === "fail" ? "text-red-400" : c.status === "warn" ? "text-amber-400" : c.status === "fixed" ? "text-[#00b4d8]" : "text-emerald-400"}`}>
                                        <span className="font-mono text-xs">[{c.status}]</span>
                                        <span>{c.check}: {c.message}</span>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </div>
            );
        }

"""

def main():
    root = Path(__file__).resolve().parent
    html = (root / "index.html").read_text()
    ph = html.find("        // ─── Placeholder screens")
    pi = html.find("        function PipelineScreen()")
    assert ph != -1 and pi != -1
    out = html[:ph] + MIDDLE + html[pi:]
    (root / "index.html").write_text(out)
    print("Spliced OK, new length", len(out))


if __name__ == "__main__":
    main()
