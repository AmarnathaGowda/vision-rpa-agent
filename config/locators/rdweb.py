"""Single selector contract for the RD Web Access flow.

Each selector is comma-joined: data-testid (set by the simulation) + a real-DOM
fallback guess (matches what RD Web/IIS likely renders). Playwright's
"first matching" semantics make the same selector work in both environments.

When real client access lands:
1. Open the real page in DevTools.
2. For each `# TODO: verify` selector, replace the second-half fallback with the
   real selector observed.
3. Run `BASE_URL=https://motownplp.pfic.com pytest -m "not requires_simulation"`.
"""


class Login:
    PAGE = '[data-testid="login-page"]'
    USERNAME = '[data-testid="login-username"], input[name="DomainUserName"]'
    PASSWORD = '[data-testid="login-password"], input[name="UserPass"]'
    SUBMIT = '[data-testid="login-submit"], input[type="submit"][value="Sign in"]'
    ERROR = '[data-testid="login-error"]'


class Sso:
    # TODO: verify against real PingIdentity tenant — field names vary per tenant config.
    PAGE = '[data-testid="sso-page"]'
    USERNAME = '[data-testid="sso-username"], input[name="pf.username"]'
    PASSWORD = '[data-testid="sso-password"], input[name="pf.pass"]'
    SUBMIT = '[data-testid="sso-submit"], button[type="submit"]'
    ERROR = '[data-testid="sso-error"]'


class Folders:
    PAGE = '[data-testid="folders-page"]'
    NAV = '[data-testid="rdweb-nav"]'
    GRID = '[data-testid="folder-grid"]'
    SIGNOUT = '[data-testid="rdweb-signout"]'

    @staticmethod
    def tab(folder_name: str) -> str:
        return f'[data-testid="folder-{folder_name}"], a:has-text("{folder_name}")'


class Production:
    PAGE = '[data-testid="production-page"]'
    GRID = '[data-testid="app-grid"]'

    @staticmethod
    def app(app_id: str) -> str:
        return f'[data-testid="app-icon-{app_id}"], a[title="{app_id}"]'


