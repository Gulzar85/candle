# Candle

A private, collaborative whiteboard for two partners to draw, plan, and create together.

**Current status: Phase 7.** The whiteboard is a real-time, offline-first,
premium product surface: server-persisted operations, live collaboration over
WebSockets, an offline-durable sync engine with a PWA shell, and a polished
UI (toolbar, mobile gestures, keyboard shortcuts, accessible shell, light/dark
theme) built on top of the deterministic canvas engine from Phase 3.

### Feature status

- ✅ Phase 0 — Django 6.1 app, PostgreSQL, Redis, Channels/ASGI, Vite + Tailwind
  v4 + HTMX + Alpine + Lucide, security baseline (CSP nonces, HTTPS/HSTS).
- ✅ Phase 1 — Email-based accounts: registration, verification, login, password
  reset/change, rate limiting, transactional emails, session rotation.
- ✅ Phase 2 — Partnerships & invitations.
- ✅ Phase 3 — Local whiteboard engine (pen/eraser/select, undo/redo, zoom/pan,
  fit) + server-authorized domain boundary.
- ✅ Phase 4 — Server-persisted, versioned, idempotent operation ledger
  (create/delete/clear) with deterministic state reconstruction.
- ✅ Phase 5 — Real-time collaboration over Django Channels: live operation
  sync and ephemeral partner presence/cursors.
- ✅ Phase 6 — Offline-first IndexedDB durability, a sync engine with retry/
  conflict handling, and a PWA shell (service worker, manifest, installable).
- ✅ Phase 7 — Product polish: dashboard as a real home, whiteboard rename/
  archive, selection + delete, a stroke-style popover (color/width/opacity),
  pinch-zoom/two-finger pan, safe-area-aware mobile layout, keyboard shortcuts
  with a help dialog, throttled presence, a consolidated light/dark theme, and
  an accessible app shell. See `docs/phase-7-completion-report.md`.
- ⏳ Future — Text/shape objects (needs a versioned ledger extension), CI/CD
  and deployment hardening (see `docs/architecture/phase-8-production-audit.md`).

## Tech Stack

### Backend

- **Django 6.1** (latest stable)
- **Python 3.13**
- **PostgreSQL 18** (primary database via psycopg3)
- **Django Channels 4.3+** (ASGI foundation for future WebSocket support)
- **Redis** (channel layer backend + cache)
- **Django REST Framework 3.18**
- **WhiteNoise** (static file serving with compression)
- **Daphne** (ASGI server)

For a WSGI deployment using SQLite on PythonAnywhere, see
[`docs/deployment/pythonanywhere.md`](docs/deployment/pythonanywhere.md).

### Frontend

- **HTMX 2.0** (partial page updates)
- **Alpine.js 3** (client-side interactivity)
- **Tailwind CSS v4** (utility-first styling)
- **TypeScript 5.8**
- **Vite 6.4** (build tooling)
- **Lucide Icons**

HTMX, Alpine.js, and Lucide are bundled by Vite and served from `'self'` (no external CDN).

## Prerequisites

- Python 3.13
- PostgreSQL 18
- Redis
- Node.js 22+
- npm

## Setup

1. Clone the repository:

```bash
git clone <repository-url>
cd Candle
```

2. Create and activate a virtual environment:

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

3. Install Python dependencies (including dev tools):

```bash
pip install -e ".[dev]"
```

4. Copy the example environment file and update values:

```bash
cp .env.example .env
# Edit .env with your database credentials and secret key
```

5. Create the PostgreSQL database:

```bash
createdb candle
```

6. Install frontend dependencies:

```bash
cd frontend
npm install
```

7. Run database migrations:

```bash
python manage.py migrate
```

8. Build frontend assets:

```bash
cd frontend
npm run build
```

9. Run the development server:

```bash
python manage.py runserver
```

The application will be available at `http://localhost:8000`.

## Environment Variables

All configuration is managed through environment variables. Copy `.env.example` to `.env` and update as needed.

