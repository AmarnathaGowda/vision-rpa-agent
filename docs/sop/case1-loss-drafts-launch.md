# Stage 2 — Loss Drafts launch + SAML SSO

## Purpose
After RD Web login lands on the Production folder, launch the Loss Drafts
module. This is the stage that transitions from the RD Web portal to the
Loss Drafts SPA. The legacy flow downloads an HTML launcher whose
meta-refresh tag carries the real URL into the Loss Drafts module; the
sim's SSO step then redirects back to `/lossdrafts/`.

## Preconditions
- `current_stage == "loss_drafts_launch"`.
- Browser URL contains `/Default.aspx/Production`.
- A tile labelled "Loss Drafts" is visible.
- `working_memory.rdweb_username` / `rdweb_password` already submitted.

## Steps (intent — not selectors)
1. The Loss Drafts tile triggers an HTML-launcher download (the same
   pattern RDWeb uses in production). Use action_type
   `click_download_open` with target=`Loss Drafts` (or the locator key
   `prod_lossdrafts`). The framework will:
     - capture the downloaded `.html` launcher file
     - parse its `<meta http-equiv="refresh">` tag
     - navigate the current tab to that URL automatically
   Do NOT use plain `click` for the Loss Drafts tile — plain click won't
   capture the download and the agent will be left on the same page
   while a file dribbles into the downloads dir unobserved.
2. The launcher's meta-refresh URL points at `/lossdrafts/` which the
   server protects with an SSO redirect to `/sso/idp/SSO.saml2`. The
   PingIdentity-style sign-on page appears with these elements:
     - Header text "Sign On"  (this is a label / heading — NEVER type
       into it; it's not an input)
     - USERNAME input  (placeholder uppercase "USERNAME")
     - PASSWORD input  (placeholder uppercase "PASSWORD")
     - "Sign On" button  (click target after both fields are filled)

   **IMPORTANT — the SAML/SSO page uses DIFFERENT credentials than the
   RD Web login.** Use these placeholders:
     - `{{SSO_USERNAME}}` for the USERNAME input
     - `{{SSO_PASSWORD}}` for the PASSWORD input

   Concretely:
     a. `type` action with target=`USERNAME` (or `Username`) and
        value=`{{SSO_USERNAME}}`.
     b. `type` action with target=`PASSWORD` (or `Password`) and
        value=`{{SSO_PASSWORD}}`.
     c. `click` action with target=`Sign On` (the button — note the
        space and capitalisation differ from RD Web's "Sign in").
3. After the SSO form submits, the cookie is set and the browser
   redirects to `/lossdrafts/`. Wait for the Loss Drafts shell to render
   — look for the "Loss Drafts" header text or the document grid.

## Common mistakes to avoid

- Reusing `{{RDWEB_USERNAME}}` / `{{RDWEB_PASSWORD}}` on the SSO page —
  they are different identities and the SAML mock will reject them.
- Emitting `type` with target=`Sign On` — that's the SUBMIT BUTTON,
  not an input. Use `click` for the button AFTER both fields are filled.
- Skipping the password and going straight to "Sign On" — the form
  validation will block the click. Both fields must be filled first.

## Done when
- URL contains `/lossdrafts/` (no further path required).
- `working.extracted_values["lossdrafts_loaded"] = True` — set this with
  an `extract` action with target=`set_flag` and value=`lossdrafts_loaded`
  once the LD shell is visible.
- Emit `stage_complete` with target=`document_management`.

## Recovery rules
- **Tile not found**: HITL — page may have logged us out. Operator can
  re-authenticate then submit guidance to retry stage 1.
- **SAML loop / stuck on /sso/ page**: HITL — credentials may be wrong or
  IdP expired in the sim's session. Operator should restart the sim.
- **Loss Drafts page returns 500**: abort — server error, not recoverable
  by the agent.
