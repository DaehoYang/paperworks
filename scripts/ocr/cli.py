from __future__ import annotations

import argparse
import os
from pathlib import Path

from .reader import OcrConfig, read_document, sidecar_path_for, write_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a document with text extraction and Codex image fallback.")
    parser.add_argument("path")
    parser.add_argument("--doc-type", default="generic")
    parser.add_argument("--method", action="append", choices=["text", "ocr_api", "codex_image"], help="Method order. Repeat to override default order. OCR API is optional, not default.")
    parser.add_argument("--required-field", action="append", default=[])
    parser.add_argument("--output", help="Write final result JSON to this path.")
    parser.add_argument("--write-sidecar", action="store_true", help="Write final JSON next to the source as <stem>.json.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-raw-text", action="store_true", help="Do not embed final raw text in result JSON.")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--ocr-api-url", default=os.environ.get("DHLAB_OCR_API_URL", OcrConfig.ocr_api_url))
    parser.add_argument("--ocr-api-key", default=os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY", ""))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-model")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    api_key = args.ocr_api_key or os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY", "")
    config = OcrConfig(
        ocr_api_url=args.ocr_api_url,
        ocr_api_key=api_key,
        codex_bin=args.codex_bin,
        codex_model=args.codex_model,
        timeout=args.timeout,
        methods=tuple(args.method) if args.method else OcrConfig.methods,
        include_raw_text=not args.no_raw_text,
    )
    required = tuple(args.required_field) if args.required_field else None
    result = read_document(args.path, doc_type=args.doc_type, required_fields=required, config=config)

    if args.output:
        write_result(result, Path(args.output), overwrite=args.overwrite)
    if args.write_sidecar:
        write_result(result, sidecar_path_for(Path(args.path)), overwrite=args.overwrite)
    if not args.output and not args.write_sidecar:
        import json

        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "validated" else 2


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


if __name__ == "__main__":
    raise SystemExit(main())
