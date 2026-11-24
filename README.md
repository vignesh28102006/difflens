# 🔍 DiffLens — Deterministic Code Risk & Change Intelligence Platform

**DiffLens** is an AI-era code risk analyzer and change intelligence platform for Pull Requests. Unlike LLM-wrapper PR reviewers that hallucinate scores or rely on nondeterministic prompts, DiffLens computes **100% deterministic, reproducible risk scores** using Tree-sitter AST call graphs, test coverage deltas, historical git regression mining, runtime traces, and deployment impact forecasts.

LLMs (Google Gemini or local Ollama via **BYOK**) are used **strictly as an explanation overlay** to translate deterministic findings into plain-English rationales, reviewer checklists, and runnable unit test stubs.

---

## ✨ Key Capabilities

1. **Tree-sitter AST & Blast Radius Engine (`ast_analyzer.py`)**
   - Multi-language support for Python, JavaScript, and TypeScript (`.py`, `.js`, `.jsx`, `.ts`, `.tsx`).
   - Maps diff hunks to enclosed functions, classes, and `<module>` scope, computing upstream caller fan-in ($F_h$) and domain sensitivity (Auth/Session, DB/Financial, API boundaries).
2. **Runtime-Verified Blast Radius (`runtime_tracer.py`)**
   - Merges static AST call graphs with observed runtime execution traces (OpenTelemetry spans, call-edge JSON, or `coverage.py` contexts).
   - Tags every caller as **`runtime_verified`**, **`dynamic_only`**, or **`static_only`**, discounting dead-code callers so they never inflate risk.
3. **Historical Regression Fingerprint Engine (`regression_miner.py`)**
   - Mines git commit history for past bug-fix/revert patterns and detects **Broken Temporal Co-Change Coupling** (flagging PRs that modify file `A` while omitting its historically coupled partner `B`).
4. **Cross-File Change Interaction Engine (`interaction_analyzer.py`)**
   - Compares base vs. head ASTs to detect **Signature Drift** (new required parameters or async flips with un-updated upstream callers) and shared-state contention.
5. **Lightweight Deployment Impact Forecast (`deployment_mapper.py`)**
   - Parses `docker-compose.yml`, `.difflens-services.yaml`, `.github/workflows/*.yml`, and FastAPI/Next.js HTTP routes to forecast affected services, exposed API endpoints, and impact tiers (`Public-API`, `Cross-Service`, `Service-Local`, `Isolated`).
6. **Risk-Weighted Test Selection (`test_selector.py`)**
   - Ranks test files by $risk \times blast\_radius\_weight$ and generates a single runnable CI command with estimated execution time reduction.
7. **Risk Debt Ledger (`/risk-debt`) & Interactive Visual Graphs**
   - Organization-level ledger tracking recurring high-risk hotspots, chronic coverage gaps, PR risk velocity trends, and an interactive **Blast-Radius Call Graph DAG & Service Impact Map**.

---

## 🚀 Quickstart Installation

### Prerequisites
- **Python 3.11+**
- **Node.js 20+** & `npm`
- **Git**
- *(Optional)* **Docker & Docker Compose** (for containerized PostgreSQL 16 + Redis)

---

### Option A: Local Development Setup (Fastest — Zero Docker Required)

DiffLens runs seamlessly out of the box using local SQLite and `fakeredis` fallback for rapid local development.

#### 1. Clone the Repository
```bash
git clone https://github.com/vignesh28102006/difflens.git
cd difflens
```

#### 2. Start the Backend API (FastAPI)
```bash
cd backend
pip install -e ".[dev]"

# Start the API server on port 8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### 3. Seed Realistic Demo Data *(Optional)*
Populate your local dashboard with 4 multi-PR runs featuring signature drift, broken co-change warnings, runtime-verified callers, and risk debt hotspots:
```bash
cd backend
python -m scripts.seed_demo
```

#### 4. Start the Frontend Dashboard (Next.js 15)
Open a second terminal:
```bash
cd frontend
npm install
npm run dev
```

Open **[http://localhost:3000](http://localhost:3000)** in your browser:
- **PR Runs Feed:** `http://localhost:3000/`
- **Change Intelligence & Blast-Radius DAG:** Click any PR run (e.g., `/runs/1`)
- **Risk Debt Ledger:** `http://localhost:3000/risk-debt`
- **Settings & BYOK:** `http://localhost:3000/settings`

---

### Option B: Docker Compose (PostgreSQL 16 + Redis + API + Frontend)

```bash
docker-compose up --build
```

---

## 🔑 Configuring BYOK (Bring Your Own Key)

DiffLens supports **per-repository BYOK** with symmetric Fernet encryption at rest (`AES-128-CBC` + `HMAC-SHA256`). API keys are **never** returned in API responses or logs.

1. *(Production)* Set a persistent encryption key in your environment:
   ```bash
   # Generate a Fernet key:
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

   export DIFFLENS_ENCRYPTION_KEY="your-generated-fernet-key"
   ```
   *(If unset in local dev, DiffLens automatically uses a safe local development fallback key.)*

2. Open **[http://localhost:3000/settings](http://localhost:3000/settings)** in the dashboard.
3. For any connected repository, configure:
   - **Risk Thresholds:** Customize `Medium` (default `40`) and `High` (default `70`) cutoffs.
   - **LLM Provider:**
     - **`gemini`** (Recommended): Enter your Google Gemini API key (e.g. from Google AI Studio) and model (`gemini-2.5-flash`).
     - **`ollama`**: Use a local Ollama instance (`http://localhost:11434`) with models like `llama3.2` or `qwen2.5-coder`—zero cloud calls!
     - **`none`**: Uses the built-in 100% deterministic template explanation fallback with zero API keys.
4. Click **Save Settings**.

---

## 🔗 Connecting Your GitHub Repositories (GitHub App)

To analyze live Pull Requests automatically:

### 1. Create a GitHub App
Go to **GitHub &rarr; Settings &rarr; Developer settings &rarr; GitHub Apps &rarr; New GitHub App**:
- **GitHub App name:** `DiffLens-YourName`
- **Homepage URL:** `https://github.com/vignesh28102006/difflens`
- **Webhook:** Active ✅
- **Webhook URL:**
  - *Production:* `https://your-backend-domain.com/webhooks/github`
  - *Local Dev (via Smee.io):* Generate a free proxy channel at [smee.io/new](https://smee.io/new) and paste `https://smee.io/<your-channel-id>`.
- **Webhook secret:** `development_webhook_secret_key` *(or set your own via `GITHUB_WEBHOOK_SECRET` env var)*
- **Repository Permissions:**
  - **Pull requests:** `Read and write`
  - **Issues:** `Read and write`
  - **Contents:** `Read-only`
  - **Checks:** `Read and write`
- **Subscribe to events:** Check **`Pull request`** ✅

### 2. Install & Forward Local Webhooks
1. Generate a private key and click **Install App** to grant access to your target repositories.
2. Run the Smee forwarder locally to pipe live GitHub webhooks to your machine:
   ```bash
   npx smee-client -u https://smee.io/<your-channel-id> -t http://127.0.0.1:8000/webhooks/github
   ```
3. DiffLens automatically syncs repositories on installation and analyzes every opened/updated PR!

---

## 🧪 Running Tests

All **45 backend unit & integration tests** run completely offline and hermetically using in-memory SQLite and `fakeredis`:

```bash
cd backend
python -m pytest tests -v
```

Verify frontend TypeScript compilation:
```bash
cd frontend
npx tsc --noEmit
```

---

## 📄 License

MIT License
