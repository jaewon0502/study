#!/usr/bin/env python3
"""
Minimal template for algorithm problems.
Usage: python template.py < input.txt
Fill in parse_input and solve to match the problem.
"""

from __future__ import annotations

import sys
from typing import List, Tuple


def parse_input(raw: str) -> Tuple[int, List[int]]:
    """Adjust this parser for your problem."""
    data = list(map(int, raw.strip().split()))
    if not data:
        return 0, []
    n, *nums = data
    return n, nums


def solve(n: int, nums: List[int]) -> str:
    """Implement the algorithm logic."""
    # Example: sum first n numbers provided.
    return str(sum(nums[:n]))


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        print("Provide input via STDIN. Example: `echo '3 1 2 3' | python template.py`", file=sys.stderr)
        return

    n, nums = parse_input(raw)
    answer = solve(n, nums)
    print(answer)


if __name__ == "__main__":
    main()
