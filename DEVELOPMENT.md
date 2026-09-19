# Development Guide

## Local Setup

See the [README](README.md) for full setup instructions. Quick version:

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -e ".[dev]"
cp .env.example .env
createdb candle
cd frontend && npm install && cd ..
python manage.py migrate
cd frontend && npm run build && cd ..
python manage.py runserver
```

## Code Quality

### Linting

```bash
ruff check .
```

### Formatting

```bash
ruff format .
```

### Import Sorting

```bash
ruff check --select I .
```

Or as part of the full lint pass (isort rules are included in the default `ruff check` configuration).

### Type Checking

```bash
mypy .
```

Mypy is configured in strict mode via `pyproject.toml` with the `django-stubs` plugin. The Django settings module for type checking is `config.settings.development`.

## Testing

### Running Tests

```bash
# pytest (preferred, configured in pyproject.toml)
pytest

# Django test runner
python manage.py test
```

Pytest is configured with:

- `DJANGO_SETTINGS_MODULE = "config.settings.development"`
- `asyncio_mode = "auto"` for async test support
- Short tracebacks by default

### Test Structure

```
tests/
├── unit/           # Unit tests
└── integration/    # Integration tests
```

## Frontend

### Building

```bash
cd frontend
npm run build      # TypeScript compilation + Vite production build
```

Output is written to `static/dist/`.

### Development Server

```bash
cd frontend
npm run dev        # Vite dev server with hot module replacement
```

The Vite dev server provides hot reload for CSS and TypeScript changes during development.

### Preview Production Build

```bash
cd frontend
npm run preview    # Serve the production build locally
```

## Database Migrations

```bash
# Generate migrations after model changes
python manage.py makemigrations

# Apply pending migrations
python manage.py migrate
```

## Environment Configuration

Copy `.env.example` to `.env` and update the values for your local environment. Key variables:

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key (required, random in production) |
| `DJANGO_DEBUG` | `true` to enable debug mode |
| `POSTGRES_DB` | PostgreSQL database name |
| `POSTGRES_USER` | PostgreSQL user |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_HOST` | PostgreSQL host |
| `POSTGRES_PORT` | PostgreSQL port |
| `REDIS_URL` | Redis connection URL |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hostnames |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated CSRF-trusted origins |
| `DJANGO_EMAIL_BACKEND` | Email backend class (console in dev, SMTP in production) |

The settings module is controlled by `DJANGO_SETTINGS_MODULE`, defaulting to `config.settings.development`.

## Git Workflow

### Branching

- Create feature branches from `main` for all work
- Use descriptive branch names (e.g., `feature/whiteboard-canvas`, `fix/csp-nonce-ordering`)
- Merge via pull request after review

### Pre-commit

Ruff (lint + format) and mypy run automatically on `git commit` via
[pre-commit](https://pre-commit.com/), configured in `.pre-commit-config.yaml`.
One-time setup after installing the `dev` extra:

```bash
pre-commit install
```

To run the same checks manually (e.g. against the whole tree, or before the
hook is installed):

```bash
ruff check .
ruff format .
mypy .
pytest
```

## Running with Daphne

Daphne is the ASGI server required for WebSocket support. It is listed as a dependency and installed as an installed app:

```bash
daphne config.asgi:application
```

In development, `python manage.py runserver` automatically uses Daphne when it is installed, since `daphne` is listed before `django.contrib.admin` in `INSTALLED_APPS`.

## Logging

Development logs verbosely to the console at DEBUG level for application code (`apps`), with Django-framework loggers (including `django.db`) at INFO level to avoid noisy per-query SQL. Production logs at INFO level. The logging configuration is in `config/settings/base.py`.
