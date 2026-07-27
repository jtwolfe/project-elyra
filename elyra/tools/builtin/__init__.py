"""Host builtin tool handlers (entry points referenced by runner.json).

- files.py — sandbox FS tools (read_file, list_dir, grep, search_replace)
- run_cmd.py — run
- social.py — speak, wait_user, schedule_wake
- ledger.py — create_goal, create_task, list_goals, get_goal, get_task, update_task, update_goal
- skills_tools.py — load_skill
- growth.py — install_tool_draft, verify_tool, promote_tool, install_skill
- package_vcs.py — get_tool, revert_tool (package archive/recovery)
- identity.py — get_identity, draft_identity, promote_identity
- search.py — web_search (optional elyra[search] / ddgs)
- secrets_tools.py — secrets_list, secrets_set, secrets_delete
"""
