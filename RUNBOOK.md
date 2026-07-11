# Runbook

## Environment

Set the following environment variables before running the service:

- `OPENAI_API_KEY` for optional LLM-backed enrichment.
- `MODEL_NAME` for the inference model to use (default: `gpt-4o-mini`).
- `APP_ENV` for the runtime environment (default: `development`).

## Start locally

```bash
python run.py
```

## Run tests

```bash
pytest -q
```

## Docker

```bash
docker build -t super-agent-intelligence .
docker run -p 8000:8000 super-agent-intelligence
```