class LossDrafts:
    """Loss Drafts module — Advance Claim Search + results + detail + doc viewer."""

    # Shared chrome (header, tabs)
    WINDOW = '[data-testid="ld-window"]'
    HEADER = '[data-testid="ld-header"]'
    USER_LABEL = '[data-testid="ld-user"]'
    QUICK_SEARCH_FORM = '[data-testid="ld-quick-search"]'
    QUICK_CLAIM_NO = '[data-testid="ld-quick-claim-no"]'
    QUICK_LOAN_NO = '[data-testid="ld-quick-loan-no"]'
    QUICK_SUBMIT = '[data-testid="ld-quick-search-submit"]'
    TABS = '[data-testid="ld-tabs"]'
    TAB_NOTIFICATIONS = '[data-testid="ld-tab-notifications"]'
    TAB_CLAIM_SEARCH = '[data-testid~="ld-tab-claim-search"]'
    TAB_DOCUMENT_MGMT = '[data-testid="ld-tab-document-management"]'
    LOGOUT = '[data-testid="ld-logout"]'

    # Backwards-compatible alias used by existing flows.
    MENU_SEARCH = '[data-testid~="ld-menu-search"]'

    # Advance Claim Search page (the home / shell)
    CLAIM_SEARCH_PAGE = '[data-testid="ld-claim-search-page"]'
    SEARCH_FORM = '[data-testid="ld-search-form"]'
    FIELD_CLIENT = '[data-testid="ld-field-client"]'
    FIELD_LOAN_NO = '[data-testid="ld-field-loan-no"]'
    FIELD_CLAIM_NO = '[data-testid="ld-field-claim-no"]'
    FIELD_PRIMARY_CONTACT_NAME = '[data-testid="ld-field-primary-contact-name"]'
    SEARCH_STATUS = '[data-testid="ld-search-status"]'
    FIELD_PROCESSED_AS = '[data-testid="ld-field-processed-as"]'
    FIELD_INSURANCE_COMPANY = '[data-testid="ld-field-insurance-company"]'
    FIELD_PRIMARY_CSR = '[data-testid="ld-field-primary-csr"]'
    FIELD_CONTRACTOR_COMPANY = '[data-testid="ld-field-contractor-company"]'
    FIELD_CONTRACTOR_CONTACT = '[data-testid="ld-field-contractor-contact"]'
    FIELD_FEDERAL_DISASTER = '[data-testid="ld-field-federal-disaster"]'
    FIELD_OPENED_FROM = '[data-testid="ld-field-opened-from"]'
    FIELD_OPENED_TO = '[data-testid="ld-field-opened-to"]'
    FIELD_INVESTOR = '[data-testid="ld-field-investor"]'
    FIELD_ADDRESS = '[data-testid="ld-field-address"]'
    FIELD_CITY = '[data-testid="ld-field-city"]'
    FIELD_STATE = '[data-testid="ld-field-state"]'
    FIELD_ZIP = '[data-testid="ld-field-zip"]'
    SEARCH_SUBMIT = '[data-testid="ld-search-submit"]'

    # Results page
    SEARCH_PAGE = '[data-testid="ld-search-page"]'
    RESULTS_TABLE = '[data-testid="ld-results-table"]'
    RESULTS_BODY = '[data-testid="ld-results-body"]'
    RESULTS_EMPTY = '[data-testid="ld-results-empty"]'
    APPLIED_FILTERS = '[data-testid="ld-applied-filters"]'

    @staticmethod
    def row(claim_no: str) -> str:
        return f'[data-testid="ld-row-{claim_no}"]'

    @staticmethod
    def open_link(claim_no: str) -> str:
        return f'[data-testid="ld-open-{claim_no}"]'

    # Detail page
    DETAIL = '[data-testid="ld-claim-detail"]'
    CLAIM_TITLE = '[data-testid="ld-claim-title"]'
    FIELD_POLICY = '[data-testid="ld-field-policy"]'
    FIELD_BORROWER = '[data-testid="ld-field-borrower"]'
    FIELD_STATUS = '[data-testid="ld-field-status"]'
    FIELD_AMOUNT = '[data-testid="ld-field-amount"]'
    DOCUMENTS = '[data-testid="ld-documents"]'

    @staticmethod
    def doc_link(index: int) -> str:
        return f'[data-testid="ld-doc-{index}"]'

    # Doc viewer
    DOC_VIEWER = '[data-testid="ld-doc-viewer"]'
    DOC_TITLE = '[data-testid="ld-doc-title"]'
    DOC_CANVAS = '[data-testid="ld-doc-canvas"]'

    # Notifications page (placeholder)
    NOTIFICATIONS_PAGE = '[data-testid="ld-notifications-page"]'

    # ---------- Document Management ---------------------------------------
    DOCUMENT_MANAGEMENT_PAGE = '[data-testid="ld-document-management-page"]'

    # Selected Claim header table
    SELECTED_CLAIM_TABLE = '[data-testid="ld-selected-claim-table"]'
    SELECTED_CLAIM_BODY = '[data-testid="ld-selected-claim-body"]'
    SELECTED_CLAIM_ROW = '[data-testid="ld-selected-claim-row"]'
    SELECTED_CLAIM_EMPTY = '[data-testid="ld-selected-claim-empty"]'

    # Available Documents (left panel)
    AVAILABLE_DOCS_PANEL = '[data-testid="ld-available-documents-panel"]'
    PENDING_DOCS_TABLE = '[data-testid="ld-pending-docs-table"]'
    PENDING_DOCS_BODY = '[data-testid="ld-pending-docs-body"]'
    SHOW_ALL_DROPDOWN = '[data-testid="ld-show-all"]'

    # Case 1 — fixed pinned record. Stable across simulation restarts and
    # independent of the row's id, so automation can locate it without
    # hardcoding the id (use these instead of pending_doc("8182408")).
    CASE1_DOC_ID = "8182408"
    CASE1_CLIENT_ID = "29983"
    CASE1_BATCH = "1584567"
    CASE1_ROW = '[data-testid~="case1-row"]'
    CASE1_LINK = '[data-testid~="case1-link"]'

    # Case 2 — three fixed batch records, selected together (multi-select).
    # DocSet order: 1 (8184371), 3 (8184373), 2 (8184372).
    CASE2_CLIENT_ID = "29983"
    CASE2_BATCH = "1584839"
    CASE2_DOC_IDS = ("8184371", "8184373", "8184372")
    CASE2_ROWS = '[data-testid~="case2-row"]'        # matches all 3
    CASE2_SELECTION_COUNTER = "#ld-selection-counter"

    @staticmethod
    def case2_row(doc_id: str) -> str:
        """Selector for a single Case 2 row by its document ID."""
        return f'[data-testid~="ld-pending-doc-{doc_id}"]'

    @staticmethod
    def case2_link(doc_id: str) -> str:
        """Selector for the Link anchor inside a Case 2 row."""
        return f'[data-testid~="case2-link-{doc_id}"]'

    @staticmethod
    def pending_doc(doc_id: str) -> str:
        # Note: data-testid for rows now contains a space-separated word list
        # for some rows (e.g. case1-row). Use ~= to match the per-id token.
        return f'[data-testid~="ld-pending-doc-{doc_id}"]'

    # Claim Search (right panel)
    CLAIM_SEARCH_PANEL = '[data-testid="ld-claim-search-panel"]'
    DOC_CLAIM_SEARCH_FORM = '[data-testid="ld-doc-claim-search-form"]'
    DOC_SEARCH_LOAN = '[data-testid="ld-doc-search-loan"]'
    DOC_SEARCH_CLAIM = '[data-testid="ld-doc-search-claim"]'
    DOC_SEARCH_PCN = '[data-testid="ld-doc-search-pcn"]'
    DOC_SEARCH_SUBMIT = '[data-testid="ld-doc-search-submit"]'
    DOC_CLAIM_RESULTS_BODY = '[data-testid="ld-doc-claim-results-body"]'
    DOC_CLAIM_RESULTS_EMPTY = '[data-testid="ld-doc-claim-results-empty"]'

    @staticmethod
    def doc_claim_row(loan_no: str) -> str:
        return f'[data-testid="ld-doc-claim-row-{loan_no}"]'

    # Standalone Claim Search results (loan-mode table)
    @staticmethod
    def loan_link(loan_no: str) -> str:
        return f'[data-testid="ld-loan-link-{loan_no}"]'

    # Claim Details page
    CLAIM_DETAILS_PAGE = '[data-testid="ld-claim-details-page"]'
    CLAIM_DETAILS_LD_ID = '[data-testid="ld-cd-ld-id"]'
    CLAIM_DETAILS_CLAIM_NO = '[data-testid="ld-cd-claim-no"]'
    CLAIM_DETAILS_STATUS = '[data-testid="ld-cd-status"]'
    CLAIM_DETAILS_LOAN_NO = '[data-testid="ld-cd-loan-no"]'
    CLAIM_DETAILS_BORROWER = '[data-testid="ld-cd-borrower"]'

    # Letter Requests section in Claim Details sidebar
    LETTER_REQUESTS_HEADER = '[data-testid="ld-cd-letter-requests-header"]'
    LETTER_REQUESTS_ADD = '[data-testid="ld-cd-letter-requests-add"]'
    CREATE_LETTER_PANEL = '[data-testid="ld-cd-create-letter-panel"]'
    LETTER_TEMPLATE_DROPDOWN = '[data-testid="ld-cd-letter-template-dropdown"]'

    # Stage 7 — letter form fields
    LETTER_TEMPLATE_FIELDS = '[data-testid="ld-cd-template-fields"]'
    LETTER_CSR_EMAIL = '[data-testid="ld-cd-csr-email"]'
    LETTER_EQT_LOCK = '[data-testid="ld-cd-eqt-lock"]'
    LETTER_EQT_TABLE = '[data-testid="ld-cd-eqt-table"]'
    LETTER_SAVE_BTN = '[data-testid="ld-cd-save-letter"]'
    LETTER_TOAST = '[data-testid="ld-cd-toast"]'
    LETTER_TOAST_MSG = '[data-testid="ld-cd-toast-msg"]'
    LETTER_LR_SAVED_ROW = '[data-testid="ld-cd-lr-saved-row"]'
    LETTER_LR_STATUS = '[data-testid="ld-cd-lr-status"]'

    # Stage 8 — Communication History
    TAB_COMM_HISTORY = '[data-testid="ld-cd-tab-comm-history"]'
    COMM_HISTORY_PANEL = '[data-testid="ld-cd-comm-history-panel"]'
    COMM_HISTORY_TBODY = '[data-testid="ld-cd-ch-tbody"]'
    COMM_HISTORY_NEW_ROW = '[data-testid="ld-cd-ch-new-row"]'
    COMM_HISTORY_NOTE_MSG = '[data-testid="ld-cd-ch-note-msg"]'

    # Stage 9 — Claim Detail tab + Edit Claim modal
    TAB_CLAIM_DETAIL = '[data-testid="ld-cd-tab-claim-detail"]'
    CLAIM_DETAIL_EDIT_BTN = '[data-testid="ld-cd-claim-detail-edit"]'
    EDIT_CLAIM_MODAL = '[data-testid="ld-cd-edit-claim-modal"]'
    EDIT_CLAIM_NO = '[data-testid="ld-cd-edit-claim-no"]'
    EDIT_CLAIM_SAVE = '[data-testid="ld-cd-edit-save"]'
    EDIT_CLAIM_CANCEL = '[data-testid="ld-cd-edit-cancel"]'

    # Stage 10 — Assign Document to Claim modal (Document Management)
    ASSIGN_DOCS_BTN = '[data-testid="ld-btn-assign-docs"]'
    ASSIGN_MODAL = '[data-testid="ld-assign-modal"]'
    ASSIGN_MODAL_BODY = '[data-testid="ld-assign-modal-body"]'
    ASSIGN_MODAL_SAVE = '[data-testid="ld-assign-modal-save"]'
    ASSIGN_MODAL_CLOSE = '[data-testid="ld-assign-modal-close"]'
    ASSIGN_MODAL_CANCEL = '[data-testid="ld-assign-modal-cancel"]'
    DM_TOAST = '[data-testid="ld-dm-toast"]'
    DM_TOAST_MSG = '[data-testid="ld-dm-toast-msg"]'

    @staticmethod
    def assign_row(docset: int) -> str:
        return f'[data-testid="ld-assign-row-{docset}"]'

    @staticmethod
    def assign_type(docset: int) -> str:
        return f'[data-testid="ld-assign-type-{docset}"]'

    @staticmethod
    def assign_label(docset: int) -> str:
        return f'[data-testid="ld-assign-label-{docset}"]'

    # Stage 10 — Claim Documents tab & panel
    CLAIM_DOCS_PANEL = '[data-testid="ld-cd-claim-docs-panel"]'
    CLAIM_DOCS_TBODY = '[data-testid="ld-cd-claim-docs-tbody"]'
    CLAIM_DOCS_EMPTY = '[data-testid="ld-cd-claim-docs-empty"]'

    @staticmethod
    def claim_doc_row(docset: int) -> str:
        return f'[data-testid="ld-cd-claim-doc-row-{docset}"]'

    @staticmethod
    def claim_doc_type(docset: int) -> str:
        return f'[data-testid="ld-cd-claim-doc-type-{docset}"]'

    @staticmethod
    def claim_doc_label(docset: int) -> str:
        return f'[data-testid="ld-cd-claim-doc-label-{docset}"]'

    # Stage 12 — Transaction modal
    ADD_DEPOSIT_LINK  = '[data-testid="ld-cd-add-deposit-link"]'
    TXN_MODAL         = '[data-testid="ld-cd-txn-modal"]'
    TXN_TYPE          = '[data-testid="ld-cd-txn-type"]'
    TXN_AMOUNT        = '[data-testid="ld-cd-txn-amount"]'
    TXN_DATE          = '[data-testid="ld-cd-txn-date"]'
    TXN_PAYER         = '[data-testid="ld-cd-txn-payer"]'
    TXN_CHECK_NO      = '[data-testid="ld-cd-txn-check-no"]'
    TXN_NOTES         = '[data-testid="ld-cd-txn-notes"]'
    TXN_CREATE        = '[data-testid="ld-cd-txn-create"]'
    TXN_ROWS          = '[data-testid^="ld-cd-txn-row-"]'

    # Stage 13 — Notification creation
    # Notifications section on Claim Details page
    NOTIF_SECTION     = '[data-testid="ld-cd-notifications-section"]'
    NOTIF_ADD_BTN     = '[data-testid="ld-cd-notifications-add"]'
    # Grid rows (after creation)
    NOTIF_GRID_BODY   = '[data-testid="ld-cd-notif-grid-body"]'
    NOTIF_GRID_ROWS   = '[data-testid^="ld-cd-notif-grid-row-"]'
    # Create Notifications dialog
    NOTIF_MODAL       = '[data-testid="ld-cd-notif-modal"]'
    NOTIF_SEARCH      = '[data-testid="ld-cd-notif-search"]'
    NOTIF_ROWS_BODY   = '[data-testid="ld-cd-notif-rows-body"]'
    NOTIF_MODAL_ROWS  = '[data-testid^="ld-cd-notif-row-"]'
    NOTIF_PAGES_INFO  = '[data-testid="ld-cd-notif-pages-info"]'
    NOTIF_NEXT        = '[data-testid="ld-cd-notif-next"]'
    NOTIF_PREV        = '[data-testid="ld-cd-notif-prev"]'
    NOTIF_CREATE      = '[data-testid="ld-cd-notif-create"]'
    NOTIF_CANCEL      = '[data-testid="ld-cd-notif-cancel"]'
    # Success toast
    NOTIF_TOAST       = '[data-testid="ld-cd-notif-toast"]'
    NOTIF_TOAST_MSG   = '[data-testid="ld-cd-notif-toast-msg"]'

    # Stage 9 — LD Module verification page
    LD_MODULE_PAGE = '[data-testid="ld-module-page"]'
    LD_MODULE_SEARCH_LOAN = '[data-testid="ld-mod-search-loan"]'
    LD_MODULE_SEARCH_SUBMIT = '[data-testid="ld-mod-search-submit"]'
    LD_MODULE_RESULT_ROW = '[data-testid="ld-mod-result-row"]'
    LD_MODULE_RESULT_CLAIM_NO = '[data-testid="ld-mod-result-claim-no"]'
    LD_MODULE_RESULT_BORROWER = '[data-testid="ld-mod-result-borrower"]'
    LD_MODULE_RESULT_STATUS = '[data-testid="ld-mod-result-status"]'


