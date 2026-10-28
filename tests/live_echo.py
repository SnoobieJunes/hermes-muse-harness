"""One live echo turn. Not part of the unit suite. Requires `muse` on PATH."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import run_turn


def main() -> None:
    result = run_turn(
        prompt="ping",
        model="echo",
        workspace=os.getcwd(),
        approval_mode="denyUnmatched",
        provider="echo",
        timeout_s=20,
    )
    print(result)
    if "ping" not in result["text"]:
        raise SystemExit(f"unexpected echo text: {result['text']!r}")


if __name__ == "__main__":
    main()
