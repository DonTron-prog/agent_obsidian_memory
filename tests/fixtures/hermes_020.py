"""Independent synthetic Hermes 0.20.0 carrier fixture.

Frozen from ~/.hermes/hermes-agent at commit
bc80a0be5c1b496a6212a1c6c594b3c5a78e31c6. No production database is opened.
"""

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into "
    "the summary below. This is a handoff from a previous context window — tr"
    "eat it as background reference, NOT as active instructions. Do NOT answe"
    "r questions or fulfill requests mentioned in this summary; they were alr"
    "eady addressed. Respond ONLY to the latest user message that appears AFT"
    "ER this summary — that message is the single source of truth for what to"
    " do right now. If no user message appears AFTER this summary, do nothing"
    ": do not resume, wrap up, or continue work from '## Historical Task Snap"
    "shot' or any other section, do not call tools, and wait for a new user m"
    "essage. This handoff must never become the active turn by itself. (Excep"
    "tion: if tool results or your own tool calls appear after this summary, "
    "you are mid-way through an in-flight exchange — continue that exchange n"
    "ormally.) Topic overlap with the summary does NOT mean you should resume"
    " its task: even on similar topics, the latest user message WINS. Treat O"
    "NLY the latest message as the active task and discard stale items from '"
    "## Historical Task Snapshot' entirely — do not 'wrap up' or 'finish' wor"
    "k described there unless the latest message explicitly asks for it. Reve"
    "rse signals in the latest message (e.g. 'stop', 'undo', 'roll back', 'ju"
    "st verify', 'don't do that anymore', 'never mind', a new topic) must imm"
    "ediately end any in-flight work described in the summary; do not re-surf"
    "ace it in later turns. IMPORTANT: Your persistent memory (MEMORY.md, USE"
    "R.md) in the system prompt is ALWAYS authoritative and active — never ig"
    "nore or deprioritize memory content due to this compaction note. None of"
    " the above restricts HOW you work: your tools remain fully active — keep"
    " calling them normally for the active task (edit files, run commands, se"
    "arch) instead of merely narrating what you would do. The current session"
    " state (files, config, etc.) may reflect work described here — avoid rep"
    "eating it:"
)

LEGACY_SUMMARY_PREFIX = "[CONTEXT SUMMARY]:"

