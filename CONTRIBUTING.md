# Contributing to Mindwall

Thank you for your interest in contributing to Mindwall. This document describes the contribution process and standards.

---

## License

Mindwall is released under the **PolyForm Noncommercial 1.0.0** license. By contributing, you agree that your contributions will be licensed under the same terms. **Commercial use of any kind requires explicit written permission from the copyright holder.**

If your intended use is commercial, please contact the maintainers before contributing.

---

## Before you start

1. Read [architecture.md](architecture.md) to understand the product design and hard constraints.
2. Read the [security policy](SECURITY.md) — security-relevant changes have additional requirements.
3. Check the [roadmap](docs/roadmap.md) to see what is planned and where contributions are most valuable.

---

## Development setup

See [docs/installation/local.md](docs/installation/local.md) for the full setup guide.

Quick summary:

```bash
git clone <repo-url>
cd mindwall
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
cp .env.example .env
# edit .env with SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL, REDIS_URL
alembic upgrade head
python scripts/create_admin.py
uvicorn app.main:app --reload
```

---

## Contribution standards

### Code quality

All contributions must pass:

```bash
ruff check .       # linting
ruff format .      # formatting
pytest             # full test suite
```

Fix all linting errors before submitting. Do not use `# noqa` to suppress warnings without a clear comment explaining why.

### Tests

- Every new behavior should have a test.
- Every bug fix should include a regression test.
- Do not remove or weaken existing tests to make a build pass.
- Tests should not require external network access (use mocks for Ollama, upstream IMAP/SMTP).

### Security

- All security-relevant changes (auth, credential handling, proxy authentication, quarantine access) require explicit rationale in the PR description.
- Do not weaken security to simplify a feature.
- Follow the non-negotiable constraints in `.github/copilot-instructions.md`.

### Documentation

- Update `docs/` for any behavior, config, or API change.
- Update `CHANGELOG.md` if the change is user-visible.
- Update `architecture.md` for significant structural changes.

---

## What to contribute

High-value contribution areas:

| Area | Description |
|------|-------------|
| STARTTLS | TLS upgrade support for IMAP and SMTP proxies |
| Background sync | Automatic periodic sync worker |
| Prometheus metrics | `/metrics` endpoint |
| Deterministic checks | Additional phishing detection rules |
| Integration tests | Sandbox-based end-to-end tests |
| Documentation | Corrections, clarifications, examples |

---

## What not to contribute

The following changes will not be accepted:

- Adding cloud AI APIs or any external data transmission of message content
- Replacing local inference with hosted inference
- Introducing React, Next.js, Vue, Angular, or a Node-based frontend build pipeline
- Adding new infrastructure (Kafka, Elasticsearch, RabbitMQ) without prior discussion
- Removing or weakening security checks, credential validation, or RBAC enforcement
- Breaking changes to the public URL structure without migration notes

---

## Pull request process

1. Fork the repository and create a feature branch.
2. Make your changes with appropriate tests and documentation.
3. Run `ruff check .` and `pytest` — both must pass cleanly.
4. Open a pull request with a clear description of what was changed and why.
5. Reference any related issues or roadmap items.
6. Be prepared to iterate based on review feedback.

---

## Reporting bugs

Open a GitHub issue with:
- A clear description of the bug
- Steps to reproduce
- Expected vs. actual behavior
- Relevant log output (redact any credentials or sensitive data)

For security vulnerabilities, see [SECURITY.md](SECURITY.md) — **do not open a public issue**.
