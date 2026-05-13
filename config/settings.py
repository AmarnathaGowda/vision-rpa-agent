from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM inference
    inference_url: str = "http://localhost:11434/v1"
    model_name: str = "minicpm-v:latest"

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

    # Agent behaviour
    agent_id: str = "agent_01"
    max_loop_steps: int = 50
    confidence_threshold: float = 0.75
    financial_confidence_threshold: float = 0.90
    hitl_timeout_minutes: int = 30
    hitl_server_port: int = 8080

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
