# Super Agent Intelligence

This project provides a lightweight FastAPI service scaffold for monitoring liquidity anomalies, routing alert workflows, and simulating Eid-eve financial scenarios.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Launch the app: `python run.py`
4. Verify health: `python smoke_test.py`

If port 8000 is already in use, the launcher will automatically select the next available port and print the chosen address in the startup logs.

## Architecture

The application is organized into API, core, schema, service, and template layers to support explainable anomaly analysis and coordination workflows.