class Explorer:
    """Windows File Explorer simulation — Case 3 Hold Check navigation."""

    # Page-level markers
    WINDOW          = '[data-testid="explorer-window"]'
    LOADING         = '[data-testid="explorer-loading"]'
    HOLD_CHECK_ROOT = '[data-testid="explorer-hold-check-root"]'

    # Address bar
    BREADCRUMB      = '[data-testid="explorer-breadcrumb"]'
    BC_TARGET       = '[data-testid="explorer-bc-target"]'

    # File list
    FILE_LIST       = '[data-testid="explorer-file-list"]'
    FILE_ROW_LATEST = '[data-testid~="explorer-file-row-latest"]'
    FILE_ROW_0      = '[data-testid~="explorer-file-row-0"]'

    @staticmethod
    def file_row(index: int) -> str:
        return f'[data-testid="explorer-file-row-{index}"]'

    @staticmethod
    def folder_row(name: str) -> str:
        return f'[data-testid="explorer-folder-{name}"]'

    # Status bar
    STATUS_BAR      = '[data-testid="explorer-status-bar"]'
    ITEM_COUNT      = '[data-testid="explorer-item-count"]'
    SELECTION_INFO  = '[data-testid="explorer-selection-info"]'

    # Left nav pane
    NAV_PANE        = '[data-testid="explorer-nav-pane"]'
    NAV_NETWORK     = '[data-testid="explorer-nav-network"]'
    NAV_COMMVAULT   = '[data-testid="explorer-nav-commvault"]'
    NAV_DOCS        = '[data-testid="explorer-nav-docs"]'

    # Case 3 check PDF — Safeco Insurance, batch 42650, 04/17/2026
    CHECK_PDF_ROW   = '[data-testid~="explorer-check-pdf-row"]'
    CHECK_PDF_LINK  = '[data-testid="explorer-check-pdf-link"]'
    CASE3_PDF_PATH  = "/static/pdfs/case3/Case3_42650_4172026_safeco_check.pdf"
    CASE3_PDF_BATCH = "42650"
    CASE3_DOC_REF   = "04172026_42650_62"


