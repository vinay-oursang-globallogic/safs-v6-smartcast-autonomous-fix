# SAFS v6.0 — SmartCast Autonomous Fix System

## 🎯 Overview

SAFS (SmartCast Autonomous Fix System) v6.0 is an AI-powered autonomous bug-fixing system for Vizio SmartCast TVs. It automatically analyzes JIRA tickets, reproduces bugs on dev TVs, generates fixes across the three-layer SmartCast stack (MediaTek SoC, LOKi C++, HTML5 Apps), validates fixes via tri-path validation (QEMU + Playwright + on-device), and creates draft PRs.

**Key Features:**
- ✅ **POC-Integrated**: Built on 4 battle-tested POC projects (~40% effort reduction)
- ✅ **Tri-Path Validation**: QEMU (α) + Playwright (β) + On-Device (γ) validation
- ✅ **Multi-Host Retrieval**: GitHub, GitLab, Bitbucket via RepositoryAdapter
- ✅ **On-Device Testing**: Real TV validation via vizio-mcp servers
- ✅ **Bug Reproduction**: Pre-fix reproduction with evidence capture
- ✅ **100+ Error Patterns**: Enriched pattern library from POC
- ✅ **3-Candidate Tournament**: SURGICAL, DEFENSIVE, REFACTORED fix strategies
- ✅ **LangGraph Orchestration**: Multi-stage pipeline with BugLayer routing
- ✅ **Qdrant Institutional Memory**: Historical fixes + known mistakes

---

## 📁 Project Structure

```
Automated_jira_log_analyser/
├── src/safs/
│   ├── intake/              # Jira webhook + attachment handling (ported from POC)
│   ├── log_analysis/        # Quality gate, BugLayerRouter, patterns (ported from POC)
│   ├── symbolication/       # ASLR, ELF, CDP parsing (built new)
│   ├── retrieval/           # RetrievalRouter, RepositoryAdapter, rate limiter
│   ├── context/             # TF-IDF, MinHash, context assembly (ported from POC)
│   ├── validation/          # QEMU, Playwright, on-device validators
│   ├── agents/              # Orchestrator, fix generator, PR creator
│   ├── qdrant_collections/  # Qdrant setup + indexers
│   ├── symbol_store/        # MinIO/S3 for debug symbols
│   ├── telemetry/           # Proactive monitoring, regression correlation
│   └── companion_lib/       # Companion version resolution
├── external_mcp/            # Vizio MCP servers (from POC - used as-is)
│   ├── vizio-remote/
│   ├── vizio-ssh/
│   └── vizio-loki/
├── tests/                   # Comprehensive test suite
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── infra/                   # Kubernetes manifests
│   └── kubernetes/
├── toolchain/               # ARM cross-compilation configs
├── pyproject.toml           # Dependencies & tool config
├── .env.example             # Environment variables template
└── README.md                # This file
```

---

## 🚀 Phase 0: Quick Start

### Prerequisites

- **Python**: 3.10+ (3.11 recommended)
- **Operating System**: macOS or Linux (for ARM toolchain)
- **Docker**: For Kubernetes deployment (optional for local dev)
- **Git**: For repository operations
- **SSH Access**: To Vizio dev TV (for on-device validation)

### 1. Clone & Setup

```bash
# Navigate to project
cd Automated_jira_log_analyser

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\\Scripts\\activate

# Install dependencies
pip install --upgrade pip
pip install -e ".[dev]"

# Copy environment template
cp .env.example .env
# Edit .env with your actual credentials
```

### 2. Configure Environment

Edit `.env` and set **at minimum**:

