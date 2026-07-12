# Super Agent Intelligence - Liquidity & Risk Coordination Platform

## A Multi-Provider Super Agent Decision Support System

**Event:** SUST CSE Carnival 2026 Hackathon (Codex Community) - bKash Presents

---

# 📖 Project Overview

Super Agent Intelligence is a unified, explainable, and role-based decision support platform designed for Multi-Provider Mobile Financial Service (MFS) Super Agents operating across **bKash**, **Nagad**, and **Rocket**.

The platform addresses one of the biggest operational challenges faced by Super Agents:

- Tracking the combined physical cash drawer against fragmented e-money wallets.
- Predicting liquidity shortages before they occur.
- Detecting suspicious behavioral anomalies.
- Maintaining strict provider-level privacy boundaries.
- Providing explainable AI-driven operational insights.

Unlike traditional systems, this project is completely database-free during the hackathon demonstration. Everything runs directly from a static dataset (`base_dataset.json`) together with in-memory RAM storage.

---

# ✨ Key Features

- ✅ Database-less architecture
- ✅ Multi-provider liquidity monitoring
- ✅ Explainable risk scoring
- ✅ Real-time anomaly detection
- ✅ ETA prediction for liquidity exhaustion
- ✅ Territory Officer workflow
- ✅ Risk Analyst dashboard
- ✅ Ticket lifecycle management
- ✅ Role-based access control
- ✅ Human-in-the-loop decision support

---

# 🏗️ Project Architecture

```
super_agent_intelligence/
│
├── app/
│   ├── api/
│   │   ├── alerts.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── health.py
│   │   ├── metrics.py
│   │   ├── router.py
│   │   └── simulation.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── fallback.py
│   │   ├── guardrails.py
│   │   └── session.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── schema.py
│   │
│   ├── schemas/
│   │   ├── enums.py
│   │   ├── request.py
│   │   └── response.py
│   │
│   ├── services/
│   │   ├── analytics_engine.py
│   │   ├── coor_orchestrator.py
│   │   ├── llm_provider.py
│   │   └── routing.py
│   │
│   ├── templates/
│   │   ├── static/
│   │   │   ├── dashboard.css
│   │   │   └── dashboard.js
│   │   │
│   │   ├── login.html
│   │   ├── agent-dash.html
│   │   ├── to-dash.html
│   │   └── risk-dash.html
│   │
│   ├── database.py
│   ├── init_db.py
│   ├── startup.py
│   ├── main.py
│   └── __init__.py
│
├── scripts/
│   ├── seed_demo.py
│   └── __init__.py
│
├── tests/
│   ├── test_analytics_engine.py
│   ├── test_dashboard.py
│   ├── test_database_env.py
│   ├── test_database_models.py
│   ├── test_health.py
│   ├── test_run.py
│   └── test_ui.py
│
├── analytics_engine_prompt.md
├── base_dataset.json
├── Dockerfile
├── Problem Statement.pdf
├── pytest.ini
├── README.md
├── requirements.txt
├── run.py
├── smoke_test.py
├── statement.md
└── test_env.py
```

---

# ⚙️ Installation

## Prerequisites

- Python 3.10+
- Git
- Chrome / Firefox / Edge

---

## Clone Repository

```bash
git clone <your-repository-url>

cd super_agent_intelligence
```

---

## Create Virtual Environment

### Linux / macOS

```bash
python3 -m venv .venv

source .venv/bin/activate
```

### Windows

```bash
python -m venv .venv

.venv\Scripts\activate
```

---

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

# 📦 Dependencies

```
fastapi==0.115.0
uvicorn==0.30.6
pydantic==2.13.4
openai==1.51.2
pytest==8.3.3
httpx==0.27.2
sqlalchemy==2.0.32
pymysql==1.1.1
sqlmodel==0.0.39
```

Although SQLAlchemy and SQLModel are included, the hackathon version does **not** require a database.

---

# 🚀 Running the Project

Simply run

```bash
python run.py
```

Expected output

```
Starting server on http://0.0.0.0:8000

INFO: Uvicorn running on http://0.0.0.0:8000
```

If port **8000** is unavailable, the launcher automatically switches to **8001**, **8002**, or the next available port.

Open your browser

```
http://localhost:8000
```

---

# 👤 Demo Credentials

## Agent

```
Role:
agent

Username:
agent1

Password:
123456
```

---

## Territory Officer

```
Role:
to

Username:
to1

Password:
123456
```

---

## Risk Analyst

```
Role:
risk

Username:
risk1

Password:
123456
```