class RemoteApps:
    """MotownPLP RemoteApps production workspace — Case 3 selectors."""

    PRODUCTION_PAGE = '[data-testid="production-page"]'
    APP_GRID        = '[data-testid="app-grid"]'
    APP_EXPLORE     = '[data-testid="app-icon-Explorer"]'
    APP_LOSS_DRAFTS = '[data-testid="app-icon-LossDrafts"]'


class Proctor:
    """Proctor Financial, Inc. — IIM Loan Search (Case 3 recovery flow)."""

    SEARCH_FORM     = '[data-testid="pf-search-form"]'
    RESULTS_SECTION = '[data-testid="pf-results-section"]'
    RESULTS_BODY    = '[data-testid="pf-results-body"]'
    CONTACT_NAME    = '[data-testid="pf-input-contact-name"]'
    LOAN_NUMBER     = '[data-testid="pf-input-loan-number"]'
    SEARCH_BTN      = '[data-testid="pf-btn-search"]'
    RESET_BTN       = '[data-testid="pf-btn-reset"]'
    NO_RESULTS      = '[data-testid="pf-no-results"]'

    # Loan Details page
    LOAN_DETAILS_PAGE = '[data-testid="pf-loan-details-page"]'
    BORROWER          = '[data-testid="pf-borrower"]'
    CARRIER           = '[data-testid="pf-ins-carrier-1"] input'
    LOAN_BAR_SUMMARY  = '[data-testid="pf-loan-bar-summary"]'

    @staticmethod
    def result_row(loan_no: str) -> str:
        return f'[data-testid="pf-result-row-{loan_no}"]'

    @staticmethod
    def view_btn(loan_no: str) -> str:
        return f'[data-testid="pf-result-view-{loan_no}"]'


