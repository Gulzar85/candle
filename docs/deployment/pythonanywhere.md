# PythonAnywhere deployment

This project can run on a PythonAnywhere WSGI web app with SQLite and
process-local cache/channel backends. The production site configured for this
repository is `https://cand.pythonanywhere.com`.

PythonAnywhere's standard WSGI web app does not provide a shared ASGI channel
layer. The whiteboard HTTP application works, but cross-process WebSocket
collaboration is not available with the SQLite/locmem profile. Use an ASGI
host with Redis for that feature.

## Initial setup

Run these commands in a PythonAnywhere Bash console, replacing the repository
path if the web app uses a different home directory:

```bash
cd ~
git clone https://github.com/Gulzar85/candle.git candle
cd ~/candle
python3.13 -m venv ~/.virtualenvs/candle
source ~/.virtualenvs/candle/bin/activate
pip install -e .
mkdir -p ~/candle/data
```

Create `~/candle/.env` with the following values. Generate a new secret key
for each deployment; do not copy the example key:

```dotenv
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=replace-with-a-random-secret-at-least-32-characters
DJANGO_DEBUG=false

DJANGO_DATABASE_ENGINE=sqlite
SQLITE_NAME=/home/cand/candle/data/db.sqlite3
DJANGO_CACHE_BACKEND=locmem
DJANGO_CHANNEL_LAYER_BACKEND=inmemory

DJANGO_ALLOWED_HOSTS=cand.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://cand.pythonanywhere.com
WEBSOCKET_ALLOWED_ORIGINS=https://cand.pythonanywhere.com
DJANGO_SITE_URL=https://cand.pythonanywhere.com

DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=smtp.gmail.com
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_HOST_USER=gulzar.ufone@gmail.com
DJANGO_EMAIL_HOST_PASSWORD=replace-with-a-16-character-gmail-app-password
DJANGO_EMAIL_USE_TLS=true
DJANGO_EMAIL_TIMEOUT=10
```

Set `SQLITE_NAME` to the actual absolute path if the PythonAnywhere username
or checkout directory differs from `cand/candle`.

For Gmail, enable 2-Step Verification and create an app password at
<https://myaccount.google.com/apppasswords>. Use that 16-character app
password as `DJANGO_EMAIL_HOST_PASSWORD`; never use or commit your normal
Google account password.

## Database and static files

```bash
source ~/.virtualenvs/candle/bin/activate
cd ~/candle
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check --deploy
```

In the PythonAnywhere **Web** tab:

1. Set the virtualenv to `/home/cand/.virtualenvs/candle`.
2. Set the source code and working directory to `/home/cand/candle`.
3. Set the WSGI file to the checked-in [`pythonanywhere_wsgi.py`](../../pythonanywhere_wsgi.py).
4. Add a static mapping from `/static/` to `/home/cand/candle/staticfiles/`.
5. Add a media mapping from `/media/` to `/home/cand/candle/media/` so uploaded
	avatars are served by PythonAnywhere's web server.
6. Reload the web app.

The WSGI entry point loads `.env` through Django's settings bootstrap and
serves the application with the production settings.

## Updating the deployment

```bash
cd ~/candle
git pull --ff-only origin main
source ~/.virtualenvs/candle/bin/activate
pip install -e .
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Reload the web app from the PythonAnywhere Web tab after updating code.