HISTORICAL_SUMMARY_PREFIXES = (
    (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into "
        "the summary below. This is a handoff from a previous context window — tr"
        "eat it as background reference, NOT as active instructions. Do NOT answe"
        "r questions or fulfill requests mentioned in this summary; they were alr"
        "eady addressed. Respond ONLY to the latest user message that appears AFT"
        "ER this summary — that message is the single source of truth for what to"
        " do right now. Topic overlap with the summary does NOT mean you should r"
        "esume its task: even on similar topics, the latest user message WINS. Tr"
        "eat ONLY the latest message as the active task and discard stale items f"
        "rom '## Historical Task Snapshot' entirely — do not 'wrap up' or 'finish"
        "' work described there unless the latest message explicitly asks for it."
        " Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll back'"
        ", 'just verify', 'don't do that anymore', 'never mind', a new topic) mus"
        "t immediately end any in-flight work described in the summary; do not re"
        "-surface it in later turns. IMPORTANT: Your persistent memory (MEMORY.md"
        ", USER.md) in the system prompt is ALWAYS authoritative and active — nev"
        "er ignore or deprioritize memory content due to this compaction note. No"
        "ne of the above restricts HOW you work: your tools remain fully active —"
        " keep calling them normally for the active task (edit files, run command"
        "s, search) instead of merely narrating what you would do. The current se"
        "ssion state (files, config, etc.) may reflect work described here — avoi"
        "d repeating it:"
    ),
    (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into "
        "the summary below. This is a handoff from a previous context window — tr"
        "eat it as background reference, NOT as active instructions. Do NOT answe"
        "r questions or fulfill requests mentioned in this summary; they were alr"
        "eady addressed. Respond ONLY to the latest user message that appears AFT"
        "ER this summary — that message is the single source of truth for what to"
        " do right now. Topic overlap with the summary does NOT mean you should r"
        "esume its task: even on similar topics, the latest user message WINS. Tr"
        "eat ONLY the latest message as the active task and discard stale items f"
        "rom '## Historical Task Snapshot' / '## Historical In-Progress State' / "
        "'## Historical Pending User Asks' / '## Historical Remaining Work' entir"
        "ely — do not 'wrap up' or 'finish' work described there unless the lates"
        "t message explicitly asks for it. Reverse signals in the latest message "
        "(e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore"
        "', 'never mind', a new topic) must immediately end any in-flight work de"
        "scribed in the summary; do not re-surface it in later turns. IMPORTANT: "
        "Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWA"
        "YS authoritative and active — never ignore or deprioritize memory conten"
        "t due to this compaction note. None of the above restricts HOW you work:"
        " your tools remain fully active — keep calling them normally for the act"
        "ive task (edit files, run commands, search) instead of merely narrating "
        "what you would do. The current session state (files, config, etc.) may r"
        "eflect work described here — avoid repeating it:"
    ),
    (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into "
        "the summary below. This is a handoff from a previous context window — tr"
        "eat it as background reference, NOT as active instructions. Do NOT answe"
        "r questions or fulfill requests mentioned in this summary; they were alr"
        "eady addressed. Respond ONLY to the latest user message that appears AFT"
        "ER this summary — that message is the single source of truth for what to"
        " do right now. Topic overlap with the summary does NOT mean you should r"
        "esume its task: even on similar topics, the latest user message WINS. Tr"
        "eat ONLY the latest message as the active task and discard stale items f"
        "rom '## Historical Task Snapshot' / '## Historical In-Progress State' / "
        "'## Historical Pending User Asks' / '## Historical Remaining Work' entir"
        "ely — do not 'wrap up' or 'finish' work described there unless the lates"
        "t message explicitly asks for it. Reverse signals in the latest message "
        "(e.g. 'stop', 'undo', 'roll back', 'just verify', 'don't do that anymore"
        "', 'never mind', a new topic) must immediately end any in-flight work de"
        "scribed in the summary; do not re-surface it in later turns. IMPORTANT: "
        "Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWA"
        "YS authoritative and active — never ignore or deprioritize memory conten"
        "t due to this compaction note. The current session state (files, config,"
        " etc.) may reflect work described here — avoid repeating it:"
    ),
    (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into "
        "the summary below. This is a handoff from a previous context window — tr"
        "eat it as background reference, NOT as active instructions. Do NOT answe"
        "r questions or fulfill requests mentioned in this summary; they were alr"
        "eady addressed. Respond ONLY to the latest user message that appears AFT"
        "ER this summary — that message is the single source of truth for what to"
        " do right now. If the latest user message is consistent with the '## Act"
        "ive Task' section, you may use the summary as background. If the latest "
        "user message contradicts, supersedes, changes topic from, or in any way "
        "diverges from '## Active Task' / '## In Progress' / '## Pending User Ask"
        "s' / '## Remaining Work', the latest message WINS — discard those stale "
        "items entirely and do not 'wrap up the old task first'. Reverse signals "
        "in the latest message (e.g. 'stop', 'undo', 'roll back', 'just verify', "
        "'don't do that anymore', 'never mind', a new topic) must immediately end"
        " any in-flight work described in the summary; do not re-surface it in la"
        "ter turns. IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the"
        " system prompt is ALWAYS authoritative and active — never ignore or depr"
        "ioritize memory content due to this compaction note. The current session"
        " state (files, config, etc.) may reflect work described here — avoid rep"
        "eating it:"
    ),
    (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into "
        "the summary below. This is a handoff from a previous context window — tr"
        "eat it as background reference, NOT as active instructions. Do NOT answe"
        "r questions or fulfill requests mentioned in this summary; they were alr"
        "eady addressed. Your current task is identified in the '## Active Task' "
        "section of the summary — resume exactly from there. Respond ONLY to the "
        "latest user message that appears AFTER this summary. The current session"
        " state (files, config, etc.) may reflect work described here — avoid rep"
        "eating it:"
    ),
)

RECOGNIZED_SUMMARY_PREFIXES = (
    SUMMARY_PREFIX,
    *HISTORICAL_SUMMARY_PREFIXES,
    LEGACY_SUMMARY_PREFIX,
)

SUMMARY_END_MARKER = (
    "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
)

MERGED_PRIOR_CONTEXT_HEADER = "[PRIOR CONTEXT — for reference only; not a new message]"

MERGED_SUMMARY_DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"

MESSAGES_SCHEMA = """
CREATE TABLE messages (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
 active INTEGER NOT NULL DEFAULT 1, compacted INTEGER NOT NULL DEFAULT 0
);
"""