class Explorer4:
    """Windows File Explorer simulation — Case 4 Stamp and Go navigation."""

    # Shares core selectors with Explorer (same CSS, different template)
    WINDOW          = '[data-testid="explorer-window"]'
    LOADING         = '[data-testid="explorer-loading"]'
    STAMPGO_ROOT    = '[data-testid="explorer-stampgo-root"]'
    BREADCRUMB      = '[data-testid="explorer-breadcrumb"]'
    BC_TARGET       = '[data-testid="explorer-bc-target"]'
    FILE_LIST       = '[data-testid="explorer-file-list"]'

    # The latest Coforge LD Daily Tasks Excel file row
    EXCEL_ROW       = '[data-testid~="explorer-excel-row"]'
    EXCEL_LINK      = '[data-testid="explorer-excel-link"]'

    STATUS_BAR      = '[data-testid="explorer-status-bar"]'


class Case4Search:
    """Claim Search — Stamp and Go flow (Case 4)."""

    LOAN_NO        = "0156312522"
    LOAN_NO_SHORT  = "156312522"
    BORROWER       = "CRYSTAL D STEWART"
    PROCESSING_AS  = "Stamp and Go"
    STATUS         = "OPEN"


class Case4Docs:
    """Claim Documents tab — Stage 8 Stamp and Go document acceptance."""

    # Internal tab link
    TAB_CLAIM_DOCUMENTS = '[data-testid="ld-cd-tab-claim-documents"]'

    # Panel
    CLAIM_DOCS_PANEL    = '[data-testid="ld-cd-claim-docs-panel"]'

    # Unreviewed section
    UNREVIEWED_SECTION  = '[data-testid="ld-cd-claim-docs-unreviewed-section"]'
    UNREVIEWED_TBODY    = '[data-testid="ld-cd-claim-docs-tbody"]'
    UNREVIEWED_COUNT    = '[data-testid="ld-cd-claim-docs-unreviewed-count"]'
    UNREVIEWED_EMPTY    = '[data-testid="ld-cd-claim-docs-empty"]'

    # Per-document rows (unreviewed)
    ROW_CHECK           = '[data-testid="ld-cd-claim-docs-row-check"]'
    ROW_ADJUSTER        = '[data-testid="ld-cd-claim-docs-row-adjuster"]'

    # Source "S" links — each opens the PDF in a new browser tab
    SRC_CHECK           = '[data-testid="ld-cd-claim-docs-src-check"]'
    SRC_ADJUSTER        = '[data-testid="ld-cd-claim-docs-src-adjuster"]'

    # Accept ✓ buttons
    ACCEPT_CHECK        = '[data-testid="ld-cd-claim-docs-accept-check"]'
    ACCEPT_ADJUSTER     = '[data-testid="ld-cd-claim-docs-accept-adjuster"]'

    # Accepted section
    ACCEPTED_SECTION    = '[data-testid="ld-cd-claim-docs-accepted-section"]'
    ACCEPTED_TBODY      = '[data-testid="ld-cd-claim-docs-accepted-tbody"]'
    ACCEPTED_COUNT      = '[data-testid="ld-cd-claim-docs-accepted-count"]'

    # Post-acceptance row testids
    ACCEPTED_ROW_CHECK    = '[data-testid="ld-cd-claim-docs-accepted-row-check"]'
    ACCEPTED_ROW_ADJUSTER = '[data-testid="ld-cd-claim-docs-accepted-row-adjuster"]'

    # PDF static paths (served by simulation)
    PDF_CHECK_PATH      = "/static/pdfs/case4/case4_check.pdf"
    PDF_ADJUSTER_PATH   = "/static/pdfs/case4/case4_adjuster_report.pdf"


