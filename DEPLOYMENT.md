# Railway deployment

## Required Railway setup

1. Add a Railway PostgreSQL service and make its connection URL available to the
   web service as `DATABASE_URL`. The app deliberately refuses to start on
   Railway without this variable because the local SQLite file is not durable
   across deployments.
2. Add these variables to the web service:
   - `SECRET_KEY`: a randomly generated value of at least 32 characters. Keep
     the same value between deployments so users' sessions remain valid.
   - `ADMIN_USERNAME`: the owner account username. For an existing database,
     this value renames the primary owner account at startup.
   - `ADMIN_PASSWORD`: a strong password of at least 12 characters. The
     primary owner's password is synchronized from this value at startup.
3. Optionally configure `PUSH_VAPID_PUBLIC_KEY`, `PUSH_VAPID_PRIVATE_KEY`, and
   `PUSH_VAPID_SUBJECT` for Web Push notifications.
4. Set the Railway start command to:

   ```sh
   gunicorn --preload --workers 2 --bind 0.0.0.0:$PORT app:app
   ```

Railway supplies `PORT` and terminates public HTTPS. Keep all secrets in
Railway's Variables, never in source control or application logs. Do not
commit `.env` or the local `sme.db`; both are ignored by `.gitignore`.

## Existing database and operational notes

Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` before deploying. On startup, the
app updates the primary owner username/password to those values and migrates
legacy plaintext user passwords to password hashes. Ensure that any existing
owner account is the primary `super_admin` account before deploying.
If an earlier version has run in a shared environment, rotate its session key
and administrator password; older startup logs may contain the initial
administrator credentials.

Use Railway PostgreSQL backups and test restoring one periodically. The
application initializes and applies its existing schema changes at startup;
use `--preload` so initialization runs once before Gunicorn workers start.
Rotate `SECRET_KEY` only when necessary because doing so invalidates all
existing sessions.
