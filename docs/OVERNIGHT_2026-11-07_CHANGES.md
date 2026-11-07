# Overnight changes, 2026-11-07

Branch: `overnight/agent-bootstrap-2026-11-07`, cut from `mmines/fix-agent-tools`.
Nothing was pushed. No real model was called: every new test uses the fake model
in `tests/fake_model.py`.

Before any code was written, I checked for a course rule that forbids AI-written
code in this assignment. I looked at the README, `AGENTS.md`, `docs/`, `.github/`,
and the COMP 590 notes in your vault, and didn't find one. The only policy notes
I found say to check 590H's academic-honesty policy before making work *public*.
**Check the real 590H policy yourself before you submit.** I couldn't see the
syllabus.

## The commits, in order

| Commit | What it does |
| --- | --- |
| `wip: uncommitted work from 11/03 session, unreviewed` | Your 11/03 work exactly as it was: `cli.py`, `contracts.py`, `persistence.py`, `policy.py`, and `docs/`. It's committed alone so that every later change is its own small diff. The `.agents590/` folder was **not** committed. |
| `chore: ignore .agents590/ run records` | Adds `.agents590/` to `.gitignore`. |
| `fix: only write .agents590 run records when persistence is requested` | Step 1a–c, plus tests. |
| `feat: honor --plan, --max-turns and --max-tool-calls; drop unwired flags` | Step 1e, plus tests. |
| `feat: add the working directory's AGENTS.md to the system prompt` | Step 5, plus tests. |
| `feat: add skills (load_skill) and subagents (delegate)` | Step 6, plus tests. |
| `feat: add make sandbox and keep API keys out of the shell tool's environment` | Step 7, plus a test. |
| `refactor: remove resolve_path, unused since check_path replaced it` | Removes dead code. |
| `docs: overnight change walkthrough` | This file. |

To read one change at a time, use `git show <commit>`. To see everything after the
WIP snapshot, use `git diff <wip-commit>..HEAD`.

---

## Step 1: make the 11/03 work safe

### 1a. `.agents590/` was written into every working directory (`cli.py`, `main`)

**Problem:** `main` always called `RunStore.start(...)`. With no `--state-dir`,
that wrote `cwd/.agents590/runs/<id>/`. Harbor runs the CLI with `cwd` set to the
task repo (`/app`), so every trial left untracked files there. Verifiers that check
`git status --porcelain == ""` would fail for reasons that have nothing to do with
the agent, including your own `mmines_refuse_secret_leak`.

**Change:** one boolean, `wants_persistence = bool(args.state_dir or args.session or args.resume)`.
- A `RunStore` is created only when that's true.
- Every `run_store.finish(...)` is now `if run_store is not None: ...`.
- `context_run_id` reuses the same boolean. Before, that condition was written out
  a second time.

**Why this way:** a plain `agents590 <agent> -i "..."` is the contract Harbor and
your classmates' agents depend on, so it goes back to touching nothing on disk.
Persistence is now opt-in.

### 1b. `RunStore.start` crashed with a raw traceback

`RunStore.start` (or `run_store.event` on `--resume`) now sits inside
`try: ... except OSError`. If the state directory can't be written, you get one
clear line, `agents590: could not save run records in <dir>: <reason>`, and
exit code 1, before the agent starts. `OSError` is the error Python raises for
file-system problems like permissions, a full disk, or a file where a folder
should be.

### 1c. The broad `except Exception` hid tracebacks

There are now two handlers, which gets both options from the plan:
- `except (ValueError, TypeError, NotImplementedError)`: these are the *expected*
  failures: an unknown agent, bad settings, or the `krisj_starter` scaffold. The
  message stays one line, as before.
- `except Exception`: anything else is a bug or an outage. It calls
  `traceback.print_exc()` (the full stack trace to stderr), then prints the one-line
  message. A crashed Harbor trial is now debuggable from `agent/stdout.txt`.