class Case4Edit:
    """Edit Claim modal — Stage 9 field selectors."""

    # Trigger button (pencil icon on Claim Detail card)
    EDIT_TRIGGER    = '[data-testid="ld-cd-claim-detail-edit"]'
    MODAL           = '[data-testid="ld-cd-edit-claim-modal"]'

    # Fields
    CLAIM_NO        = '[data-testid="ld-cd-edit-claim-no"]'
    INSURANCE_CO    = '[data-testid="ld-cd-edit-insurance-co"]'
    LOSS_TYPE       = '[data-testid="ld-cd-edit-loss-type"]'
    LOSS_DATE       = '[data-testid="ld-cd-edit-loss-date"]'
    CLAIM_TOTAL     = '[data-testid="ld-cd-edit-claim-total"]'
    PROCESSED_AS    = '[data-testid="ld-cd-edit-processed-as"]'

    # Actions
    SAVE            = '[data-testid="ld-cd-edit-save"]'
    CANCEL          = '[data-testid="ld-cd-edit-cancel"]'

    # Toast after save
    TOAST           = '[data-testid="ld-cd-toast"]'
    TOAST_MSG       = '[data-testid="ld-cd-toast-msg"]'


class Case4Tasks:
    """Claim Tasks section — Stage 9 selectors."""

    SECTION         = '[data-testid="ld-cd-claim-tasks-section"]'
    ADD_BTN         = '[data-testid="ld-cd-claim-tasks-add"]'
    TBODY           = '[data-testid="ld-cd-claim-tasks-tbody"]'
    OPEN_GROUP      = '[data-testid="ld-cd-claim-tasks-open-group"]'

    # Create Claim Event modal
    EVENT_MODAL     = '[data-testid="ld-cd-claim-event-modal"]'
    EVENT_SELECT    = '[data-testid="ld-cd-claim-event-select"]'
    EVENT_SAVE      = '[data-testid="ld-cd-claim-event-save"]'
    EVENT_CANCEL    = '[data-testid="ld-cd-claim-event-cancel"]'

    # Tasks toast
    TASKS_TOAST     = '[data-testid="ld-cd-tasks-toast"]'
    TASKS_TOAST_MSG = '[data-testid="ld-cd-tasks-toast-msg"]'

    MATCHED_ROW    = '[data-testid="ld-row-0156312522"]'
    MATCHED_LINK   = '[data-testid="ld-loan-link-0156312522"]'

    CLAIM_DETAILS_PAGE      = '[data-testid="ld-claim-details-page"]'
    CLAIM_DETAILS_LOAN_NO   = '[data-testid="ld-cd-loan-no"]'
    CLAIM_DETAILS_BORROWER  = '[data-testid="ld-cd-borrower"]'
    CLAIM_DETAILS_STATUS    = '[data-testid="ld-cd-status"]'
    CLAIM_DETAILS_PROC_AS   = '[data-testid="ld-cd-processing-as"]'


