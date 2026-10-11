# Reliable ScanSplit delivery

User authorized implementation and a runnable Windows offline delivery on 2026-10-11.
Baseline: local 9cf5fee (remote main was 61f984f). Work in managed worktree on codex/reliable-scansplit; preserve original checkout.

## Contract
- Mixed scanned PDFs. Names use title + reporting unit (填报单位), never substitute issuer/body/seal unit.
- Keep internal and uncertain blank pages. Separate retained split boundaries from excluded separator pages.
- Preserve originals, encoded image/content streams; rotation metadata only. No cloud calls.
- Preserve manual decisions through reruns, undo/redo. Boundary uncertainty prevents reliable names/export.
- Hard integrity checks inside exporter, regardless of UI/non-strict/partial selections.
- Review uncertain results, then export. Deliver executable, acceptance evidence, limitations.

## Milestones
1. Baseline + reproduce gaps, repair hard export/manual/orientation contracts.
2. Shared automatic pipeline: blank detection -> direction proposals -> unified boundaries -> review.
3. Reporting-unit extraction with labels/geometry and source-page evidence; separate human/automatic state.
4. Consolidated review, boundary/name dependency, affected-result invalidation.
5. Full tests, representative mixed OCR acceptance, stress/cancellation, package/selftest, deploy versioned directory.

## Evidence / progress
- Baseline: 214 passed. Added 30 regression cases; final full suite: 244 passed in 119.81s.
- Hard exporter checks now run before every export, including old strict=False and partial callers.
- Reporting unit extraction uses explicit labels/geometry, source page and box; no issuer/seal substitution.
- Unified pipeline checks direction before boundaries; names require confirmed boundaries. Manual zero rotation is protected.
- Uncertain blanks are retained; exclusion requires confirmation in the normal workflow. Manual retained/excluded pages and split/merge decisions survive re-analysis.
- Original /Rotate is preserved; PDFium rotation is applied once. OCR, preview and export use unified direction.
- Background import/analysis, cancellation rollback, PDFium serialization and inactive-renderer cache clearing implemented.
- Mixed 11-page acceptance: missing/multiple reporting units exposed, internal blank preserved, five verified outputs after explicit simulated human decisions, source hash unchanged.
- PyInstaller build succeeded; deployed to D:/ScanSplitPDF-Reliable-1.10.0. Packaged selftest exit 0 with actual Chinese OCR, label extraction, rendering, export and manifest.

## Remaining
Implementation and verification complete. Real business scan accuracy remains unmeasured; do not infer universal automatic correctness from synthetic samples. Source changes stay on codex/reliable-scansplit; GitHub main is unchanged.
