---
description: Update the project map document after significant code changes
---

# Update PROJECT_MAP.md

Run this workflow whenever you add/remove agents, change key module responsibilities, or make architectural changes.

## Steps

1. Review the current `PROJECT_MAP.md` at the project root to understand what needs updating.

2. Scan changed files to identify impacted sections:
```bash
git diff --name-only HEAD~1 HEAD
```

3. Update `PROJECT_MAP.md` to reflect the changes:
   - Add/remove entries in the Module Registry table
   - Update the dependency graph if imports changed
   - Update the Feature → File mapping if new capabilities were added
   - Bump the `Last Updated` date at the top

4. Commit the map update together with the code changes:
```bash
git add PROJECT_MAP.md && git commit --amend --no-edit
```
