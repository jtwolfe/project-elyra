---
name: read_file
description: Read a text file from the sandbox workspace by relative path.
kind: read
---

# read_file

Read a UTF-8 text file under the persistent sandbox root. Path is jailed;
escapes return a tool error (never host paths outside the sandbox).
