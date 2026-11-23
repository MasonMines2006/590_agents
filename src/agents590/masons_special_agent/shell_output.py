"""Read a command's output without keeping all of it.

Ported from Yewon Song's `yewons_stale_path` agent (tools.py, `_read_output`).

Why: a test run can print megabytes. We only send the model a few thousand
bytes, and the most useful part is often at the END (a traceback, a failing
assert, a summary line). So we keep the first half of the budget and a rolling
window of the last half, and say how much was dropped in between.
"""

import asyncio

# How many bytes to pull from the pipe per read.
CHUNK_BYTES = 4096


async def read_bounded(stream: asyncio.StreamReader, max_bytes: int) -> str:
    """Read *stream* to the end, keeping at most *max_bytes* (head + tail).

    The pipe is always drained completely, so the child process never blocks
    on a full pipe, but memory use stays around *max_bytes*.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1.")
    head_limit = (max_bytes + 1) // 2  # first half (rounded up)
    tail_limit = max_bytes // 2  # last half
    head = bytearray()
    tail = bytearray()
    total = 0

    while chunk := await stream.read(CHUNK_BYTES):
        total += len(chunk)
        # Fill the head first; whatever does not fit goes to the tail.
        room_in_head = max(head_limit - len(head), 0)
        head.extend(chunk[:room_in_head])
        if tail_limit:
            tail.extend(chunk[room_in_head:])
            # Keep only the newest `tail_limit` bytes.
            del tail[:-tail_limit]

    if total <= max_bytes:
        return (head + tail).decode("utf-8", errors="replace")
    omitted = total - len(head) - len(tail)
    return (
        head.decode("utf-8", errors="replace")
        + f"\n[output truncated: {omitted} bytes omitted]\n"
        + tail.decode("utf-8", errors="replace")
    )
