# Loss Draft processing SOP — example

> Sample SOP file. Replace with your real procedure once available.

## Approval thresholds

- Claims under $5,000: approve silently — no HITL required if confidence ≥ 0.90.
- Claims $5,000 to $50,000: route to a senior reviewer via the HITL dashboard.
- Claims over $50,000: ALWAYS route to HITL. Never auto-approve regardless of confidence.

## Document management page

- The document grid loads asynchronously. Wait for at least one document row
  to appear before attempting any read or extract action.
- If the grid shows "No documents found", do not retry — flag for human review.

## Common failure modes

- Session timeout banner ("Your session has expired"): re-authenticate via RD
  Web before continuing.
- The "Submit" button is disabled until every required field is non-empty;
  do not click it speculatively.