class Case4LetterRequest:
    """Letter Request sidebar and Create Letter panel — Stage 11."""

    # Sidebar
    HEADER         = '[data-testid="ld-cd-letter-requests-header"]'
    ADD_BTN        = '[data-testid="ld-cd-letter-requests-add"]'
    BODY           = '[data-testid="ld-cd-letter-requests-body"]'
    PENDING_ROW    = '[data-testid="ld-cd-lr-pending-row"]'
    STATUS         = '[data-testid="ld-cd-lr-status"]'

    # Create Letter panel
    PANEL          = '[data-testid="ld-cd-create-letter-panel"]'
    TEMPLATE_DD    = '[data-testid="ld-cd-letter-template-dropdown"]'
    TEMPLATE_FIELDS = '[data-testid="ld-cd-template-fields"]'
    SAVE           = '[data-testid="ld-cd-save-letter"]'

    # Toast (shared letter toast)
    TOAST          = '[data-testid="ld-cd-toast"]'
    TOAST_MSG      = '[data-testid="ld-cd-toast-msg"]'

    STAMP_AND_GO_OPTION = 'stampandgo'   # option value in the template dropdown


class Case4SgRequest:
    """Stamp and Go Request sidebar menu, modal, and Requests tab — Stage 11."""

    # Sidebar submenu
    MENU_TRIGGER   = '[data-testid="ld-cd-sg-menu-trigger"]'
    SUBMENU        = '[data-testid="ld-cd-sg-submenu"]'
    REQUEST_LINK   = '[data-testid="ld-cd-sg-request-link"]'

    # Create SG Request modal
    MODAL          = '[data-testid="ld-cd-sg-create-modal"]'
    DRAW_TYPE      = '[data-testid="ld-cd-sg-draw-type"]'
    HELD_CHECK     = '[data-testid="ld-cd-sg-held-check"]'
    DOCUMENT_LIST  = '[data-testid="ld-cd-sg-document-list"]'
    DOCUMENT_RADIO = '[data-testid="ld-cd-sg-document-radio"]'
    NOTE           = '[data-testid="ld-cd-sg-note"]'
    SUBMIT         = '[data-testid="ld-cd-sg-submit"]'
    CANCEL         = '[data-testid="ld-cd-sg-cancel"]'

    # SG success toast
    TOAST          = '[data-testid="ld-cd-sg-toast"]'
    TOAST_MSG      = '[data-testid="ld-cd-sg-toast-msg"]'

    # Stamp and Go Requests tab + panel
    TAB            = '[data-testid="ld-cd-tab-sg-requests"]'
    PANEL          = '[data-testid="ld-cd-sg-requests-panel"]'
    TABLE          = '[data-testid="ld-cd-sg-requests-table"]'
    TBODY          = '[data-testid="ld-cd-sg-requests-tbody"]'
    RECORD_COUNT   = '[data-testid="ld-cd-sg-record-count"]'

    # Request row cells (format with row index, default 0)
    @staticmethod
    def row(i: int = 0) -> str:
        return f'[data-testid="ld-cd-sg-request-row-{i}"]'

    @staticmethod
    def status(i: int = 0) -> str:
        return f'[data-testid="ld-cd-sg-request-status-{i}"]'

    @staticmethod
    def amount(i: int = 0) -> str:
        return f'[data-testid="ld-cd-sg-request-amount-{i}"]'

    @staticmethod
    def payer(i: int = 0) -> str:
        return f'[data-testid="ld-cd-sg-request-payer-{i}"]'

    @staticmethod
    def draw_type(i: int = 0) -> str:
        return f'[data-testid="ld-cd-sg-request-draw-type-{i}"]'

    @staticmethod
    def check_no(i: int = 0) -> str:
        return f'[data-testid="ld-cd-sg-request-check-no-{i}"]'

    # Expected values for Case 4
    EXPECTED_STATUS    = "REQUESTED"
    EXPECTED_PAYER     = "State Farm"
    EXPECTED_DRAW_TYPE = "Stamp and Go"
    EXPECTED_AMOUNT    = "$10,640.58"
    EXPECTED_CHECK_NO  = "2117060367"


