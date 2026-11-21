from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    one_min_api_key: str
    one_min_base_url: str = "https://api.1min.ai"
    model_summarizer: str = "us.anthropic.claude-haiku-5-5"
    # Used for the Books tab's "Recommend similar" button. Defaults to the same mid-tier model as the
    # summarizer - recommendations are short JSON lists, a flagship model isn't worth the credits.
    model_recommender: str = "us.anthropic.claude-haiku-5-5"

    db_path: str = "/data/read-later.db"

    # Articles longer than this get truncated before being sent to the summarizer, so token usage
    # (and 1min.ai credit cost) stays predictable regardless of how long a saved page turns out to be.
    max_article_chars: int = 20000

    # --- Auth ---
    # Required: the whole app sits behind this single password. The app refuses to start without it,
    # so a missing .env line fails closed instead of silently exposing the app (and the 1min.ai key).
    app_password: str
    # Optional: signs the session cookie. If empty, it's derived from app_password, which means
    # changing the password also logs out every existing session.
    secret_key: str = ""
    session_days: int = 30

    # --- Books ---
    # Optional Hardcover personal access token (hardcover.app -> Settings -> Hardcover API).
    # Only the `read:catalog` scope is needed. Without it, the Books tab still works off Open Library
    # alone, just without automatic series/read-order import.
    hardcover_token: str = ""

    # --- New-release watch ---
    # How often the background check asks Hardcover for new books by followed authors / in imported
    # series. One check is a single Hardcover request, so even hourly is far under the free-plan limit.
    watch_interval_hours: float = 6
    # How far back a check looks: catches books Hardcover's librarians only add a few days after release.
    watch_lookback_days: int = 21


settings = Settings()
