"""Run with: python -m swai_demo --request-file examples/plate_ja.txt"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo import evaluate_request


def main() -> int:
    parser = argparse.ArgumentParser(description="SolidWorksAI request validation demo")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", help="Request text with explicit dimensions")
    source.add_argument("--request-file", type=Path, help="UTF-8 request text file")
    args = parser.parse_args()
    try:
        request = (
            args.request_file.read_text(encoding="utf-8")
            if args.request_file is not None else args.request
        )
    except (OSError, UnicodeError) as error:
        print(json.dumps({"status": "input_error", "message": str(error)}, ensure_ascii=False))
        return 1
    result = evaluate_request(request)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "validated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
