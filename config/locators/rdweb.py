"""POC locator carry-over for the RD Web Access portal.

Status: starter set. The full 120+ data-testid map from the
`insurance-agent-project` POC lands when that repo is available. See
[CLAUDE.md](../../CLAUDE.md): "Copy `config/locators/rdweb.py` selectors
directly — they are proven. DO NOT rewrite."

Each value MUST be a Playwright-compatible selector. Friendly name → selector.
"""
from __future__ import annotations

LOGIN: dict[str, str] = {
    "username":  "[data-testid='user-input']",
    "password":  "[data-testid='password-input']",
    "sign_in":   "[data-testid='submit-btn']",
    "page_title": "[data-testid='page-title']",
}

CLAIM_SEARCH: dict[str, str] = {
    "claim_id":      "[data-testid='claim-input']",
    "search":        "[data-testid='search-btn']",
    "result_row":    "[data-testid='result-row']",
    "result_claim":  "[data-testid='result-claim']",
    "result_status": "[data-testid='result-status']",
    "result_amount": "[data-testid='result-amount']",
}

FORM: dict[str, str] = {
    "loan_number":  "[data-testid='loan-input']",
    "status":       "[data-testid='status-select']",
    "amount":       "[data-testid='amount-input']",
    "submit":       "[data-testid='submit-btn']",
    "success_toast": "[data-testid='success-toast']",
}

# Convenience aggregate — useful for the SelectorResolver locator_map.
ALL: dict[str, str] = {**LOGIN, **CLAIM_SEARCH, **FORM}