```bash
# === REQUIRED ===
ANTHROPIC_API_KEY=sk-ant-api03-...       # Claude API key
JIRA_URL=https://vizio.atlassian.net     # Your Jira instance
JIRA_API_TOKEN=ATATT3xFfGF0...           # Jira API token
GITHUB_TOKEN=ghp_...                     # GitHub personal access token
VOYAGE_API_KEY=pa-...                    # Voyage AI for embeddings

# === OPTIONAL (for on-device validation) ===
VIZIO_TV_IP=192.168.1.100                # Dev TV IP
VIZIO_TV_SSH_PASSWORD=vizio123           # SSH password
VIZIO_SCPL_AUTH_TOKEN=Zmh4a2pzZ...       # SCPL pairing token
```

### 3. Install External Dependencies

```bash
# Playwright (for PATH β validation)
playwright install chromium

# NLTK data (for keyword extraction)
python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"

# QEMU (for PATH α validation - macOS)
brew install qemu

# ARM toolchains (for cross-compilation)
# Download from MediaTek SDK and set paths in .env
```

### 4. Initialize Database & Services

```bash
# Start local services (PostgreSQL, Redis, Qdrant)
docker-compose up -d

# Initialize Qdrant collections
python -m safs.qdrant_collections.collection_setup

# Run database migrations
python -m safs.db.migrations
```

### 5. Run Tests

```bash
# Unit tests
pytest tests/unit -v

# Integration tests (requires services)
pytest tests/integration -v

# End-to-end tests (requires dev TV)
pytest tests/e2e -m requires_tv -v
```

### 6. Start SAFS Pipeline

```bash
# Run in development mode
safs run --environment development

# Or with Temporal.io orchestration
safs run --orchestrator temporal
```

---

## 🔧 Development Workflow

### Phase 0: Foundation (Current Phase) ✅
- [x] Project structure
- [x] Dependency management (`pyproject.toml`)
- [x] Configuration files (`.env.example`)
- [x] README & setup instructions

### Phase 1: Pydantic Data Models (Next)
- [ ] Port `interfaces.py` from POC
- [ ] Define BugLayer, ErrorCategory enums
- [ ] PipelineState, FixCandidate, ValidationResult models
- [ ] Test: Model serialization & validation

### Phase 2-23: Remaining Phases
See `SAFS_v6_Complete_Master_Prompt.md` Part Seven for complete phase breakdown.

---

## 📖 Documentation

- **Master Prompt**: `../SAFS_v6_Complete_Master_Prompt.md` - Complete specification
- **Architecture Review**: `../SAFS_v6_Architecture_Review.md` - Architecture deep-dive
- **POC Integration**: See Master Prompt Part Twelve

---

## 🧪 Testing Strategy

### Test Categories

1. **Unit Tests** (`tests/unit/`)
   - Test individual functions/classes in isolation
   - Mock external dependencies (LLM, Jira, GitHub)
   - Fast (<1s per test)

2. **Integration Tests** (`tests/integration/`)
   - Test component interactions
   - Use Docker services (PostgreSQL, Redis, Qdrant)
   - Medium speed (1-10s per test)

3. **End-to-End Tests** (`tests/e2e/`)
   - Test complete pipeline scenarios
   - Use fixtures (log files, crash dumps)
   - Slow (10s-5min per test)
   - Some require physical TV (`@pytest.mark.requires_tv`)

### Running Tests

```bash
# All tests
pytest

# Specific category
pytest tests/unit -v

# Exclude slow tests
pytest -m "not slow"

# Exclude tests requiring LLM API
pytest -m "not requires_llm"

# Exclude tests requiring TV
pytest -m "not requires_tv"

# With coverage
pytest --cov=src/safs --cov-report=html
```

---

## 🏗️ Architecture Highlights

### Three-Layer Vizio SmartCast Stack

1. **Layer 1: MediaTek SoC + Linux** (auto-escalate, no fixes)
2. **Layer 2: LOKi C++ Application** (QEMU + on-device validation)
3. **Layer 3: HTML5 Streaming Apps** (Playwright + on-device validation)

### Pipeline Stages

