# RD Web login + RemoteApps workspace (simulation)

## Purpose

Authenticate to the RD Web Access portal and land on the production
RemoteApps workspace, from which Loss Drafts / Proctor / Explore can be
launched. Used as the first leg of cases 2, 3, and 4.

## Preconditions

- The browser is open and not yet navigated.
- Working memory contains `rdweb_username` and `rdweb_password` (read from
  `RDWEB_USERNAME` / `RDWEB_PASSWORD` in `.env`).

## Steps (intent — not selectors)

1. Navigate to the URL `http://localhost:8000/rdweb/pages/en-US/login.aspx`.
2. Wait for the login form to appear. The page shows three fields: a
   username input, a password input, and a Sign In button. The username
   field accepts a DOMAIN\\username string (e.g. `plp\\sonawane001`).
3. Type the placeholder `{{RDWEB_USERNAME}}` into the username field.
   The framework resolves this to the configured credential at action
   time. Do NOT type the literal string `RDWEB_USERNAME` or
   `rdweb_username` — always use the `{{ }}` braces.
4. Type the placeholder `{{RDWEB_PASSWORD}}` into the password field.
   Same rules as above. This is a credential — never read it back from
   the screen, never log it, never expand it in the plan's `reason`.
5. Click the Sign In button.
6. Wait for the navigation. On success the URL changes to
   `/rdweb/pages/en-US/Default.aspx` and shows a folder grid (Production,
   Testing).
7. Click the "Production" folder. The grid then shows ~13 RemoteApp tiles
   including Loss Drafts, Proctor, and Explore.

## Decision points

- If the page shows "Invalid credentials" or any login error banner, do
  NOT retry blindly — flag for human. Locked accounts compound on retry.
- If the URL ends with `/sso/idp/SSO.saml2`, the SAML IdP intercepted — fill
  the SSO form using the same credentials and continue.
- Do not click any RemoteApp tile unless the task goal explicitly names it.

## Done when

- The current URL ends with `/Default.aspx/Production`.
- A grid of RemoteApp tiles is visible (≥ 5 tiles).
- Working memory carries `rdweb_authenticated: true`.

## Common errors

- **Session expired** ("Your session has expired"): clear cookies and
  restart from step 1.
- **Form did not appear within 10 s**: simulation server may not be running;
  flag for human rather than retrying.