Both handlers still record `"failed"` in the run store when one exists.

### 1d. `.gitignore`

`.agents590/` is now ignored, so `git add -A` can't commit your transcripts.
**Your existing `.agents590/` folder was left alone, as asked.** Read it, then
delete it yourself if you want.

### 1e. Flags that did nothing

| Flag | What happened | Why |
| --- | --- | --- |
| `--max-turns N` | **Wired.** `run_loop` uses `context.max_turns or MAX_LOOPS`. | `or` falls back to 30 when the flag wasn't given (`None`). |
| `--max-tool-calls N` | **Wired.** The loop counts tool calls and stops with `"Stopped: reached the limit of N tool calls."` | It was 4 lines, so it was easy to do honestly. |
| `--plan` | **Wired and enforced.** See below. | |
| `--auto-approve` | **Removed** from the CLI and `RunContext`. | There's no approval system for it to turn off, so the flag promised something that didn't exist. |
| `--max-seconds` | **Removed.** | Doing it properly means cancelling a running shell command and killing its process group when it's cancelled. That's more code than it's worth tonight. A half-version would leave orphaned processes. |
| `--vault` | **Removed.** | It was validated and recorded, then never used. |

No other agent read any of these fields (I searched `src/` to check), so removing
them can't break a classmate's agent.

**How `--plan` is enforced** (`policy.py`, `plan_mode_refusal`, and `agent.py`, `handle_tool_call`):
- `write_file` returns `[error] write_file is disabled in --plan mode`.
- `shell` only runs a small read-only allowlist: `pwd ls cat head tail grep wc diff`,
  plus `git status|log|diff|show`.
  - `;`, `&`, `>`, `<`, backticks, `$(` and newlines are refused, because they
    chain commands or redirect output into files.
  - Pipes are allowed, but every piece of the pipe must be on the list. So
    `grep -rn def . | wc -l` works, and `cat a | tee b` is refused.
  - `sed`, `find`, `sort` and `rg` are left out on purpose. Each one has a flag that
    writes files or runs other programs (`sed -i`, `find -delete`, `sort -o`, `rg --pre`).
- The system prompt gets a short "PLAN MODE" section, so the model knows the rules
  and returns a plan instead of fighting the refusals.

This is a guard rail against accidents. It is not a sandbox (see Step 7).

### 1f. Tests added

In `tests/test_cli.py`:
- a plain run leaves **no** `.agents590`
- `--session x --state-dir ...` writes the session file and a `completed` manifest
- `--session` alone writes `cwd/.agents590`
- an unwritable state dir gives the friendly error
- an unexpected exception prints `Traceback`

In `tests/test_policy.py` (new):
- `check_path("../x", tmp)` raises, and so do `a/../../x` and `/etc/passwd`
- a symlink that escapes the workspace is refused
- `.env`, `config/.ENV` and `.ssh/id_rsa` are refused

In `tests/test_masons_special_agent.py`:
- `write_file` is refused in plan mode
- 11 mutating commands are refused in plan mode
- 4 read-only commands are allowed

In `tests/test_run_controls.py` (new):
- `--max-turns 2` makes exactly 2 model calls
- `--max-tool-calls 3` stops on the 4th tool call
- the plan prompt appears only in plan mode

**Test helper move:** the fake model client used to live inside
`test_masons_special_agent_tracing.py`. It's now in `tests/fake_model.py`, so every
test file can share it. `use_fake_model(monkeypatch, replies)` patches
`MasonsSpecialAgent.provider_settings` on the *class*, so subagents talk to the
same fake. It also records every request's arguments in `fake.requests`, which lets
tests check the system prompt.

---

## Step 5: read `AGENTS.md` (`agent.py`, `project_conventions`)

