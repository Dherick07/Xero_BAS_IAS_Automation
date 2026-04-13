from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://xero_user:xero_password@postgres:5432/xero_automation"

    # Encryption
    encryption_key: str = "your-32-byte-fernet-key-here-change-me"

    # API Security
    api_key: str = "change-this-api-key-in-production"

    # Playwright
    playwright_timeout: int = 30000
    headless: bool = True

    # CORS
    allowed_origins: str = "http://localhost:8000,http://localhost:3000,http://127.0.0.1:8000"

    # OneDrive
    one_drive_folder_origin: str | None = None
    onedrive_local_prefix: str = "Dexter's files - Bookkeeping & Accounting"
    sharepoint_base_url: str = ""

    # Directories
    download_dir: str = "/app/downloads"
    screenshot_dir: str = "/app/screenshots"
    session_dir: str = "/app/sessions"

    # Screenshot settings
    debug_screenshots: bool = False
    screenshot_retention_days: int = 7

    # Logging
    log_level: str = "INFO"

    # Optional: n8n webhook
    n8n_webhook_url: str | None = None

    # Xero credentials
    xero_email: str | None = None
    xero_password: str | None = None
    xero_security_answer_1: str | None = None
    xero_security_answer_2: str | None = None
    xero_security_answer_3: str | None = None

    # Email fallback
    smtp_host: str = "smtp.office365.com"
    smtp_port: int = 587
    smtp_email: str = ""
    smtp_password: str = ""
    smtp_fallback_email: str = ""

    # Asana
    asana_api_key: str = ""               # Personal Access Token

    # Asana — IAS
    ias_asana_section_gid: str = ""       # IAS "Ready to Export" section
    ias_asana_reassignee_gid: str = ""    # Person to reassign IAS tasks to

    # Asana — BAS
    bas_asana_section_gid: str = ""       # BAS "Ready to Prepare" section
    bas_asana_reassignee_gid: str = ""    # Person to reassign BAS tasks to
    bas_asana_team_gid: str = ""          # Income Tax team GID to tag in BAS comments

    @field_validator('encryption_key')
    @classmethod
    def validate_encryption_key(cls, v):
        if v == "your-32-byte-fernet-key-here-change-me":
            import warnings
            warnings.warn("WARNING: Using default encryption key! Set ENCRYPTION_KEY in .env for production.")
        return v

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v):
        if v == "change-this-api-key-in-production":
            import warnings
            warnings.warn("WARNING: Using default API key! Set API_KEY in .env for production.")
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
