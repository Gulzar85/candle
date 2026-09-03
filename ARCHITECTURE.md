# Architecture

## Application Architecture

Candle is a Django monolith with app-level separation, an HTMX-driven UI, and an ASGI-first server configuration. The application is designed as a single-process Django deployment with Django Channels providing the foundation for future WebSocket-based real-time collaboration.

The current phase (Phase 0) delivers a working Django application with database, cache, channel layer, frontend build tooling, and security configuration. No whiteboard or real-time functionality is implemented yet.

## Django Structure

```
config/          Project configuration (settings, URLs, ASGI/WSGI entry points)
apps/            Application modules
templates/       Django HTML templates
static/          Static deployable assets (icons, PWA manifest, Vite build output)
frontend/        Vite/TypeScript source (builds into static/dist/)
tests/           Test suite (unit and integration)
media/           User-uploaded files
```

Application code lives under `apps/`. Each Django app within this directory handles a specific domain concern. The `config/` directory contains project-level settings, URL routing, and ASGI/WSGI configuration.

## ASGI Configuration

The ASGI entry point is `config/asgi.py`. It uses `ProtocolTypeRouter` from Django Channels to handle both HTTP and WebSocket protocols:

```python
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # WebSocket routing will be added in Phase 1+
    }
)
```

All HTTP requests pass through Django's standard middleware stack. WebSocket routing is stubbed out and will be implemented when real-time collaboration is added. The project uses Daphne as the ASGI server.

## Django Channels Foundation

Django Channels is installed and configured as an installed app. The channel layer uses Redis as its backend:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    },
}
```

No WebSocket consumers or routes are implemented yet. The channel layer is available for use in Phase 1+ when real-time features are added.

## Redis

Redis serves two roles:

1. **Channel layer backend** -- Used by Django Channels for inter-process communication and future WebSocket message routing.
2. **Cache backend** -- The default cache backend in production. In development, `LocMemCache` is used instead for simplicity.

Redis is not used as a primary data store. All persistent data lives in PostgreSQL.

## PostgreSQL

PostgreSQL is the primary database, accessed via `psycopg3` (`psycopg[binary]`). Connection pooling is configured in production with `CONN_MAX_AGE = 600`.

All application models, user data, and business state are stored in PostgreSQL.

## Frontend Architecture

### Build System

The frontend uses **Vite 6.4** as the build tool. Source TypeScript lives in `frontend/src/` and builds to `static/dist/`. The Vite configuration produces a manifest file for Django integration:

- Entry point: `frontend/src/main.ts`
- Output: `static/dist/assets/`
- Manifest: `static/dist/.vite/manifest.json`

### Styling

**Tailwind CSS v4** is configured through the Vite plugin. Styling uses semantic design tokens defined in CSS custom properties, supporting both light and dark modes. The dark mode toggle reads from `localStorage` and respects the system `prefers-color-scheme` preference.

### Client-Side Libraries

- **HTMX 2.0** -- Drives partial page updates via HTML attributes. All server communication for UI updates goes through HTMX, eliminating the need for client-side routing.
- **Alpine.js 3** -- Handles client-side interactivity (dropdowns, modals, toggles) that does not require server round-trips.
- **Lucide Icons** -- SVG icon library. Icons are re-initialized after HTMX swaps via the `htmx:afterSwap` event.

### Bundled via Vite

HTMX, Alpine.js, and Lucide are bundled by Vite from `frontend/src/main.ts` and served from `'self'` (no external CDN). This keeps all third‑party code self‑hosted, which is required by the strict Content Security Policy (only `'self'` sources plus per‑request nonces are allowed). Lucide icons are re‑initialized after HTMX swaps via the `htmx:afterSwap` event.

## PWA Strategy

A web app manifest is configured at `static/manifest.json` with icons for 192px and 512px sizes. The app supports `standalone` display mode. No service worker or offline sync is implemented yet. The manifest provides the foundation for future PWA capabilities.

## Future Whiteboard Architecture

The whiteboard will be the primary interface of the application. Key architectural decisions for implementation:

- **Full viewport** -- The whiteboard will occupy the entire viewport, replacing the current page-based layout.
- **WebSocket collaboration** -- Real-time drawing sync between partners will use Django Channels WebSocket consumers, routed through the `ProtocolTypeRouter` in `config/asgi.py`.
- **Redis channel layer** -- The already-configured Redis channel layer will handle message broadcasting between connected clients.
- **Canvas-based rendering** -- The whiteboard will use an HTML5 Canvas element with TypeScript for drawing logic and state management.

These features are planned for Phase 1 and beyond.

## Security Boundaries

### Content Security Policy

CSP is enforced via `django.middleware.csp.ContentSecurityPolicyMiddleware` with nonce-based script and style allowlisting. The CSP policy restricts:

- `script-src` and `style-src` to `'self'` with per-request nonces
- `frame-ancestors` to `'none'` (no embedding)
- `object-src` to `'none'`
- `connect-src` includes `ws:` and `wss:` for future WebSocket connections

CSP is disabled in development for convenience.

### Transport Security

Production enforces:

- HTTPS redirect (`SECURE_SSL_REDIRECT`)
- HSTS with 1-year max age, including subdomains and preload
- Secure cookies (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`)
- `X-Frame-Options: DENY`
- Content type nosniff

### Secrets Management

No secrets or keys are committed to source control. All sensitive values are loaded from environment variables. The `.env.example` file provides placeholder values. Production raises `ImproperlyConfigured` if the default secret key is detected.

## Settings Hierarchy

Settings follow a layered pattern:

```
config/settings/
├── base.py          # Shared configuration (database, apps, middleware, CSP, etc.)
├── development.py   # Debug mode, permissive allowed hosts, local cache, verbose logging
└── production.py    # Debug off, security hardening, strict secret key validation
```

`development.py` and `production.py` both import from `base.py` and override as needed. The active settings module is set via the `DJANGO_SETTINGS_MODULE` environment variable (defaults to `config.settings.development`).

## Design System

### Semantic Tokens

Colors and spacing are defined as CSS custom properties in the Tailwind configuration, referenced by semantic names rather than raw values. This allows consistent theming across light and dark modes.

### Dark Mode

Dark mode is supported via Tailwind's `dark:` variant. The initial theme is set by an inline script in `base.html` that reads from `localStorage` and falls back to the system `prefers-color-scheme` preference.

### Responsive Breakpoints

The layout uses Tailwind's default responsive breakpoints (`sm`, `md`, `lg`, `xl`, `2xl`). The base template includes `viewport-fit=cover` for notched device support.
