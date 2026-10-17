# Application Court Agent — Long-Term Architectural Memory (c3)

- Fixed-width bureau response payloads require strict byte-offset alignment; an upstream +120-byte header expansion shifts `cibil_score` extraction into filler bytes and triggers `cibil_stub_900`.
- Always run `python3 scripts/check_cibil_score_offsets.py` when `cibil_score` clusters at 900 across `loan_applications`.
- Unpinned model endpoints or disabled Model Armor templates cause prompt drift detectable via `python3 scripts/check_prompt_drift_cusum.py`.
