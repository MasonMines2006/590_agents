# Bootstrapping `agents590` toward a Claude Code-like agent

**Status:** planning only  
**Scope:** `590_agents` repository and the user's local files/Obsidian vault  
**Principle:** do not begin implementation until the safety, persistence, and interaction contracts below are agreed.

## 1. Starting point and target

The repository already has a useful foundation:

- one shared `agents590` CLI with agent selection, `--cwd`, model selection, and optional dotenv loading;
- a typed `Agent.run(instruction, context)` boundary;
- a model/tool loop in `masons_special_agent`;
- shell, read-file, and write-file capabilities;
- a shell command allowlist, timeouts, output truncation, and non-interactive Git settings;
- tests around the CLI, provider, registry, agent behavior, and tracing.

The target is a dependable local coding and knowledge-work assistant, not merely a model wrapper. It should be able to inspect a repository, make bounded changes, verify them, explain what it did, and persist plans and resumable state. It should feel conversational and transparent while retaining explicit user control over consequential actions.

Important gaps to address before calling it production-ready:

- the current CLI is largely one-shot and has no session/resume model;
- tool authorization is mostly a command-name allowlist, rather than a path- and operation-aware policy;
- `write_file` can write arbitrary paths reachable by the process;
- shell parsing is permissive (`split()` checks only the first word), so shell operators and interpolated commands need a stronger policy;
- there is no approval workflow, dry run, rollback, or change preview;
- plans, transcripts, and tool results are not durably organized;
- Obsidian integration and local file permissions have not been defined;
- failures, interruptions, retries, and partial work need a recoverable state model.

## 2. Product goals and non-goals

### Goals

1. **Safe autonomy:** allow useful multi-step work while making filesystem, shell, network, deletion, and credential-impacting actions understandable and controllable.
2. **Excellent CLI UX:** support both quick commands and long-running interactive sessions without breaking the existing `-i/--instruction` contract used by evaluation tooling.
3. **Persistent memory:** save plans, decisions, summaries, checkpoints, and run metadata in a human-readable local location, with optional Obsidian vault synchronization.
4. **Reproducibility:** make every run inspectable: inputs, model, tools, approvals, files touched, test results, and errors.
5. **Incremental delivery:** preserve the current simple agents and add capabilities behind explicit profiles or modes.

### Non-goals for the first release

- unrestricted autonomous operation across the entire computer;
- silently modifying an Obsidian vault or arbitrary external directories;
- a cloud-hosted memory service;
- pretending that model output is verified when tests or tools were not run;
- replacing Git as the source of truth for code history.

## 3. Proposed user experience and CLI roadmap

### Phase A: compatible command improvements

Keep existing invocation behavior intact:

- `agents590 <agent> -i "..."`
- `--cwd`, `--model`, `--env-file`, and `--list`

Add a clearly documented run profile and observability options:

- interactive versus one-shot mode;
- `--plan` for plan-only output with no mutations;
- `--auto-approve` only as an explicit opt-in, never a default;
- `--resume <run-id>` and `--session <name>`;
- `--state-dir <path>` and `--vault <path>`;
- machine-readable output (JSON Lines) separately from friendly terminal output;
- quiet, verbose, and trace/log output controls;
- bounded limits for turns, time, tool calls, and estimated spend.

Do not remove or rename the existing `-i/--instruction` interface.

### Phase B: interactive session

Provide a REPL with:

- visible current working directory, session, model, and safety profile;
- an explicit distinction between user messages, proposed actions, tool output, and final summaries;
- commands such as help, status, plan, approve, deny, diff, checkpoint, resume, compact, and exit;
- interruption that stops the current tool/model turn cleanly and records the interruption;
- confirmation prompts that show the exact command/path, reason, expected impact, and affected scope;
- a final concise summary plus links to the persisted plan and transcript.

The REPL should not be required for automation or Harbor-compatible one-shot runs.

### Phase C: task-oriented workflows

Add named workflows rather than one giant “do anything” mode:

1. **Explore:** read-only repository analysis.
2. **Plan:** produce an ordered plan, risks, affected files, and verification steps.
3. **Implement:** apply approved changes and run verification.
4. **Review:** inspect a diff and test results without editing.
5. **Remember:** update durable project/session notes.
6. **Resume:** reload a checkpoint and continue only after showing the pending actions.

## 4. Safety and reliability design

### Capability and path policy

Use a policy that evaluates the complete operation, not just the first shell token:

