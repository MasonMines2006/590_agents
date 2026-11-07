"""Friendly command line entry point for all registered agents."""

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from dotenv import load_dotenv

from agents590.contracts import RunContext
from agents590.persistence import RunStore
from agents590.registry import agent_names, create_agent


class Arguments(argparse.Namespace):
    agent: str | None
    instruction: str | None
    cwd: Path
    model: str | None
    env_file: Path | None
    list_agents: bool
    plan: bool
    auto_approve: bool
    session: str | None
    resume: str | None
    state_dir: Path | None
    vault: Path | None
    jsonl: bool
    quiet: bool
    verbose: bool
    trace: bool
    log_file: Path | None
    max_turns: int | None
    max_tool_calls: int | None
    max_seconds: int | None


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _session_path(state_dir: Path, name: str) -> Path:
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    if not safe_name:
        raise ValueError("session name must contain a letter or number")
    return state_dir / f"session-{safe_name}.json"


def _load_session(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        value: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read session {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"session {path.name} is not a valid agents590 session")
    payload = cast(dict[str, object], value)
    raw = payload.get("messages", [])
    if not isinstance(raw, list):
        raise ValueError(f"session {path.name} is not a valid agents590 session")
    history: list[dict[str, str]] = []
    items = cast(list[object], raw)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"session {path.name} is not a valid agents590 session")
        fields = cast(dict[object, object], item)
        history.append({str(key): str(content) for key, content in fields.items()})
    return history


def _session_instruction(instruction: str, history: list[dict[str, str]]) -> str:
    if not history:
        return instruction
    previous = "\n\n".join(
        f"{item.get('role', 'assistant').upper()}: {item.get('content', '')}" for item in history
    )
    return (
        "The following is the conversation so far. Keep its context in mind, but treat the new "
        "message as the current request.\n\n"
        f"{previous}\n\nUSER: {instruction}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents590", description="Run an agents590 agent.")
    parser.add_argument("agent", nargs="?", help="Registered agent name; see --list")
    parser.add_argument("-i", "--instruction", help="Instruction to execute")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Agent working directory")
    parser.add_argument("-m", "--model", help="Per-run model/deployment override")
    parser.add_argument("--env-file", type=Path, help="Explicit environment file to load")
    parser.add_argument("--list", dest="list_agents", action="store_true", help="List agents")
    parser.add_argument("--plan", action="store_true", help="Plan only; do not mutate files")
    parser.add_argument("--auto-approve", action="store_true", help="Explicitly allow actions")
    parser.add_argument("--session", help="Session name; conversation context is saved locally")
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume the instruction saved for a run")
    parser.add_argument("--state-dir", type=Path, help="Directory for persistent run records")
    parser.add_argument(
        "--vault", type=Path, help="Optional existing vault path recorded with the run"
    )
    parser.add_argument("--jsonl", action="store_true", help="Print one JSON result line")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress the final response")
    parser.add_argument("-v", "--verbose", action="store_true", help="Include run metadata")
    parser.add_argument("--trace", action="store_true", help="Print timing details to stderr")
    parser.add_argument("--log-file", type=Path, help="Append timing details to a log file")
    parser.add_argument("--max-turns", type=_positive, help="Maximum model turns")
    parser.add_argument("--max-tool-calls", type=_positive, help="Maximum tool calls")
    parser.add_argument("--max-seconds", type=_positive, help="Maximum run duration in seconds")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv, namespace=Arguments())
    if args.list_agents:
        print("\n".join(agent_names()))
        return 0
    if not args.agent:
        parser.error("an agent name is required")
    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        parser.error("--cwd must name an existing directory")
    if args.env_file is not None:
        env_file = args.env_file.expanduser().resolve()
        if not env_file.is_file():
            parser.error("--env-file must name an existing file")
        load_dotenv(env_file, override=False)
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else cwd / ".agents590"
    vault = args.vault.expanduser().resolve() if args.vault else None
    if vault is not None and not vault.is_dir():
        parser.error("--vault must name an existing directory")

    run_store: RunStore | None = None
    instruction = args.instruction
    if args.resume:
        try:
            run_store = RunStore.open(state_dir, args.resume)
            instruction = run_store.instruction()
        except ValueError as error:
            parser.error(str(error))
    if not instruction or not instruction.strip():
        parser.error("-i/--instruction must contain an instruction (or use --resume)")

    history: list[dict[str, str]] = []
    session_path: Path | None = None
    if args.session:
        try:
            session_path = _session_path(state_dir, args.session)
            history = _load_session(session_path)
        except ValueError as error:
            parser.error(str(error))
    model_instruction = _session_instruction(instruction, history)
    run_id = args.resume or uuid.uuid4().hex[:12]
    if run_store is None:
        run_store = RunStore.start(
            state_dir,
            run_id,
            {
                "agent": args.agent,
                "instruction": instruction,
                "cwd": str(cwd),
                "session": args.session,
                "model": args.model,
                "vault": str(vault) if vault else None,
            },
        )
    else:
        run_store.event("run_resumed", {"instruction": instruction})

    # Keep the original context contract for callers that did not request a
    # persistent session; persistence itself is still enabled by default.
    context_run_id = run_id if (args.state_dir or args.session or args.resume) else None
    context_state_dir = args.state_dir.expanduser().resolve() if args.state_dir else None
    context = RunContext(
        cwd=cwd,
        model=args.model,
        plan_only=args.plan,
        auto_approve=args.auto_approve,
        session=args.session,
        run_id=context_run_id,
        max_turns=args.max_turns,
        max_tool_calls=args.max_tool_calls,
        max_seconds=args.max_seconds,
        state_dir=context_state_dir,
        vault=vault,
    )
    started = time.monotonic()
    try:
        output = asyncio.run(create_agent(args.agent).run(model_instruction, context))
    except KeyboardInterrupt:
        run_store.finish("interrupted", error="interrupted by user")
        return 130
    except Exception as error:
        run_store.finish("failed", error=str(error))
        print(f"agents590: {error}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started
    run_store.finish("completed", output=output)
    record = {
        "run_id": run_id,
        "agent": args.agent,
        "instruction": instruction,
        "cwd": str(cwd),
        "session": args.session,
        "model": args.model,
        "output": output,
    }
    if args.session and session_path is not None:
        history.extend(
            [{"role": "user", "content": instruction}, {"role": "assistant", "content": output}]
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps({"session": args.session, "messages": history}, indent=2) + "\n"
        )
    details = f"run {run_id} | {args.agent} | {cwd}"
    if args.trace or args.log_file:
        trace = f"{details} | {elapsed:.2f}s"
        if args.trace:
            print(trace, file=sys.stderr)
        if args.log_file:
            log_path = args.log_file.expanduser().resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as log:
                log.write(trace + "\n")
    if args.jsonl:
        print(json.dumps(record))
    elif not args.quiet:
        if args.session:
            print(f"\n── {args.session} · {args.agent} ──\n")
        elif args.verbose:
            print(details)
        print(output)
    return 0


def chat_main(argv: Sequence[str] | None = None) -> int:
    """Short convenience entry point for the primary coding agent."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    return main(["masons_special_agent", *arguments])
