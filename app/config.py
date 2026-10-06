from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    one_min_api_key: str
    one_min_base_url: str = "https://api.1min.ai"
    model_summarizer: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    db_path: str = "/data/read-later.db"

    # Articles longer than this get truncated before being sent to the summarizer, so token usage
    # (and 1min.ai credit cost) stays predictable regardless of how long a saved page turns out to be.
    max_article_chars: int = 20000


settings = Settings()