- default workspace is the resolved `--cwd` and its permitted descendants;
- require explicit approval for paths outside the workspace, including the Obsidian vault;
- canonicalize paths before checks and account for symlinks, `..`, and path traversal;
- separate read, create, modify, rename, delete, execute, network, and credential capabilities;
- protect `.env`, SSH keys, cloud credentials, browser profiles, and other secret-like paths by default;
- define whether symlink targets may be followed; safest initial behavior is to deny escapes;
- use an explicit allowlist for external directories, persisted per session only when the user approves it.

### Shell execution

Prefer structured tools for common operations (list, search, read, write, patch, test) over arbitrary shell strings. For any remaining shell tool:

- parse commands rather than checking `split()` output;
- reject or separately authorize pipes, redirects, command substitution, backgrounding, `&&`, `;`, environment mutation, and shell spawning;
- pass arguments without a shell where possible;
- enforce timeout, output, process-count, and resource limits;
- run in the selected workspace with a sanitized environment;
- classify commands by risk (read-only, build/test, network, destructive, privilege escalation);
- never permit `sudo`, credential exfiltration, or hidden interactive prompts by default.

### Approval and change control

For each mutation, show a proposed action before execution. For file changes, show a diff or bounded preview. Require approval for:

- deletion or overwrite of existing files;
- writes outside the workspace or into the vault;
- network requests, package installation, commits, pushes, or migrations;
- commands classified as destructive or privileged;
- actions involving possible secrets.

Support deny, edit/modify request, approve once, and approve for this exact operation/session. Record the decision and policy version in the run log.

### Recovery and verification

- create checkpoints before multi-file mutations;
- maintain an operation journal and use atomic writes where possible;
- never claim success from a tool call alone: report verification status explicitly;
- run targeted tests first, then broader checks selected by the plan;
- preserve failed command output and the last safe checkpoint;
- on restart, detect incomplete runs and offer resume, inspect, or abandon;
- use Git diff/status as a review aid, but do not automatically discard user changes.

## 5. Persistent plans, state, and Obsidian integration

### Storage model

Use a local state directory with a stable, documented layout. The exact directory can be configured, but the default should be outside the code repository to avoid accidental commits. A run should have:

- a human-readable plan document;
- a machine-readable manifest containing run/session IDs, timestamps, model, working directory, profile, policy version, and status;
- an append-only event log for user messages, model responses, tool calls, approvals, results, errors, and checkpoints;
- a final summary with files changed, verification performed, unresolved risks, and next steps;
- optional compacted context for resuming without loading an unbounded transcript.

Use unique IDs and atomic replacement. Treat persisted state as potentially sensitive; document permissions, retention, redaction, and how to delete it.

### Plan lifecycle

1. Create a plan before implementation when the task is more than trivial.
2. Give it a stable ID and status (`draft`, `approved`, `in_progress`, `blocked`, `completed`, `abandoned`).
3. Record assumptions, scope, ordered steps, approval requirements, and verification criteria.
4. Update step status after each meaningful action.
5. Save a final summary and outstanding follow-ups, even on failure or cancellation.

Plans should remain readable and editable by a person; the agent must reread an edited plan and ask about contradictory changes rather than silently overwriting them.

### Obsidian adapter

Treat Obsidian as an optional filesystem-backed presentation and review layer, not as the only database:

- configure a vault root explicitly; never discover or write to an arbitrary vault automatically;
- choose a dedicated folder such as `Agents590/` and allow the user to configure it;
- create Markdown notes with YAML front matter for run ID, session, project, status, created/updated times, and tags;
- link plan notes to run summaries and related project notes using normal Obsidian links;
- use deterministic filenames and collision handling; never overwrite a note that was changed by the user without a diff/approval;
- keep large/raw transcripts in the state directory and link to them from Obsidian, unless the user opts in to full transcript notes;
- support export/sync first; defer two-way sync and conflict resolution until the local format is proven;
- redact secrets and sensitive tool output before export;
- provide a dry-run preview of every vault write.

A useful initial note set is: `Plans/<project>-<plan-id>.md`, `Runs/<run-id>.md`, and optionally a project index. The plan itself should be the canonical human-facing artifact; generated run logs should not clutter the vault by default.

## 6. Architecture and boundaries

Separate responsibilities so safety does not depend on model instructions:

