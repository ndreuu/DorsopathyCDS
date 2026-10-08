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
