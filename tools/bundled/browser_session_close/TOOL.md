---
name: browser_session_close
description: Close a browser session and free Chromium resources. Prefer explicit close when finished; moment end also cleans up.
kind: mutate
---

# browser_session_close

Close `session_id` and release Chromium. Unknown ids return `session_not_found`.
Moment finalize / fail paths also call close-for-moment — still prefer explicit
close when you finish early.