1. **CLI/session layer:** arguments, REPL, rendering, interruptions, and exit codes.
2. **Orchestrator:** turn loop, budgets, state transitions, retries, and checkpoints.
3. **Policy/approval engine:** capability checks, path checks, risk classification, and user decisions.
4. **Tool layer:** typed read/write/search/patch/process tools with consistent result envelopes.
5. **Persistence layer:** manifests, event log, plans, summaries, and recovery.
6. **Vault adapter:** explicit Markdown export and later conflict-aware synchronization.
7. **Provider layer:** model configuration, streaming, retries, cancellation, and usage accounting.
8. **Presentation layer:** terminal output and machine-readable output.

Keep the shared `Agent` protocol and the existing entry-point registry compatible while introducing richer context/services in a backwards-compatible manner. Existing simple agents should continue to work without persistence or extra permissions.

## 7. Testing and evaluation plan

Before each phase is considered complete, add tests for:

- CLI compatibility, invalid arguments, session/resume behavior, and exit codes;
- path canonicalization, workspace boundaries, symlinks, protected files, and external-directory approval;
- shell parsing and rejection of operators, injection, background processes, and interactive commands;
- approval decisions, dry runs, denial behavior, and policy-version recording;
- atomic persistence, crash recovery, corrupted state, truncation, redaction, and concurrent access;
- plan status transitions and preservation of user-edited plans;
- Obsidian filename collisions, front matter, dry-run export, and vault boundary enforcement;
- model/tool retries, timeouts, cancellation, budget exhaustion, and partial results;
- end-to-end runs with mocked model responses and no live API calls.

Add adversarial tests specifically for prompt injection in repository files, malicious filenames, symlink escapes, secret leakage in logs, and instructions that attempt to override the safety policy. Measure useful completion rate, unauthorized-action rate, recovery success, latency, token/cost usage, and percentage of runs with reproducible summaries.

## 8. Delivery milestones

### Milestone 0 — decisions and threat model

- agree on default state location, vault location, retention, and privacy expectations;
- define risk classes and approval defaults;
- document supported platforms and process/resource limits;
- establish a small set of representative tasks and safety abuse cases.

**Exit:** written policy and UX examples reviewed by the user.

### Milestone 1 — durable one-shot runs

- persist plan/manifest/events/summary for existing one-shot execution;
- add explicit dry-run/plan mode and bounded budgets;
- preserve current CLI and existing agents;
- record interruptions and failures.

**Exit:** kill-and-resume/review scenarios are understandable and do not lose state.

### Milestone 2 — hardened tools and approvals

- replace permissive command checks with structured tools and policy enforcement;
- add previews, diffs, approvals, protected paths, and atomic writes;
- add adversarial security tests.

**Exit:** unsafe examples are denied or require clear approval.

### Milestone 3 — interactive CLI

- add REPL, session commands, streaming rendering, cancellation, and resume;
- offer human and JSONL output modes;
- add task/workflow profiles.

**Exit:** a user can complete and review a normal repository task without losing context.

### Milestone 4 — Obsidian export

- add explicit vault configuration and a read-only/dry-run preview;
- export plans and summaries with metadata and links;
- add redaction and conflict-safe behavior.

**Exit:** vault writes are predictable, reviewable, and never silently overwrite user notes.

### Milestone 5 — reliability and polish

- improve compaction, retries, cost controls, performance, accessibility, and documentation;
- test on clean environments and representative repositories;
- decide whether two-way Obsidian sync is worth its conflict and privacy cost.

## 9. Open decisions to resolve first

1. Should the default persistent state live under the user config/data directory, a project-local `.agents590/`, or both?
2. Which Obsidian vault and subfolder should be the initial explicit default, if any?
3. Should plan files be committed with projects, ignored, or selected per project?
4. Is approval required for every write, or can a user-approved session profile allow bounded writes?
5. Which network operations, if any, are needed for the first useful workflow?
6. What is the desired retention period and redaction policy for prompts, transcripts, and tool output?
7. Should Git checkpoints be automatic, or should the agent only produce diffs and let the user commit?
8. Which model/provider features (streaming, tool calls, usage accounting) are reliable enough to make part of the first contract?
9. What is the acceptable failure mode when a model asks for an unsupported or ambiguous action: deny, ask, or fall back to read-only?

## Recommended first build slice

Start with **Milestone 0 plus the persistence portion of Milestone 1**: define the policy, then make every existing one-shot run produce a durable plan/manifest/event log/summary without expanding permissions. This creates inspectable history and a safe foundation for the later CLI, approvals, and Obsidian adapter, while avoiding the risk of adding broad autonomy before its boundaries are testable.