| Variable | Description | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django secret key for cryptographic signing (required in production) | `change-me-in-production` |
| `DJANGO_DEBUG` | Enable debug mode | `false` |
| `POSTGRES_DB` | PostgreSQL database name | `candle` |
| `POSTGRES_USER` | PostgreSQL database user | `candle` |
| `POSTGRES_PASSWORD` | PostgreSQL database password | (empty) |
| `POSTGRES_HOST` | PostgreSQL host | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of allowed hostnames | `localhost,127.0.0.1` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated CSRF-trusted origins | (empty) |
| `DJANGO_EMAIL_BACKEND` | Email backend class (console in dev, SMTP in production) | `django.core.mail.backends.console.EmailBackend` |

Email transport is configured via the `MAILERS` setting in `config/settings/base.py` (env-driven: `DJANGO_EMAIL_HOST`, `DJANGO_EMAIL_PORT`, `DJANGO_EMAIL_HOST_USER`, `DJANGO_EMAIL_HOST_PASSWORD`, `DJANGO_EMAIL_USE_TLS`, `DJANGO_EMAIL_TIMEOUT`). Production overrides to use SMTP by setting `DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`. HTTPS, HSTS, and secure-cookie settings are hardcoded in `production.py` and applied only when `DJANGO_SETTINGS_MODULE=config.settings.production`.

## Running the Development Server

```bash
python manage.py runserver
```

For ASGI (required for WebSocket support in future phases):

```bash
daphne config.asgi:application
```

## Running Tests

```bash
# Using pytest (preferred)
pytest

# Using Django's test runner
python manage.py test
```

## Building Static Assets

The frontend is built with Vite and outputs to `static/dist/`.

```bash
cd frontend
npm run build      # Production build
npm run dev        # Vite dev server with hot reload
```

## PostgreSQL Setup

Ensure PostgreSQL is running and accessible. Create the database and user:

```sql
CREATE USER candle WITH PASSWORD 'candle';
CREATE DATABASE candle OWNER candle;
```

Update `POSTGRES_*` variables in `.env` if your credentials differ.

## Redis Setup

Ensure Redis is running on `localhost:6379` (or update `REDIS_URL` in `.env`). Redis is used for:

- **Channel layer** (Django Channels, for future real-time features)
- **Cache backend** (in development; production uses Redis directly)

## Project Structure

```
Candle/
├── apps/
│   ├── accounts/            # Email-based users, auth, profiles, rate limiting
│   ├── core/                # Core application (home page, emailing, shared utilities)
│   ├── partnerships/        # Partnerships & invitations (Phase 2)
│   └── whiteboard/          # Whiteboard domain boundary (Phase 3)
├── config/
│   ├── settings/
│   │   ├── base.py         # Shared settings
│   │   ├── development.py  # Development overrides
│   │   └── production.py   # Production overrides + security hardening
│   ├── asgi.py             # ASGI application with Channels routing
│   ├── urls.py             # Root URL configuration
│   └── wsgi.py             # WSGI fallback
├── docs/
│   ├── architecture/       # Architecture + audit documents
│   ├── design/             # Design system (Phase 7)
│   ├── product/            # Product/UX docs: whiteboard, mobile, sync, shortcuts
│   ├── security/           # Security threat models
│   └── testing/            # Test matrices (Phase 7)
├── frontend/
│   ├── src/                # TypeScript + Tailwind CSS source
│   ├── vite.config.ts      # Vite build configuration
│   └── package.json        # Frontend dependencies
├── media/                  # User-uploaded files
├── static/
│   ├── dist/               # Vite build output
│   ├── icons/              # PWA icons and favicon
│   └── manifest.json       # PWA web manifest
├── templates/
│   ├── base.html           # Base template (CSP, HTMX, Alpine.js)
│   ├── components/         # Reusable template components
│   ├── emails/             # Transactional email templates (html + text)
│   ├── errors/             # Error page templates
│   ├── layouts/            # Page layout templates
│   ├── partnerships/       # Partnership/invitation templates (Phase 2)
│   ├── whiteboard/         # Whiteboard page template (Phase 3)
│   └── pages/              # Page-specific templates
├── tests/
│   ├── accounts/           # Phase 1 account tests
│   ├── partnerships/       # Phase 2 partnership/invitation tests
│   ├── whiteboard/         # Phase 3 whiteboard authorization tests
│   ├── integration/        # Integration tests
│   └── unit/               # Unit tests
├── scripts/                # Utility scripts
├── manage.py
└── pyproject.toml          # Project metadata and tool configuration
```
