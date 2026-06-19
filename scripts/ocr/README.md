# OCR reader

This package is a standalone replacement candidate for document reading. Existing
pipelines are intentionally left untouched.

The reader tries methods in this order by default:

1. Embedded text extraction: `pdftotext`, then `pypdf` for PDFs; simple text
   extraction for HTML/text files.
2. Codex image fallback. PDFs are rendered to a temporary image first.

OCR API remains available, but it is not part of the default path. To include it,
override the method order:

```bash
--method text --method ocr_api --method codex_image
```

Each attempt is parsed according to `doc_type`, then validated with required
fields for that document type. Fallback is driven by validation failure, not by
OCR engine success alone.

For pipeline integration, call `read_document(..., validator=your_validator)`.
The validator receives `(doc_type, data)` and returns `ValidationResult`, so each
workflow can decide fallback using its own rules. The CLI exposes the simpler
`--required-field` path for manual tests.

Results are a single final JSON object. No `.ocr/` directory is created. Use
`--write-sidecar` to write `<source-stem>.json` next to the document, or
`--output` for explicit test output.

Example:

```bash
python -m scripts.ocr.cli "meeting/receipt/CamScanner 05-28-2026 20.03.jpg" \
  --doc-type receipt \
  --output /tmp/receipt.read.json \
  --overwrite
```