```
Stage -1: Log Quality Gate → Stage 0: BugLayerRouter
  → Stage 1-2: Log Parsing + Symbolication
  → Stage 3: Root Cause Analysis
  → Stage 4: Repo Locator (RetrievalRouter)
  → Stage 5: Context Builder
  → Stage 5.5: Bug Reproduction (NEW in v6.0)
  → Stage 6: Fix Generation (3-candidate tournament)
  → Stage 7: Tri-Path Validation (α/β/γ)
  → Stage 7.5: Confidence Ensemble
  → Stage 8: PR Creation (DRAFT only)
```

### Four-Path Retrieval

- **PATH A**: GitHub MCP / RepositoryAdapter (exact file reads, writes)
- **PATH B**: Code-Index-MCP (semantic search, AST symbols)
- **PATH C**: Qdrant (historical fixes, known mistakes)
- **PATH D**: On-Device Registry (firmware version, LOKi version)

### Tri-Path Validation

- **PATH α (QEMU)**: Fast LOKi C++ validation (~30s, ASan/TSan)
- **PATH β (Playwright)**: Fast HTML5 validation (~45s, headless)
- **PATH γ (On-Device)**: Ground-truth TV validation (~2-5min, highest confidence)

---

## 🔑 Key Non-Negotiable Rules

1. **PIPELINE ORDER**: ALWAYS Stage -1 → Stage 0 → layer routing
2. **ASLR SYMBOLICATION**: ALWAYS extract `/proc/pid/maps` for address correction
3. **THREE-PATH+ RETRIEVAL**: ALL retrieval via RetrievalRouter (A/B/C/D)
4. **FIX CANDIDATES**: ALWAYS generate 3 candidates (SURGICAL/DEFENSIVE/REFACTORED)
5. **DRAFT PRs ONLY**: NEVER direct merge, always draft PR
6. **CROSS_LAYER PRs**: CROSS_LAYER bugs → TWO PRs (LOKi + app repos)
7. **QDRANT SACRED**: Historical fixes/corrections NEVER deleted
8. **PRODUCTION REGRESSION**: 72h post-merge monitoring, spike ≥1.5x → self-healing

---

## 🐛 Troubleshooting

### Common Issues

**Issue**: `ImportError: No module named 'safs'`
```bash
# Solution: Install in editable mode
pip install -e .
```

**Issue**: `Playwright browser not found`
```bash
# Solution: Install browsers
playwright install chromium
```

**Issue**: `NLTK data not found`
```bash
# Solution: Download NLTK data
python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"
```

**Issue**: `Cannot connect to Qdrant`
```bash
# Solution: Start Qdrant via Docker
docker run -p 6333:6333 qdrant/qdrant
```

**Issue**: `SSH connection to TV failed`
```bash
# Solution: Check TV IP, ensure SSH enabled
ping $VIZIO_TV_IP
ssh root@$VIZIO_TV_IP  # Should prompt for password
```

---

## 📊 Monitoring & Observability

### LangFuse Integration

All LLM calls are traced in LangFuse:
- Model usage (Opus/Haiku/Sonnet)
- Token consumption & cost
- Latency per stage
- Success/failure rates

Access at: `https://cloud.langfuse.com` (set `LANGFUSE_PUBLIC_KEY` in `.env`)

### Prometheus Metrics

Exported metrics:
- `safs_pipeline_duration_seconds{stage}`
- `safs_fix_candidates_generated_total`
- `safs_validation_pass_rate{path}`
- `safs_confidence_score_avg`
- `safs_pr_created_total{layer}`

---

## 🤝 Contributing

### Code Quality

All code must pass:
```bash
# Formatting
black src/ tests/

# Linting
ruff check src/ tests/

# Type checking
mypy src/

# Security scan
bandit -r src/
```

### Commit Convention

```
feat(stage-6): implement 3-candidate fix tournament
fix(log-analysis): handle missing timestamp in kernel logs
docs(readme): add troubleshooting section
test(validation): add on-device validator tests
```


