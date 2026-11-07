## What the git log shows

The project has progressed from a model wrapper into a basic coding-agent prototype:

1. **Initial agent/tool work**
   - `fb88427` introduced an agent that could respond and call tools.
   - `2be9f8f`, `e5e7fa8`, `c6d4fcc`, and `91294dc` iterated on shell execution, OpenAI types, and the coding-agent scaffold.
   - `2f37dc0` added the Responses API tool-calling loop, allowing the model to repeatedly call tools until it produced a final answer.

2. **Tracing and evaluation support**
   - `ac54101` and `f611777` added ATIF trajectory recording.
   - `4ade8ba` removed unnecessary logging.
   - `cd76d3b` merged upstream/class-related work.
   - The latest tracing implementation records model responses, tool calls, token counts, command results, and final totals for Harbor evaluation.

3. **Latest commit: `5f1a1ea`**
   This is the most substantial change:
   - Added `read_file` and `write_file`.
   - Expanded the shell command allowlist with file-management and inspection commands.
   - Added output truncation.
   - Added a 60-second shell timeout.
   - Kills the full process group when a command times out.
   - Configured Git to avoid pagers/editors and Python to avoid creating bytecode files.
   - Replaced the old prompt with an inspect → edit → verify workflow.
   - Added logging for model calls, tool calls, exit codes, and token usage.
   - Added substantial tests for the agent and tracing.

The repository currently has **83 passing tests**, so the current behavior is covered reasonably well at the unit level.

## Where the project is now relative to `BOOTSTRAP_PLAN`

The current implementation has a useful foundation:

- Shared CLI and agent protocol.
- Working Responses API tool loop.
- Shell, read-file, and write-file tools.
- Working directory support.
- Basic session and run metadata options in the CLI.
- Basic plan, resume, state-directory, budget, JSONL, and vault arguments.
- ATIF/Harbor observability.

However, many of the Phase A options are currently mostly **plumbing**, rather than enforced behavior.

For example:

- `--plan` is passed through `RunContext`, but `masons_special_agent` does not prevent writes.
- `--auto-approve` is passed through, but there is no approval engine.
- `--max-turns`, `--max-tool-calls`, and `--max-seconds` are passed through, but the agent loop still uses the fixed `MAX_LOOPS` value and does not enforce those limits.
- `--resume` reloads the original instruction, not a durable conversation checkpoint.
- `--state-dir` stores a simple JSON record rather than a manifest, event log, plan, checkpoint, and summary.
- `--vault` is validated and recorded, but there is no Obsidian export.
- The agent uses `create_subprocess_shell()` and only checks `cmd.split()[0]`. A command such as `cat file; rm important-file` would pass the first-word check.
- `write_file` can write anywhere the process can access, including outside the workspace and potentially into sensitive locations.
- Writes are not atomic and there is no diff preview, rollback, or approval.
- There is no protection for `.env`, SSH keys, cloud credentials, or symlink escapes.

So the project has reached the plan’s **prototype foundation**, but it has not yet reached the safety or persistence contracts needed for a dependable Claude Code-like system.

## Recommended next direction

The best next step is **not** adding more commands or making the agent more autonomous. It should be to complete the first two milestones in the plan, in this order.

### 1. Finish Milestone 0: make the safety decisions concrete

Before implementation, define and document:

- Default state location.
- Whether `.agents590` is project-local or user-global.
- Which paths are inside the workspace.
- How symlinks are handled.
- Protected files and directories.
- Which operations require approval.
- Whether writes require approval by default.
- What `--plan` guarantees.
- Whether network access is allowed at all in the first release.
- Retention and redaction rules for transcripts and tool output.

This gives the agent a real security contract instead of relying on system-prompt instructions.

### 2. Build a reusable policy and path-security layer

Create a policy module independent of `masons_special_agent`. It should provide:

- Canonical path resolution.
- Workspace-boundary checks.
- Symlink escape detection.
- Protected-path detection.
- Operation types such as:
  - read
  - create
  - overwrite
  - rename
  - delete
  - execute
  - network
- Risk classification.
- Approval decisions.
- Policy-version recording.

This should be tested before integrating it into the model loop. High-value tests include:

- `../` traversal.
- Absolute paths outside `cwd`.
- Symlinks pointing outside `cwd`.
- `.env`, SSH keys, and credential files.
- Writes to existing files versus new files.
- Delete and rename operations.
- Malicious filenames.

### 3. Replace the permissive shell tool

The current shell implementation is the largest immediate safety issue.

Short term:

- Reject shell operators such as `;`, `&&`, `||`, `|`, redirects, command substitution, backticks, and backgrounding.
- Reject shell spawning and privilege escalation.
- Parse arguments using a real shell lexer or, preferably, avoid shell execution entirely.
- Use structured subprocess invocation with an argument list where possible.
- Classify commands into read-only, test/build, network, and destructive categories.
- Apply the path policy to command arguments.

