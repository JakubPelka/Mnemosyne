#!/usr/bin/env python3
"""Print content-safe structural information about an OpenAI/ChatGPT export."""

import argparse
import json
from pathlib import Path

from backend.app.importers.chatgpt import ChatGPTExportAdapter, ChatGPTExportError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", type=Path, help="ZIP archive or unpacked export directory")
    args = parser.parse_args()

    adapter = ChatGPTExportAdapter()
    try:
        report = adapter.inspect(args.input_path)
        adapter.validate(args.input_path)
    except ChatGPTExportError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 1

    print(
        json.dumps(
            {
                "valid": True,
                "detected_format": report.detected_format,
                "input_kind": report.input_kind,
                "file_count": report.file_count,
                "conversation_files": list(report.candidate_files),
                "warnings": list(report.warnings),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