class Case4Notifications:
    """Notifications section and Create Notifications modal — Stage 12."""

    # Notifications section (claim detail page)
    SECTION    = '[data-testid="ld-cd-notifications-section"]'
    ADD_BTN    = '[data-testid="ld-cd-notifications-add"]'
    GRID_BODY  = '[data-testid="ld-cd-notif-grid-body"]'

    # Create Notifications modal
    MODAL      = '[data-testid="ld-cd-notif-modal"]'
    SEARCH     = '[data-testid="ld-cd-notif-search"]'
    ROWS_BODY  = '[data-testid="ld-cd-notif-rows-body"]'
    CREATE_BTN = '[data-testid="ld-cd-notif-create"]'
    CANCEL_BTN = '[data-testid="ld-cd-notif-cancel"]'

    # Toast
    TOAST      = '[data-testid="ld-cd-notif-toast"]'
    TOAST_MSG  = '[data-testid="ld-cd-notif-toast-msg"]'

    # Notifications to create (Case 4 Stamp and Go)
    NOTIF_1         = "StampGo Requirements Met"
    NOTIF_1_SEARCH  = "stamp"
    NOTIF_2         = "Freedom Check to be Mailed"
    NOTIF_2_SEARCH  = "fre"


class Case4ProcessEvent:
    """Process Event modal — Stage 10 (QUESTION and DOCUMENT variants)."""

    MODAL             = '[data-testid="ld-cd-process-event-modal"]'
    DIALOG            = '[data-testid="ld-cd-process-event-dialog"]'
    TITLE             = '[data-testid="ld-cd-pe-title"]'

    # QUESTION variant
    QUESTION_SECTION  = '[data-testid="ld-cd-pe-question-section"]'
    CHECKBOX_UNDER40K = '[data-testid="ld-cd-pe-under40k-checkbox"]'

    # DOCUMENT variant
    DOC_SECTION       = '[data-testid="ld-cd-pe-doc-section"]'
    SELECT_DOCS_LINK  = '[data-testid="ld-cd-pe-select-docs-link"]'
    ASSOC_DOCS        = '[data-testid="ld-cd-pe-assoc-docs"]'

    NOTES             = '[data-testid="ld-cd-pe-notes"]'
    SAVE              = '[data-testid="ld-cd-pe-save"]'
    CANCEL            = '[data-testid="ld-cd-pe-cancel"]'


class Case4SelectDocs:
    """Select Documents dual-list modal — Stage 10."""

    MODAL      = '[data-testid="ld-cd-select-docs-modal"]'
    DIALOG     = '[data-testid="ld-cd-select-docs-dialog"]'
    AVAILABLE  = '[data-testid="ld-cd-sd-available"]'
    ASSIGNED   = '[data-testid="ld-cd-sd-assigned"]'
    MOVE_RIGHT = '[data-testid="ld-cd-sd-move-right"]'
    MOVE_LEFT  = '[data-testid="ld-cd-sd-move-left"]'
    SAVE       = '[data-testid="ld-cd-sd-save"]'


class Case3Search:
    """Claim Search — borrower name flow specific to Case 3 Hold Check."""

    # Expected data for the DIANE S BISSETT claim
    LOAN_NO      = "9703503582"
    CLAIM_NO     = "3300504951"
    CONTACT_NAME = "DIANE S BISSETT"
    ADDRESS      = "7836 TUSCAN BAY CIR"
    CITY         = "WESLEY CHAPEL"
    STATE        = "FL"
    ZIP          = "33545"

    # Selectors built from known loan_no
    MATCHED_ROW  = '[data-testid="ld-row-9703503582"]'
    MATCHED_LINK = '[data-testid="ld-loan-link-9703503582"]'

    # Claim Details page verifiers after claim open
    CLAIM_DETAILS_PAGE    = '[data-testid="ld-claim-details-page"]'
    CLAIM_DETAILS_LOAN_NO = '[data-testid="ld-cd-loan-no"]'
    CLAIM_DETAILS_BORROWER = '[data-testid="ld-cd-borrower"]'


# ── Flat-dict adapter for the new framework's SelectorResolver ─────────────
# The legacy automation code consumes the class form (e.g. `Login.USERNAME`).
# The vision-rpa-agent SelectorResolver consumes a flat `name -> selector` map.
# This block flattens every class-level constant into ALL so both APIs work.
# Adding a new selector to a class above automatically surfaces it here.
import inspect as _inspect

ALL: dict[str, str] = {}
for _name, _cls in list(globals().items()):
    if _inspect.isclass(_cls) and _cls.__module__ == __name__:
        for _attr, _val in vars(_cls).items():
            if _attr.startswith("_"):
                continue
            if isinstance(_val, str):
                # Friendly key:  "login_username", "case3search_loan_no", etc.
                key = f"{_name.lower()}_{_attr.lower()}"
                ALL[key] = _val
                # Also expose under the unqualified attr name when there's no
                # collision — lets SOPs say "username" instead of "login_username".
                short = _attr.lower()
                if short not in ALL:
                    ALL[short] = _val
del _inspect
