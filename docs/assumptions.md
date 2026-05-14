# Assumptions, Workarounds, and Pending Validations

Single rolling tracker for anything carried forward across phases. Reviewed at
the end of each phase and again before MVP sign-off.

Format: each row has a stable ID, the phase that introduced it, the
category, a short description, the impact if left unresolved, and the
verification step. Mark `RESOLVED — <date>` inline when retired.

| ID | Phase | Category | Description | Impact | Verify by |
|----|-------|----------|-------------|--------|-----------|
| A-01 | 0 | Environment | Windows-install verification of Playwright + pywinauto + mss skipped — dev box is macOS. | Phase 3+ cannot be runtime-validated on dev machine. | Run `poetry install && pytest` on the Windows VM. |
| A-02 | 1 | Architecture | Roadmap originally said "Claude Vision"; CLAUDE.md non-negotiable forbids external LLMs. Implementation uses local Ollama/vLLM via OpenAI-compatible client. | None — aligns with non-negotiables. | n/a (decision, not a workaround). |
| A-03 | 2 | Environment | External LD / IIM "simulation site (localhost)" servers don't exist in this repo. Vendored equivalent HTML under `tests/sim/pages/`. | Real-app integration still pending. | Point `LD_BASE_URL` / `IIM_BASE_URL` at the actual sim once provided; rerun `pytest tests/test_browser_integration.py`. |
| A-04 | 2 | Coverage | `config/locators/rdweb.py` is a starter set covering only the vendored sim. Full 120+ POC locator map lives in `insurance-agent-project` which isn't accessible from this repo. | LLM will frequently miss real-app selectors and route to HITL. | Copy verbatim from POC when accessible (CLAUDE.md: "DO NOT rewrite"). |
| A-05 | 2 | Coverage | `BrowserExecutor` exposes no dedicated `select_option` primitive — dropdowns are handled by `click` + `text=` resolution. | Some IIM dropdown patterns may fail until added. | Add `select_option(target, label)` when first failing case is seen. |
| A-06 | 3 | Platform | `pywinauto`, `mstsc.exe`, RDP keep-alive, and File Explorer automation require Windows. macOS dev box runs *only* mock-based unit tests for these modules. | Cannot validate Phase 3 end-to-end on Mac. Desktop/RDP regressions invisible until tested on Windows. | Run `tests/test_desktop.py`, `tests/test_rdp.py`, `tests/test_file_ops.py` plus the live RDP YAML on the App VM (Windows). |
| A-07 | 3 | Infra | RDP target host, credentials, and `.rdp` file template are placeholders in `.env.example`. Real values must come from Vault before live testing. | Live RDP launch will fail with auth error. | Provision a Vault-backed `.env` for the Windows agent host. |
| A-08 | 3 | Behaviour | Keep-alive thread sends a synthetic mouse jiggle every `RDP_KEEPALIVE_SECONDS` (default 240). If the RDP host has a strict "no input idle" lockout shorter than that, sessions will still time out. | Long-running tasks may drop the session and trigger recovery. | Configure `RDP_KEEPALIVE_SECONDS` to be < the host's idle policy. |
| A-09 | 3 | Behaviour | Desktop perception (RDP region capture) uses an `mss` sub-region grab — assumes the RDP window is unminimised and on the primary monitor. Multi-monitor setups need explicit monitor index. | Wrong screenshot region → misperception → HITL. | Once a target Windows host is available, add `RDP_MONITOR_INDEX` setting. |
| A-10 | 3 | Recovery | Reconnect logic re-launches `mstsc` with the same `.rdp` file; it does not yet attempt credential rotation or warn on session lock. | Repeated reconnect loops possible if creds were rotated. | Add a "max reconnects per task" counter and surface to HITL. |
| A-11 | 3 | Testing | All Phase 3 unit tests use `unittest.mock` to fake pywinauto + subprocess; no real Windows API call is exercised by CI on macOS. | Test green ≠ runtime green on Windows. | Mirror the unit test suite on a Windows runner. |
