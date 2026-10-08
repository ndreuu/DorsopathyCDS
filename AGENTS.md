# Working in DorsopathyCDS

- This is a standalone local research repository. Do not edit the MRI backend or SpinoSarc as part of changes here.
- Application code lives in `src/`; research source files in `research/`; explanatory documentation in `docs/`; synthetic cases in `examples/`.
- Every clinical condition must reference a verified verbatim quotation in the source catalog. Paraphrases belong in labels and mapping notes.
- Keep unknown, false and not-applicable states distinct. A missing uploaded study must never become a negative clinical finding.
- New clinical rules require mapping notes, source/version/page references, and meaningful tests for missing data and conflicting evidence.
- No unvalidated disease exclusion, numerical diagnostic probability, psychogenic-pain or malingering inference from normal MRI/sensors.
- Use synthetic cases in the repository. Keep reports, actual patient data, local PDFs and environments out of Git.
- Run `PYTHONPATH=src python3 -m unittest discover -s tests -v` after behavior changes. Tests verify software behavior, not clinical validity.
- Do not describe this software as approved, registered or clinically validated. Source approval and software approval are separate.
- Shared module wire contracts are versioned in `src/dorsopathy_platform/contracts.py` and `schemas/v1/`. Breaking changes need a new major version and coordinated consumer changes. JSON Schema alone does not perform semantic validation.
- Provider implementations live under `src/dorsopathy_modules/`. Providers may import platform contracts, ports and result helpers; do not import orchestration, SQLite or the clinical engine into a provider. Heavy MRI dependencies stay in separate environments/processes.
- Agree module IDs, concepts, units, ownership and fixtures in `docs/module_development_ru.md` before implementing a provider. Do not edit another owner's provider as an integration shortcut.
- Every assessed observation needs evidence. Preserve acquisition time, quality, numbering status and origin; synthetic providers must only emit `synthetic_demo`.
- Large imaging artifacts use streamed `add_file`/`open`, not whole-file reads. Never commit artifact stores, patient data or weights.
