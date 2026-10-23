# Project Rules & Safety Guidelines (sanalcadir-bot)

## 🛡️ Critical Safety & Command Policies (Turbo Mode Guardrails)

The agent must strictly adhere to the following command execution guidelines:

### 1. Prohibited Commands (DENY - Never Run)
Under no circumstances should the agent execute or attempt to execute:
- Force-push git operations (`git push --force`, `git push -f`).
- Destructive root/system deletion (`rm -rf /`, `rm -rf ~`, `Remove-Item * -Recurse -Force` on system/root folders).
- Database drop/truncate operations (`dropdb`, `DROP DATABASE`).
- Drive format operations (`format`, `mkfs`).

### 2. Guarded Commands (ASK - Always Prompt for User Approval)
The agent must ask for explicit user consent before executing:
- Normal remote push operations (`git push`).
- Destructive git state resets or cleans (`git reset --hard`, `git clean -fd`).
- Deleting directories or bulk files.
- Uninstalling dependencies or packages.

### 3. File Access Scope
- Always operate strictly within the project workspace directory.
- Do not create, modify, or delete files outside of this workspace unless explicitly commanded by the user.