---

# 🧪 Interactive Demo Workflow

## 1. Login as Agent

Observe:

- Cash Drawer
- Wallet Balances
- Liquidity Health
- Active Alerts
- ETA Prediction

---

## 2. Inject Transaction Burst

Open browser Developer Console (F12)

Run

```javascript
fetch('/api/simulate/inject-burst', {
    method:'POST',
    headers:{
        'Content-Type':'application/json'
    },
    body:JSON.stringify({
        agent_id:'agent1',
        count:20
    })
})
.then(r=>r.json())
.then(console.log)
```

This injects twenty CASH_OUT transactions directly into RAM.

---

## 3. Refresh Dashboard

The dashboard immediately recalculates

- Liquidity Runway
- Velocity
- Risk Score
- Active Alerts

Possible alerts include

- LIQUIDITY_SHORTAGE
- BEHAVIORAL_ANOMALY

All generated in Bangla.

---

## 4. Login as Territory Officer

Observe

- Assigned Tickets
- SLA
- Evidence
- Provider-isolated information

No cross-provider leakage occurs.

---

## 5. Login as Risk Analyst

Observe

- Overall Risk Funnel
- MTTA
- MTTR
- Confirm Real
- False Positive
- Ticket Resolution

---

# 📊 Analytics Engine

The system continuously computes three major metrics.

---

## 1. Liquidity Runway

Predicts how many minutes remain before physical cash reaches zero.

Example

```
পরবর্তী ২৫ মিনিটের মধ্যে ক্যাশ শেষ হয়ে যেতে পারে।
```

---

## 2. Confidence Score

```
0.0
↓

1.0
```

Represents overall confidence that an alert corresponds to genuine operational risk.

Example

```
C = 0.850
```

---

## 3. SLA Metrics

Includes

- MTTA
- MTTR

These evaluate operational response performance.

---

# ⚙️ Detection Methodology

The analytics engine uses deterministic statistics.

Including

- Median
- Median Absolute Deviation (MAD)
- Velocity Analysis
- Structuring Detection
- ETA Calculation
- Confidence Aggregation

No machine learning model is required.

---

# 🎫 Ticket Lifecycle

```
OPEN
↓

ACKNOWLEDGED
↓

UNDER_REVIEW
↓

RESOLVED
```

Ticket state is stored entirely in RAM.

---

# 🔒 Privacy & Isolation

Each dashboard is isolated.

Agent Dashboard

- Own data only.

Territory Officer

- Only assigned territory.

Risk Analyst

- Aggregated system-wide insights.

Provider-specific transaction details remain isolated.

---

# 🛡 Responsible AI Guardrails

The system follows strict responsible AI principles.

- No automatic account blocking
- No automatic fund freezing
- No accusation of fraud
- Human approval required
- Explainable evidence for every alert

---

# ⚠ Assumptions

- Demo dataset only.
- No production APIs.
- No live financial data.
- No real customer information.
- Customer IDs remain hashed.
- Demo password for all users is **123456**.

---

# 🧠 LLM Integration

The current version includes a stub implementation.

```
llm_provider.py
```

Future versions may use OpenAI APIs to generate richer Bangla explanations.

Current alerts remain deterministic and fully explainable.

---

# 🛠 Troubleshooting

## Port Already Busy

No action required.

The launcher automatically switches ports.

---

## Dashboard Not Updating

Perform a hard refresh

```
Ctrl + F5
```

or clear browser cache.

---

## Missing Dataset

Ensure

```
base_dataset.json
```

exists in the project root.

---

# 🧪 Testing

Run all tests

```bash
pytest
```

Or execute

```bash
python smoke_test.py
```

---

# 💡 Technologies Used

- Python
- FastAPI
- Uvicorn
- Pydantic
- SQLModel (unused)
- SQLAlchemy (unused)
- HTML
- CSS
- JavaScript

---

# 👥 User Roles

| Role | Responsibility |
|-------|----------------|
| Agent | Monitor liquidity and receive alerts |
| Territory Officer | Investigate and acknowledge alerts |
| Risk Analyst | Monitor system-wide risk and resolve incidents |

---

# 📌 Highlights

- Database-less
- Real-time simulation
- Multi-provider support
- Explainable AI
- Role isolation
- Risk scoring
- Ticket workflow
- ETA prediction
- Human-in-the-loop
- Hackathon optimized

---

# 📄 License

Developed for **SUST CSE Carnival 2026 Hackathon (Codex Community) - bKash Presents**.

This project is intended solely for educational, research, and demonstration purposes.