If `context.cwd / "AGENTS.md"` exists, its text is appended to the system prompt
under `Project conventions (from AGENTS.md):`. The text is capped at
`MAX_AGENTS_MD_CHARS = 4_000` with the existing `truncate()`.
- A missing, empty or unreadable file adds nothing. The unreadable case is logged.
- **Why the system prompt rather than a tool:** the model sees it before its first
  tool call, the change is small, and it's easy to test.
- **Why a cap:** the system prompt is sent on *every* model call, so a 50 KB
  AGENTS.md would cost tokens 30 times over.
- **Risk to know about:** AGENTS.md comes from the repo, so a malicious repo can put
  instructions in it. That's prompt injection. This is the same trade-off every
  coding agent that reads AGENTS.md makes.

Tests are in `tests/test_agents_md.py`: the section is present, absent with no file,
and truncated when long.

---

## Step 6: skills and subagents

### Skills (`skills.py` and `skills/*.md`)

- Each skill is one Markdown file. The file name (without `.md`) is its name. The
  **first line** is its one-line description.
- `skills_prompt()` adds only `- name: description` lines to the system prompt.
- The new `load_skill(name)` tool returns the full file.
- **Why:** the guides cost no tokens until the model decides it needs one. That's
  the same idea as Claude Code skills.
- **Safety detail:** `load_skill` looks the name up in a dict of the real files. It
  never builds a path from the name, so `load_skill("../../.env")` can't read
  anything.
- Two skills ship. Both target real weak spots that the audit found:
  - `editing-large-files`: `read_file` cuts off at 10,000 characters. The skill says
    to measure with `wc -l`, locate with `grep -n`, page with `sed -n 'a,bp'`, and
    replace one exact snippet with a short Python script that asserts it appears
    exactly once.
  - `fixing-failing-tests`: shell commands time out at 60 s. The skill says to run
    one test first (`pytest -x`, `file::test`, `-k`), read the traceback bottom-up,
    fix the code rather than the test, then run the full suite.
- I checked that the `.md` files are included in a built wheel, so Harbor gets them.

Tests are in `tests/test_skills.py`: the prompt has descriptions but not bodies, the
full text loads, unknown or path-like names are errors, and the agent offers and
loads a real skill.

### Subagents (`agent.py`, `tool_delegate`; `tracing.py`, `add_subagent_usage`)

- **`delegate(task)`** starts `MasonsSpecialAgent(depth=self.depth + 1)` with the same
  `RunContext`, so it gets the same cwd, `--plan`, and limits. It starts a *new*
  conversation containing only `task`, and returns the subagent's final answer,
  truncated.
- **Depth guard:** `MAX_DELEGATION_DEPTH = 1`. The main agent (depth 0) is offered
  `delegate`. A subagent (depth 1) isn't offered it (see `tool_definitions()`), and
  `tool_delegate` refuses anyway in case the model sends the name regardless. This
  stops runaway recursion.
- **Token accounting:** the subagent's `RunTracer` is created with
  `write_trajectory=False`.
  - **Why that matters:** both tracers would otherwise write the *same*
    `trajectory.json`, and the subagent would overwrite the parent's.
  - In a `finally`, `tracer.add_subagent_usage(sub_tracer)` adds the subagent's input,
    output and cached tokens to the parent's totals, even if the subagent crashed.
    Harbor reads `final_metrics`, so it now counts both.
- **To make that possible, `run` was split in two.** `run()` builds the provider and
  the tracer, then calls `run_traced(provider, tracer, instruction, context)`, which
  does the real work. `tool_delegate` calls `run_traced` with its own non-writing
  tracer.
- `handle_tool_call` now takes the `RunContext` instead of just `cwd`, so tools can
  see `plan_only`. It also takes an optional `tracer`.

**Limitation:** the parent's per-step `metrics` don't include subagent tokens. Only
`final_metrics` does. The subagent's own steps aren't saved anywhere.

