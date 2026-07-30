"""Host builtin tool handlers (entry points referenced by runner.json).

- files.py — sandbox FS tools (read_file, list_dir, grep, search_replace)
- run_cmd.py — run
- social.py — speak, wait_user, schedule_wake
- ledger.py — create_goal, create_task, list_goals, get_goal, get_task, update_task, update_goal
- skills_tools.py — load_skill
- growth.py — install_tool_draft, verify_tool, promote_tool, install_skill
- identity.py — get_identity, draft_identity, promote_identity
- package_vcs.py — get/revert tool+skill package VCS
- search.py — web_search (optional elyra[search] / ddgs)
- browser.py — browser_session_open/close, goto, snapshot, click/type/fill, get_text, wait
- secrets_tools.py — secrets_list, secrets_set, secrets_delete
- git_tools.py — frozen git_* / worktree builtins (path-jailed host)
- gh_tools.py — frozen gh_* PR/issue/project builtins (GH_TOKEN soft-fail)
- sandbox_packages.py — sandbox_pip_update (allowlist-add guest curated env)
- memory_traverse.py — memory_traverse_start/step/inspect/finish/abandon (Phase 2a)
"""
