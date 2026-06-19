from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .reader import OcrConfig, read_document
from .validation_profiles import validate_document


DOC_PATTERNS: dict[str, tuple[str, ...]] = {
    "receipt": (
        "meeting/receipt/*.jpg",
        "meeting/receipt/*.jpeg",
        "meeting/receipt/*.png",
        "meeting/receipt/used/*.jpg",
        "meeting/receipt/used/*.png",
        "purchase/*/*영수증*.pdf",
    ),
    "estimate": (
        "purchase/*/견적*.pdf",
        "purchase/*/*견적서*.pdf",
        "purchase/*/*양대호*.pdf",
    ),
    "statement": (
        "purchase/*/거명*.pdf",
        "purchase/*/*거래명세*.pdf",
        "purchase/*/전세_거명*.pdf",
        "purchase/*/거명_전세*.pdf",
    ),
    "tax_invoice": (
        "purchase/*/전세*.pdf",
        "purchase/*/*세금계산서*.pdf",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OCR reader on sampled document types.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="scripts/ocr/tmp/batch_eval")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--doc-type", action="append", choices=sorted(DOC_PATTERNS))
    parser.add_argument("--method", action="append", choices=["text", "ocr_api", "codex_image"])
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--no-raw-text", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    load_env_file(root / args.env_file)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = OcrConfig(
        ocr_api_key=os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY", ""),
        ocr_api_url=os.environ.get("DHLAB_OCR_API_URL", OcrConfig.ocr_api_url),
        timeout=args.timeout,
        methods=tuple(args.method) if args.method else OcrConfig.methods,
        include_raw_text=not args.no_raw_text,
    )

    doc_types = tuple(args.doc_type) if args.doc_type else tuple(DOC_PATTERNS)
    summary: dict[str, object] = {"doc_types": {}, "results": []}

    for doc_type in doc_types:
        samples = collect_samples(root, doc_type, args.limit)
        type_dir = output_dir / doc_type
        type_dir.mkdir(parents=True, exist_ok=True)
        type_summary = {"sample_count": len(samples), "validated": 0, "review_required": 0, "methods": {}, "samples": []}
        print(f"## {doc_type}: {len(samples)} samples")
        for index, sample in enumerate(samples, 1):
            expected_vendor = expected_vendor_from_path(sample)

            def validator(_doc_type: str, data: dict[str, object], expected_vendor: str | None = expected_vendor):
                return validate_document(_doc_type, data, expected_vendor=expected_vendor)

            try:
                result = read_document(sample, doc_type=doc_type, validator=validator, config=config)
            except Exception as exc:
                result = None
                status = "error"
                method = None
                result_data = {"error": str(exc)}
                attempts = []
            else:
                status = result.status
                method = result.method
                result_data = result.to_dict()
                attempts = result_data["attempts"]

            safe_name = f"{index:02d}_{safe_stem(sample)}.json"
            out_path = type_dir / safe_name
            out_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")

            if status == "validated":
                type_summary["validated"] += 1
            else:
                type_summary["review_required"] += 1
            if method:
                methods = type_summary["methods"]
                methods[method] = methods.get(method, 0) + 1
            row = {
                "path": str(sample),
                "status": status,
                "method": method,
                "output": str(out_path),
                "attempts": summarize_attempts(attempts),
            }
            type_summary["samples"].append(row)
            summary["results"].append({"doc_type": doc_type, **row})
            print(f"{index:02d} {status:15} {str(method):12} {sample}")
        summary["doc_types"][doc_type] = type_summary

    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {output_dir / 'summary.json'}")
    return 0


def collect_samples(root: Path, doc_type: str, limit: int) -> list[Path]:
    seen: set[Path] = set()
    samples: list[Path] = []
    for pattern in DOC_PATTERNS[doc_type]:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            if path in seen:
                continue
            if doc_type == "tax_invoice" and ("거명" in path.name or "거래명세" in path.name):
                continue
            if doc_type == "estimate" and "물품검수확인서" in path.name:
                continue
            seen.add(path)
            samples.append(path)
            if len(samples) >= limit:
                return samples
    return samples


def expected_vendor_from_path(path: Path) -> str | None:
    if path.parent.name == "vendors":
        return None
    name = path.parent.name
    if "_" in name:
        return name.split("_", 1)[1]
    if path.parent.parent.name == "vendors":
        return path.parent.name
    return None


def summarize_attempts(attempts: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for attempt in attempts:
        rows.append(
            {
                "method": attempt.get("method"),
                "ok": attempt.get("ok"),
                "validated": attempt.get("validated"),
                "reason": attempt.get("reason") or attempt.get("error") or "",
                "elapsed_sec": attempt.get("elapsed_sec"),
            }
        )
    return rows


def safe_stem(path: Path) -> str:
    raw = "_".join(path.with_suffix("").parts[-3:])
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)[:120]


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
