# DiffLens Agent Guardrails & Architectural Principles

## 1. Deterministic Core First, LLM Second
- **`scorer.py` and `ast_analyzer.py` must remain 100% deterministic.**
  - Zero external API keys or remote LLM calls are permitted in scoring or AST parsing.
  - Every calculation must be pure, reproducible, and verifiable via standalone unit tests.
- **Role of the LLM (`explainer.py`):**
  - The LLM explains findings and generates test stubs based on the deterministic output.
  - The LLM never invents or overrides the numerical risk scores.

## 2. Multi-file Edits & Planning
- Use `/plan` (or an explicit task breakdown) before touching multiple files across backend and frontend.
- Respect separation of concerns:
  - `backend/app/main.py`: Ingress webhook handling, auth validation, job enqueueing.
  - `backend/app/scorer.py`: Pure scoring formulas and hunk aggregation.
  - `backend/app/ast_analyzer.py`: Tree-sitter parsing and symbol blast radius.
  - `backend/app/explainer.py`: LLM synthesis and test recommendations.

## 3. Quota & Efficiency
- Batch clear, scoped tasks rather than initiating unconstrained full-codebase rewrites.
- Test changes locally with quick, targeted unit tests before expanding scope.