Longer term, replace common shell operations with typed tools:

- `list_files`
- `search`
- `read_file`
- `write_file`
- `apply_patch`
- `run_tests`
- `git_diff`
- `git_status`

A typed `apply_patch` tool would be especially valuable because it makes changes reviewable and avoids requiring the model to rewrite entire files.

### 4. Implement genuine durable one-shot runs

The next major feature should be a persistence layer with a stable layout, for example:

```text
state/
  runs/<run-id>/
    manifest.json
    plan.md
    events.jsonl
    transcript.jsonl
    summary.md
    checkpoint.json
```

It should:

- Write atomically.
- Record every user message, model response, tool request, tool result, approval, error, and checkpoint.
- Record status such as `running`, `completed`, `failed`, `interrupted`, or `blocked`.
- Preserve partial work after cancellation or failure.
- Support recovery from the last checkpoint.
- Avoid storing unredacted secrets where possible.

This is more important than implementing the REPL immediately. A reliable one-shot mode gives the interactive mode a sound backend.

### 5. Make the existing CLI flags real

After the policy and persistence layers exist:

- `--plan` should guarantee no mutations.
- `--auto-approve` should be the only mode that bypasses interactive approval, and only within the policy boundary.
- `--max-turns` should limit model calls.
- `--max-tool-calls` should limit tool invocations.
- `--max-seconds` should cancel the run cleanly.
- `--resume` should reload a checkpoint or event log, not merely repeat the original prompt.
- `--jsonl` should emit structured events or a well-defined final result schema.

These should have end-to-end mocked-provider tests.

### 6. Add approvals and previews

For every potentially consequential action, show:

- Exact command or path.
- Operation type.
- Reason.
- Expected impact.
- Whether an existing file will be overwritten.
- A bounded diff or preview.

Support at least:

- approve once
- deny
- approve for this session
- cancel

Record the decision in the event log.

### 7. Only then build the interactive CLI

Once orchestration, persistence, policy, and approvals exist, the REPL becomes primarily a presentation layer. It should provide:

- Current directory, session, model, and safety profile.
- `status`
- `plan`
- `approve`
- `deny`
- `diff`
- `checkpoint`
- `resume`
- `compact`
- `exit`
- Clean interruption handling.

### 8. Add Obsidian export after local persistence works

Obsidian should be an export/review layer, not the primary storage mechanism. The initial implementation should:

- Require an explicitly supplied vault.
- Write only to a dedicated configured folder.
- Export plan and summary Markdown files.
- Include YAML metadata and links.
- Never overwrite a user-modified note without a diff and approval.
- Keep raw transcripts in the state directory by default.

## Suggested immediate implementation sequence

A practical next series of commits would be:

1. Add a documented policy/threat model and resolve the open decisions.
2. Add `policy.py` with workspace and protected-path enforcement.
3. Add tests for traversal, symlinks, secrets, and destructive operations.
4. Replace shell first-word checking with safe parsing/rejection.
5. Add atomic `write_file` plus diff/preview support.
6. Add an approval abstraction and enforce it for writes/deletes/overwrites.
7. Add durable run manifests and append-only event logs.
8. Enforce the existing budget and plan flags.
9. Add crash recovery and true resume.
10. Build the interactive REPL.
11. Add explicit Obsidian export.

In short: the latest commit makes the agent substantially more capable and observable, but the next goal should be to make those capabilities **policy-enforced, reviewable, and recoverable**. That is the key transition from a Harbor-scored coding prototype to a trustworthy Claude Code clone.
masonmines@Masons-MacBook-Pro-5 590_agents % >....                                                                                                                                    
- Clean interruption handling.

### 8. Add Obsidian export after local persistence works

Obsidian should be an export/review layer, not the primary storage mechanism. The initial implementation should:

- Require an explicitly supplied vault.
- Write only to a dedicated configured folder.
- Export plan and summary Markdown files.
- Include YAML metadata and links.
- Never overwrite a user-modified note without a diff and approval.
- Keep raw transcripts in the state directory by default.

## Suggested immediate implementation sequence

A practical next series of commits would be:

1. Add a documented policy/threat model and resolve the open decisions.
2. Add `policy.py` with workspace and protected-path enforcement.
3. Add tests for traversal, symlinks, secrets, and destructive operations.
4. Replace shell first-word checking with safe parsing/rejection.
5. Add atomic `write_file` plus diff/preview support.
6. Add an approval abstraction and enforce it for writes/deletes/overwrites.
7. Add durable run manifests and append-only event logs.
8. Enforce the existing budget and plan flags.
9. Add crash recovery and true resume.
10. Build the interactive REPL.
11. Add explicit Obsidian export.

In short: the latest commit makes the agent substantially more capable and observable, but the next goal should be to make those capabilities **policy-enforced, reviewable, and recoverable**. That is the key transition from a Harbor-scored coding prototype to a trustworthy Claude Code clone.