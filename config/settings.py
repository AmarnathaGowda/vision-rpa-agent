from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Runtime mode ──────────────────────────────────────────────────
    # "client_onprem" (default, safe): only local providers allowed.
    # "production":   only local providers allowed (same safety contract as
    #                 client_onprem but signals a deployed environment).
    # "demo":         external API providers permitted (OpenAI / Claude) for
    #                 stakeholder presentations. NEVER enable in production.
    runtime_mode: str = "client_onprem"

    # LLM inference — local (Ollama / vLLM)
    inference_url: str = "http://localhost:11434/v1"
    model_name: str = "minicpm-v:latest"

    # Provider selection: "ollama" (default, production) | "openai" / "claude" (demo only).
    # Provider factory enforces the demo-mode gate; setting llm_provider=openai
    # while runtime_mode != "demo" raises at startup.
    llm_provider: str = "ollama"
    llm_timeout_s: float = 120.0   # per-request timeout; 30.0 recommended for external APIs
    llm_max_retries: int = 2       # tenacity retry attempts on transient errors

    # Claude API (demo/testing only — never used in production)
    # Set via ANTHROPIC_API_KEY env var or .env; never commit the key.
    claude_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""

    # OpenAI API (demo/testing only — gated by runtime_mode=demo)
    # Set via OPENAI_API_KEY env var or .env; never commit the key.
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""

    # ── Lightweight demo execution ────────────────────────────────────
    # When True, demo runs trade accuracy for speed:
    #   - perception downscales to LIGHTWEIGHT_MAX_DIMENSION
    #   - max_tokens halved for plan/perception calls
    #   - extraction skips OCR tier if pdfplumber meets threshold
    lightweight_mode: bool = False
    lightweight_max_dimension: int = 1024   # vs default 1600
    lightweight_max_tokens: int = 256       # vs default 512/1024

    # ── External-API redaction policy ─────────────────────────────────
    # When the active provider is external (openai/claude) and this flag is
    # True, prompts are passed through redaction.redact_prompt() before send.
    redact_external_payloads: bool = True
    # When True, attach redacted prompts to the audit log (helpful for QA;
    # disable in client envs where even redacted payloads are sensitive).
    audit_external_payloads: bool = False

    # Application URLs
    ld_base_url: str = "http://localhost:8000"
    iim_base_url: str = "http://localhost:8001"
    rdweb_url: str = "http://localhost:8000/rdweb"

    # RDP
    rdp_host: str = ""
    rdp_username: str = ""
    rdp_password: str = ""
    rdweb_username: str = ""
    rdweb_password: str = ""
    rdp_keepalive_seconds: int = 240

    # Agent behaviour
    agent_id: str = "agent_01"
    max_loop_steps: int = 50
    confidence_threshold: float = 0.75
    financial_confidence_threshold: float = 0.90
    hitl_timeout_minutes: int = 30
    hitl_server_port: int = 8080

    # Extraction
    vlm_max_pages: int = 5     # VLM tier scans at most this many pages per spec
    vlm_dpi: int = 200         # render DPI for VLM tier
    ocr_dpi: int = 300         # render DPI for OCR tier

    # Mode flags
    use_simulation: bool = True
    headless: bool = False
    demo_slowmo: int = 0

    # Paths
    db_dir: str = "./data/db"
    chroma_path: str = "./data/chroma"
    screenshot_dir: str = "./screenshots"
    audit_log_dir: str = "./logs/audit"
    download_dir: str = "./downloads"


settings = Settings()
