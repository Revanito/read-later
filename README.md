# read-later

Live at [later.vaultinc.fr](https://later.vaultinc.fr).

A personal read-later app: paste a link, it fetches the page, extracts the article text, and summarizes
it (summary + key points + tags) via a shared [1min.ai](https://1min.ai) proxy. No Discord bot (yet?), no
browser extension (also yet?) - just a web form and a feed of summaries.

## How it works

1. Paste a URL into the box on `/`.
2. The app fetches the page and extracts clean article text, trying [readability-lxml](https://github.com/buriy/python-readability)
   first (it avoids nav/sidebar blocks that trip up density-based extractors on some news sites), falling
   back to [trafilatura](https://github.com/adbar/trafilatura) if that comes back too short.
3. The extracted text is sent to 1min.ai for summarization (model configurable via `MODEL_SUMMARIZER`).
4. The result (title, summary, key points, tags, full text) is stored in SQLite and shown on `/articles`.

Failed extractions/summaries are stored with a `failed` status and can be retried from the article page.

## Stack

FastAPI (async) + httpx + SQLite + Jinja2 templates, no JS framework. Dockerized.

## Running locally

```bash
cd app
pip install -r requirements.txt
cp ../.env.example .env   # fill in ONE_MIN_API_KEY
uvicorn main:app --reload
```

## Running with Docker

```bash
cp .env.example .env   # fill in ONE_MIN_API_KEY
docker compose up -d --build
```

The app listens on port `8086` by default (see `docker-compose.yml`).

## Exposing it publicly

See [deploy/README.md](deploy/README.md) for the nginx reverse-proxy + certbot setup used for
later.vaultinc.fr.

## Config

All settings are environment variables (see `.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `ONE_MIN_API_KEY` | — | required |
| `ONE_MIN_BASE_URL` | `https://api.1min.ai` | |
| `MODEL_SUMMARIZER` | `claude-haiku-4-5-20251001` | any model ID 1min.ai's `chat-with-ai` accepts |
| `DB_PATH` | `/data/read-later.db` | |
| `MAX_ARTICLE_CHARS` | `20000` | article text is truncated to this before summarizing |

## License

MIT