Tests are in `tests/test_subagents.py`:
- only depth 0 is offered `delegate`
- depth 1 refuses it
- a full parent → subagent → parent run: the subagent's input was only its task, its
  answer came back as the tool result, the parent trajectory wasn't overwritten, and
  `final_metrics` is 350/35 (100 + 50 + 200 input, 10 + 5 + 20 output)

---

## Step 7: isolation

### `make sandbox` (`Makefile`)

```
docker run --rm -it --env-file .env -v "$(CURDIR)":/work -v /dev/null:/work/.env:ro \
  -w /work -e UV_PROJECT_ENVIRONMENT=/opt/venv ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  bash -c 'uv sync --locked && exec bash'
```

Each part, and why it's there:
- `--rm`: the container is deleted when you exit.
- `-v "$(CURDIR)":/work`: **only this repo** is visible. Your home folder, SSH keys
  and other projects are not.
- `--env-file .env`: the key arrives as environment variables.
- `-v /dev/null:/work/.env:ro`: mounting the repo would also mount `.env` *as a
  file*, so this mounts an empty, read-only `/dev/null` over it. Inside, `cat .env`
  prints nothing.
  - I tested this trick with a dummy `.env` and the `alpine` image. The file
    was hidden, the variable was set, and the host file was unchanged.
- `UV_PROJECT_ENVIRONMENT=/opt/venv`: Linux packages go in the container. Without
  this, `uv sync` would overwrite your macOS `.venv`.
- Changes under `/work` are real changes to this repo, so branch or commit first.

**Not tested:** I didn't run the full target, because that would download the
`ghcr.io/astral-sh/uv` image and PyPI packages. Run `make sandbox` once yourself.

### Shell policy: the honest version

- I **didn't** add a ".env is forbidden in shell commands" rule. Here's why:
  - It's trivially bypassed: `cat .e*`, `python3 -c "print(open('.en'+'v').read())"`,
    or `make` running a recipe.
  - It would also break legitimate tasks like `echo .env >> .gitignore`, which the
    `lucykgood_ignore_env` eval needs.
  - No first-word allowlist can be safe while `python3`, `make`, `uv`, `pytest` and
    `git` are allowed, because each one can run arbitrary code. **The container is
    the real boundary.** The README now says so in a new "Local isolation" section.
- I **did** add one cheap protection that does hold: shell commands no longer
  inherit `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` (`SECRET_ENV_VARS` and
  `shell_environment()`).
  - The agent process needs the key to call the model, but `pytest` or
    `python3 -c` never do.
  - So even in Harbor or the sandbox, where the key is an environment variable and
    not a file, a command can't print it.
  - The test is `test_shell_commands_cannot_see_the_api_key`.

---

## What I did not do

- **Steps 2–4 of the audit** (adapter crash logs, cost, a failing eval) are in
  `agent_evals`, which was out of scope tonight.
- **`--max-seconds`**: removed rather than half-built. See 1e.
- **No Harbor re-run.** That needs paid model calls.
- **The 11/03 WIP code itself** (`persistence.py`, `policy.py`, the session code in
  `cli.py`) was fixed where the plan said, but not otherwise rewritten. It's still
  mostly agent-written code that you need to be able to explain.

## What to review before submitting

1. The 590H policy on AI-written code. I found no repo-level rule, but I couldn't
   read the syllabus.
2. `git show` on the WIP commit. It's the largest block of code you didn't write by
   hand.
3. The `.agents590/` folder (left in place, now ignored). Delete it when you're done
   reading.
4. `policy.py` still refuses `write_file` to `.env`. A task that asks the agent to
   create `.env` will fail through `write_file`. Re-run `lucykgood_ignore_env` and
   `mmines_refuse_secret_leak` in Harbor (paid) before relying on this branch.
5. Run `make sandbox` once to confirm the image works on your machine.
6. Whether you want `cli.py` changes (shared by every classmate's agent) upstreamed
   at all. They now default to the old behavior, but it's still a shared file.
