import json
import math
import os
import secrets
import sqlite3
from datetime import datetime, date, timedelta
from urllib.parse import quote

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session
from requests.exceptions import RequestException
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT"))
IS_PRODUCTION = IS_RAILWAY or os.getenv("APP_ENV", "").lower() == "production"
SECRET_KEY = os.getenv("SECRET_KEY")
if IS_PRODUCTION:
    if not SECRET_KEY or len(SECRET_KEY) < 32:
        raise RuntimeError("Set SECRET_KEY to a random value with at least 32 characters.")
    if not os.getenv("ADMIN_USERNAME") or not os.getenv("ADMIN_PASSWORD"):
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD in the production environment.")
    if IS_RAILWAY and not os.getenv("DATABASE_URL"):
        raise RuntimeError("Configure a Railway PostgreSQL DATABASE_URL; local SQLite is not persistent on Railway.")
elif not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=1_048_576,
)
PORT = int(os.environ.get("PORT", 5050))
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = DATABASE_URL is not None
PUSH_VAPID_PUBLIC_KEY = os.getenv("PUSH_VAPID_PUBLIC_KEY", "")
PUSH_VAPID_PRIVATE_KEY = os.getenv("PUSH_VAPID_PRIVATE_KEY", "")
PUSH_VAPID_SUBJECT = os.getenv("PUSH_VAPID_SUBJECT", "")

SECURE_ADMIN_USER = os.getenv("ADMIN_USERNAME", "Niyoo_2026")
SECURE_ADMIN_PASS = os.getenv("ADMIN_PASSWORD")

MAX_ATTEMPTS = 3
LOCK_MINUTES = 15


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def get_db():
    if 'db' not in g:
        if USE_POSTGRES:
            import psycopg2
            g.db = psycopg2.connect(DATABASE_URL)
        else:
            g.db = sqlite3.connect("sme.db", detect_types=sqlite3.PARSE_DECLTYPES)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def run_query(q, params=(), fetch=False):
    db = get_db()
    if USE_POSTGRES:
        q = q.replace("?", "%s")
    cur = db.cursor()
    cur.execute(q, params)
    if fetch:
        return cur.fetchall(), None
    db.commit()
    return None, None


def init_db():
    if USE_POSTGRES:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        def exec_sql(sql, params=()):
            cur.execute(sql.replace("?", "%s"), params)
    else:
        conn = sqlite3.connect("sme.db")
        cur = conn.cursor()

        def exec_sql(sql, params=()):
            cur.execute(sql, params)

    # USERS with branch lock + owner_ref
    exec_sql(
        "CREATE TABLE IF NOT EXISTS users "
        "(id SERIAL PRIMARY KEY, username TEXT UNIQUE, pin_hash TEXT, business_type TEXT, role TEXT, branch_id INTEGER, owner_ref TEXT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS users "
        "(id INTEGER PRIMARY KEY, username TEXT UNIQUE, pin_hash TEXT, business_type TEXT, role TEXT, branch_id INTEGER, owner_ref TEXT)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS branches "
        "(id SERIAL PRIMARY KEY, owner TEXT, business_type TEXT, name TEXT, district TEXT, phone TEXT, created_at TEXT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS branches "
        "(id INTEGER PRIMARY KEY, owner TEXT, business_type TEXT, name TEXT, district TEXT, phone TEXT, created_at TEXT)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS products "
        "(id SERIAL PRIMARY KEY, name TEXT, category TEXT, unit TEXT, quantity INT, buy_price REAL, sell_price REAL, business_type TEXT, owner TEXT, barcode TEXT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS products "
        "(id INTEGER PRIMARY KEY, name TEXT, category TEXT, unit TEXT, quantity INTEGER, buy_price REAL, sell_price REAL, business_type TEXT, owner TEXT, barcode TEXT)"
    )
    if USE_POSTGRES:
        exec_sql("ALTER TABLE products ADD COLUMN IF NOT EXISTS min_sell_price REAL")
    else:
        cur.execute("PRAGMA table_info(products)")
        product_columns = [row[1] for row in cur.fetchall()]
        if "min_sell_price" not in product_columns:
            cur.execute("ALTER TABLE products ADD COLUMN min_sell_price REAL")
    exec_sql("UPDATE products SET min_sell_price=sell_price WHERE min_sell_price IS NULL")
    exec_sql(
        "CREATE TABLE IF NOT EXISTS branch_stock "
        "(id SERIAL PRIMARY KEY, owner TEXT, business_type TEXT, branch_id INT, product_id INT, qty INT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS branch_stock "
        "(id INTEGER PRIMARY KEY, owner TEXT, business_type TEXT, branch_id INTEGER, product_id INTEGER, qty INTEGER)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS sales "
        "(id SERIAL PRIMARY KEY, product_id INT, product_name TEXT, quantity INT, total REAL, profit REAL, date TEXT, business_type TEXT, owner TEXT, branch_id INT, order_id INT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS sales "
        "(id INTEGER PRIMARY KEY, product_id INT, product_name TEXT, quantity INTEGER, total REAL, profit REAL, date TEXT, business_type TEXT, owner TEXT, branch_id INTEGER, order_id INTEGER)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS expenses "
        "(id SERIAL PRIMARY KEY, description TEXT, amount REAL, date TEXT, business_type TEXT, owner TEXT, branch_id INT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS expenses "
        "(id INTEGER PRIMARY KEY, description TEXT, amount REAL, date TEXT, business_type TEXT, owner TEXT, branch_id INTEGER)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS leads "
        "(id SERIAL PRIMARY KEY, owner TEXT, business_type TEXT, lead_name TEXT, contact_name TEXT, email TEXT, phone TEXT, source TEXT, status TEXT, priority TEXT, notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_contacted_at TIMESTAMP, next_follow_up_at TIMESTAMP)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS leads "
        "(id INTEGER PRIMARY KEY, owner TEXT, business_type TEXT, lead_name TEXT, contact_name TEXT, email TEXT, phone TEXT, source TEXT, status TEXT, priority TEXT, notes TEXT, created_at TEXT DEFAULT (datetime('now')), last_contacted_at TEXT, next_follow_up_at TEXT)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS lead_activities "
        "(id SERIAL PRIMARY KEY, owner TEXT, business_type TEXT, lead_id INT, activity_type TEXT, activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, summary TEXT, next_follow_up_at TIMESTAMP)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS lead_activities "
        "(id INTEGER PRIMARY KEY, owner TEXT, business_type TEXT, lead_id INT, activity_type TEXT, activity_at TEXT DEFAULT (datetime('now')), summary TEXT, next_follow_up_at TEXT)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS sales_orders "
        "(id SERIAL PRIMARY KEY, owner TEXT, business_type TEXT, lead_id INT, sale_day TEXT, status TEXT, customer_display_name TEXT, branch_id INT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, payment_status TEXT NOT NULL DEFAULT 'UNPAID', branch_name TEXT NOT NULL DEFAULT '', attendant_name TEXT NOT NULL DEFAULT '')"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS sales_orders "
        "(id INTEGER PRIMARY KEY, owner TEXT, business_type TEXT, lead_id INT, sale_day TEXT, status TEXT, customer_display_name TEXT, branch_id INTEGER, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')), payment_status TEXT NOT NULL DEFAULT 'UNPAID', branch_name TEXT NOT NULL DEFAULT '', attendant_name TEXT NOT NULL DEFAULT '')"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS transfers "
        "(id SERIAL PRIMARY KEY, owner TEXT, business_type TEXT, from_branch INT, to_branch INT, product_id INT, qty INT, date TEXT, by_user TEXT)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS transfers "
        "(id INTEGER PRIMARY KEY, owner TEXT, business_type TEXT, from_branch INTEGER, to_branch INTEGER, product_id INTEGER, qty INTEGER, date TEXT, by_user TEXT)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS push_subscriptions "
        "(id SERIAL PRIMARY KEY, owner TEXT NOT NULL, username TEXT NOT NULL, endpoint TEXT NOT NULL UNIQUE, subscription_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        if USE_POSTGRES
        else
        "CREATE TABLE IF NOT EXISTS push_subscriptions "
        "(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, username TEXT NOT NULL, endpoint TEXT NOT NULL UNIQUE, subscription_json TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    exec_sql(
        "CREATE TABLE IF NOT EXISTS login_attempts "
        "(username TEXT PRIMARY KEY, attempt_count INTEGER NOT NULL, locked_until TEXT, updated_at TEXT NOT NULL)"
    )

    if not SECURE_ADMIN_USER:
        raise RuntimeError("Set a non-empty ADMIN_USERNAME.")

    cur.execute(
        "SELECT id,username FROM users WHERE role='super_admin' AND owner_ref='admin' ORDER BY id LIMIT 1"
    )
    primary_admin = cur.fetchone()

    if not primary_admin:
        if not SECURE_ADMIN_PASS:
            raise RuntimeError("Set ADMIN_PASSWORD before initializing a database without an owner account.")
        if len(SECURE_ADMIN_PASS) < 12:
            raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters.")
        ph = generate_password_hash(SECURE_ADMIN_PASS)
        exec_sql(
            "INSERT INTO users(username,pin_hash,business_type,role,branch_id,owner_ref) VALUES (?,?,?,?,?,?)",
            (SECURE_ADMIN_USER, ph, "Incubators Business", "super_admin", None, "admin"),
        )
        for name, dist in [("Kampala Main", "Kampala"), ("Mbale Branch", "Mbale"), ("Mbarara Branch", "Mbarara"), ("Gulu Branch", "Gulu")]:
            exec_sql(
                "INSERT INTO branches(owner,business_type,name,district,created_at) VALUES (?,?,?,?,?)",
                ("admin", "Incubators Business", name, dist, datetime.now().isoformat()),
            )
    else:
        admin_id, current_admin_name = primary_admin
        if current_admin_name != SECURE_ADMIN_USER:
            exec_sql("SELECT id FROM users WHERE username=?", (SECURE_ADMIN_USER,))
            if cur.fetchone():
                raise RuntimeError("ADMIN_USERNAME is already used by another account.")
            exec_sql("UPDATE users SET username=? WHERE id=?", (SECURE_ADMIN_USER, admin_id))
        if SECURE_ADMIN_PASS:
            if IS_PRODUCTION and len(SECURE_ADMIN_PASS) < 12:
                raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters.")
            exec_sql(
                "UPDATE users SET pin_hash=? WHERE id=?",
                (generate_password_hash(SECURE_ADMIN_PASS), admin_id),
            )

    cur.execute("SELECT id,pin_hash FROM users")
    for user_id, stored_password in cur.fetchall():
        if not isinstance(stored_password, str) or not stored_password.startswith(("scrypt:", "pbkdf2:")):
            exec_sql(
                "UPDATE users SET pin_hash=? WHERE id=?",
                (generate_password_hash(str(stored_password or "")), user_id),
            )

    # Ensure barcode column exists for old DB
    try:
        if not USE_POSTGRES:
            cur.execute("PRAGMA table_info(products)")
            cols = [r[1] for r in cur.fetchall()]
            if "barcode" not in cols:
                cur.execute("ALTER TABLE products ADD COLUMN barcode TEXT")
    except:
        pass

    if USE_POSTGRES:
        exec_sql("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'UNPAID'")
        exec_sql("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS branch_name TEXT NOT NULL DEFAULT ''")
        exec_sql("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS attendant_name TEXT NOT NULL DEFAULT ''")
    else:
        cur.execute("PRAGMA table_info(sales_orders)")
        order_columns = [r[1] for r in cur.fetchall()]
        if "payment_status" not in order_columns:
            cur.execute("ALTER TABLE sales_orders ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'UNPAID'")
        if "branch_name" not in order_columns:
            cur.execute("ALTER TABLE sales_orders ADD COLUMN branch_name TEXT NOT NULL DEFAULT ''")
        if "attendant_name" not in order_columns:
            cur.execute("ALTER TABLE sales_orders ADD COLUMN attendant_name TEXT NOT NULL DEFAULT ''")

    conn.commit()
    conn.close()


init_db()


def require_login():
    return session.get("logged_in")


def get_session_owner_bt():
    return session.get("owner", "admin"), session.get("business_type", "Incubators Business")


def is_admin():
    return session.get("role") == "super_admin"


def is_owner():
    return is_admin() and session.get("user") == SECURE_ADMIN_USER


def get_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def valid_csrf_token():
    expected = session.get("_csrf_token", "")
    submitted = request.headers.get("X-CSRF-Token", "")
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            submitted = payload.get("csrf_token", submitted)
    else:
        submitted = request.form.get("csrf_token", submitted)
    return bool(
        expected
        and isinstance(submitted, str)
        and submitted
        and secrets.compare_digest(expected, submitted)
    )


@app.before_request
def protect_state_changes():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not valid_csrf_token():
        if request.is_json:
            return jsonify({"error": "Invalid or missing CSRF token"}), 400
        abort(400, description="Invalid or missing CSRF token")


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token()}


def get_login_lockout(username, now):
    rows, _ = run_query(
        "SELECT attempt_count,locked_until FROM login_attempts WHERE username=?",
        (username,),
        fetch=True,
    )
    if not rows:
        return 0, None
    locked_until = datetime.fromisoformat(rows[0][1]) if rows[0][1] else None
    if locked_until and now >= locked_until:
        return 0, None
    return rows[0][0], locked_until


def record_failed_login(username, now):
    now_text = now.isoformat(timespec="seconds")
    stale_before = (now - timedelta(days=1)).isoformat(timespec="seconds")
    locked_until = (now + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
    run_query("DELETE FROM login_attempts WHERE updated_at<?", (stale_before,), fetch=False)
    run_query(
        "INSERT INTO login_attempts(username,attempt_count,locked_until,updated_at) "
        "VALUES (?,1,NULL,?) ON CONFLICT(username) DO UPDATE SET "
        "attempt_count=CASE "
        "WHEN login_attempts.updated_at<? THEN 1 "
        "WHEN login_attempts.locked_until IS NOT NULL AND login_attempts.locked_until<=? THEN 1 "
        "ELSE login_attempts.attempt_count+1 END, "
        "locked_until=CASE "
        "WHEN login_attempts.updated_at<? THEN NULL "
        "WHEN login_attempts.locked_until IS NOT NULL AND login_attempts.locked_until<=? THEN NULL "
        "WHEN login_attempts.locked_until IS NOT NULL THEN login_attempts.locked_until "
        "WHEN login_attempts.attempt_count+1>=? THEN ? ELSE NULL END, "
        "updated_at=?",
        (
            username,
            now_text,
            stale_before,
            now_text,
            stale_before,
            now_text,
            MAX_ATTEMPTS,
            locked_until,
            now_text,
        ),
        fetch=False,
    )
    return get_login_lockout(username, now)[0]


def get_web_push_components():
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return None
    return WebPushException, webpush


def push_is_configured():
    return bool(
        PUSH_VAPID_PUBLIC_KEY
        and PUSH_VAPID_PRIVATE_KEY
        and PUSH_VAPID_SUBJECT
        and get_web_push_components()
    )


def send_admin_push(owner, title, body, url):
    if not all((PUSH_VAPID_PUBLIC_KEY, PUSH_VAPID_PRIVATE_KEY, PUSH_VAPID_SUBJECT)):
        app.logger.error("Push notification skipped: VAPID configuration is incomplete")
        return
    components = get_web_push_components()
    if components is None:
        app.logger.error(
            "Push notification skipped: pywebpush is not installed; install project requirements"
        )
        return
    web_push_exception, webpush_sender = components

    subscriptions, _ = run_query(
        "SELECT endpoint,subscription_json,username FROM push_subscriptions WHERE owner=?",
        (owner,),
        fetch=True
    )
    payload = json.dumps({"title": title, "body": body, "url": url})
    for endpoint, subscription_json, username in subscriptions:
        try:
            subscription = json.loads(subscription_json)
        except json.JSONDecodeError:
            app.logger.error("Invalid push subscription data for admin %s", username)
            continue

        try:
            webpush_sender(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=PUSH_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": PUSH_VAPID_SUBJECT},
                ttl=60 * 60,
                timeout=5
            )
        except web_push_exception as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (404, 410):
                run_query(
                    "DELETE FROM push_subscriptions WHERE endpoint=? AND owner=?",
                    (endpoint, owner),
                    fetch=False
                )
                app.logger.info("Removed expired push subscription for admin %s", username)
            else:
                app.logger.error(
                    "Push delivery failed for admin %s with status %s (%s)",
                    username,
                    status_code,
                    type(exc).__name__
                )
        except RequestException as exc:
            app.logger.error(
                "Push network request failed for admin %s (%s)",
                username,
                type(exc).__name__
            )


def notify_low_stock_if_crossed(owner, branch_name, product_name, previous_qty, current_qty):
    if current_qty > 5 or (previous_qty is not None and previous_qty <= 5):
        return
    send_admin_push(
        owner,
        "Low stock",
        f"{product_name} at {branch_name} has {current_qty} unit(s) left.",
        "/dashboard"
    )


@app.route("/service-worker.js")
def service_worker():
    response = app.send_static_file("service-worker.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/api/push/config")
def push_config():
    if not require_login() or not is_admin():
        return jsonify({"error": "Administrator login required"}), 403
    enabled = push_is_configured()
    if not enabled:
        reason = (
            "dependency"
            if all((PUSH_VAPID_PUBLIC_KEY, PUSH_VAPID_PRIVATE_KEY, PUSH_VAPID_SUBJECT))
            and get_web_push_components() is None
            else "vapid"
        )
    else:
        reason = ""
    return jsonify({
        "enabled": enabled,
        "reason": reason,
        "public_key": PUSH_VAPID_PUBLIC_KEY if enabled else ""
    })


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    if not require_login() or not is_admin():
        return jsonify({"error": "Administrator login required"}), 403
    if not push_is_configured():
        if (
            all((PUSH_VAPID_PUBLIC_KEY, PUSH_VAPID_PRIVATE_KEY, PUSH_VAPID_SUBJECT))
            and get_web_push_components() is None
        ):
            return jsonify({"error": "pywebpush is not installed. Install the project requirements to enable phone alerts."}), 503
        return jsonify({"error": "Push notification VAPID credentials are not configured"}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid subscription request"}), 400
    expected = session.get("_csrf_token", "")
    submitted = payload.get("csrf_token", "")
    if (
        not expected
        or not isinstance(submitted, str)
        or not submitted
        or not secrets.compare_digest(expected, submitted)
    ):
        return jsonify({"error": "Invalid request token"}), 400

    subscription = payload.get("subscription")
    if not isinstance(subscription, dict):
        return jsonify({"error": "Invalid push subscription"}), 400
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys")
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith("https://")
        or len(endpoint) > 4096
        or not isinstance(keys, dict)
        or not isinstance(keys.get("p256dh"), str)
        or not isinstance(keys.get("auth"), str)
        or not keys["p256dh"]
        or not keys["auth"]
    ):
        return jsonify({"error": "The browser returned an invalid push subscription"}), 400

    owner, _ = get_session_owner_bt()
    username = session.get("user", "")
    query = (
        "INSERT INTO push_subscriptions(owner,username,endpoint,subscription_json,created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET "
        "owner=excluded.owner,username=excluded.username,"
        "subscription_json=excluded.subscription_json,created_at=excluded.created_at"
    )
    run_query(
        query,
        (owner, username, endpoint, json.dumps(subscription), datetime.now().isoformat()),
        fetch=False
    )
    return jsonify({"subscribed": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    if not require_login() or not is_admin():
        return jsonify({"error": "Administrator login required"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid unsubscribe request"}), 400
    expected = session.get("_csrf_token", "")
    submitted = payload.get("csrf_token", "")
    if (
        not expected
        or not isinstance(submitted, str)
        or not submitted
        or not secrets.compare_digest(expected, submitted)
    ):
        return jsonify({"error": "Invalid request token"}), 400

    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return jsonify({"error": "Invalid subscription endpoint"}), 400
    owner, _ = get_session_owner_bt()
    run_query(
        "DELETE FROM push_subscriptions WHERE endpoint=? AND owner=? AND username=?",
        (endpoint, owner, session.get("user", "")),
        fetch=False
    )
    return jsonify({"subscribed": False})


def normalize_whatsapp_number(raw_value):
    if raw_value is None:
        return ""
    digits = "".join(ch for ch in str(raw_value) if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 10 and digits.startswith("0"):
        return "256" + digits[1:]
    if len(digits) == 9:
        return "256" + digits
    if len(digits) > 10 and digits.startswith("256"):
        return digits
    if len(digits) == 12 and digits.startswith("256"):
        return digits
    return digits


def build_lead_followup_message(lead_row):
    lead_name = (lead_row[3] if len(lead_row) > 3 else "") or "customer"
    contact_name = (lead_row[4] if len(lead_row) > 4 else "") or ""
    next_follow_up = (lead_row[13] if len(lead_row) > 13 else "") or "soon"
    greeting = contact_name.strip() or lead_name.strip() or "there"
    return (
        f"Hello {greeting}, this is Niyoo Farm Services. "
        f"I am following up regarding {lead_name}. "
        f"We would be happy to assist you further. "
        f"Please let us know when you are available to continue. "
        f"Thank you. Next follow-up: {next_follow_up}."
    )


@app.route("/", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:150]
        pin = request.form.get("pin") or request.form.get("password") or ""
        pin = pin if len(pin) <= 256 else ""
        if not username:
            flash("Enter a username and password", "danger")
            return render_template("login.html")

        now = datetime.now()
        _, locked_until = get_login_lockout(username, now)
        if locked_until and now < locked_until:
            mins = int((locked_until - now).total_seconds() / 60) + 1
            flash(f"Locked {mins} min. Too many attempts", "danger")
            return render_template("login.html")

        rows, _ = run_query("SELECT * FROM users WHERE username=?", (username,), fetch=True)
        if rows:
            stored = rows[0][2]
            try:
                ok = check_password_hash(stored, pin)
            except (TypeError, ValueError):
                ok = False

            if ok:
                role = rows[0][4]
                owner_ref = rows[0][6] or "admin"
                if role not in ("staff", "super_admin") or (role == "super_admin" and owner_ref != "admin"):
                    flash("This account is not authorized to sign in", "danger")
                    return render_template("login.html")
                if role == "staff":
                    branch = rows[0][5]
                    valid_branch, _ = run_query(
                        "SELECT id FROM branches WHERE id=? AND owner=?",
                        (branch, owner_ref),
                        fetch=True
                    )
                    if not branch or not valid_branch:
                        flash("This staff account has no valid branch assignment. Contact the owner.", "danger")
                        return render_template("login.html")

                run_query("DELETE FROM login_attempts WHERE username=?", (username,), fetch=False)
                session.clear()
                session.permanent = True
                session["logged_in"] = True
                session["user"] = rows[0][1]
                session["business_type"] = rows[0][3]
                session["role"] = role
                session["branch_id"] = rows[0][5]
                session["owner"] = owner_ref
                session["display_branch"] = rows[0][5]
                return redirect("/dashboard")

        attempt_count = record_failed_login(username, now)
        flash(f"Wrong login {attempt_count}/{MAX_ATTEMPTS}", "danger")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/")


@app.route("/staff", methods=["GET", "POST"])
def staff_page():
    if not require_login():
        return redirect("/")
    if not is_owner():
        flash("Only the owner can manage staff and administrator accounts", "danger")
        return redirect("/dashboard")

    owner, _ = get_session_owner_bt()

    if request.method == "POST":
        uname = request.form['username'].strip()
        pwd = request.form['password'].strip()
        role = request.form.get("role", "")
        branch_value = request.form.get("branch_id", "").strip()
        if uname and pwd:
            rows, _ = run_query("SELECT id FROM users WHERE username=?", (uname,), fetch=True)
            if role not in ("staff", "super_admin"):
                flash("Select a valid account role", "danger")
            elif rows:
                flash("Username exists", "danger")
            elif role == "super_admin":
                db = get_db()
                cursor = db.cursor()
                db.commit()
                cursor.execute("BEGIN" if USE_POSTGRES else "BEGIN IMMEDIATE")
                lock_owner_sql = "SELECT id FROM users WHERE username=? AND owner_ref=? AND role='super_admin'"
                if USE_POSTGRES:
                    lock_owner_sql += " FOR UPDATE"
                cursor.execute(
                    lock_owner_sql.replace("?", "%s") if USE_POSTGRES else lock_owner_sql,
                    (SECURE_ADMIN_USER, owner)
                )
                if not cursor.fetchone():
                    db.rollback()
                    flash("Owner account could not be verified", "danger")
                else:
                    count_sql = "SELECT COUNT(*) FROM users WHERE owner_ref=? AND role='super_admin'"
                    cursor.execute(count_sql.replace("?", "%s") if USE_POSTGRES else count_sql, (owner,))
                    if cursor.fetchone()[0] >= 3:
                        db.rollback()
                        flash("The maximum of three administrators, including the owner, has been reached", "danger")
                    else:
                        insert_sql = "INSERT INTO users(username,pin_hash,business_type,role,branch_id,owner_ref) VALUES (?,?,?,?,?,?)"
                        cursor.execute(
                            insert_sql.replace("?", "%s") if USE_POSTGRES else insert_sql,
                            (uname, generate_password_hash(pwd), "Incubators Business", "super_admin", None, owner)
                        )
                        db.commit()
                        flash(f"Administrator {uname} created", "success")
            else:
                try:
                    bid = int(branch_value)
                except ValueError:
                    bid = None
                branch, _ = run_query(
                    "SELECT id FROM branches WHERE id=? AND owner=?",
                    (bid, owner),
                    fetch=True
                ) if bid is not None else ([], None)
                if not branch:
                    flash("Select a valid branch for this staff account", "danger")
                else:
                    run_query(
                        "INSERT INTO users(username,pin_hash,business_type,role,branch_id,owner_ref) VALUES (?,?,?,?,?,?)",
                        (uname, generate_password_hash(pwd), "Incubators Business", "staff", bid, owner),
                        fetch=False,
                    )
                    flash(f"Staff {uname} created - Branch locked", "success")

    staff_list, _ = run_query(
        "SELECT u.id,u.username,u.pin_hash,u.business_type,u.role,"
        "COALESCE(b.name,CASE WHEN u.role='super_admin' THEN 'All branches' ELSE 'Unassigned' END),u.owner_ref "
        "FROM users u LEFT JOIN branches b ON b.id=u.branch_id AND b.owner=u.owner_ref "
        "WHERE u.owner_ref=? ORDER BY u.role,u.username",
        (owner,),
        fetch=True
    )
    branches, _ = run_query("SELECT * FROM branches WHERE owner=?", (owner,), fetch=True)
    admin_count, _ = run_query(
        "SELECT COUNT(*) FROM users WHERE owner_ref=? AND role='super_admin'",
        (owner,),
        fetch=True
    )
    return render_template(
        "staff.html",
        staff=staff_list,
        branches=branches,
        secure_admin=SECURE_ADMIN_USER,
        admin_count=admin_count[0][0],
        csrf_token=get_csrf_token()
    )


@app.route("/staff/delete/<int:uid>", methods=["POST"])
def delete_staff(uid):
    if not require_login() or not is_owner():
        return redirect("/dashboard")
    if not valid_csrf_token():
        flash("Invalid request token. Refresh and try again.", "danger")
        return redirect("/staff")
    user, _ = run_query(
        "SELECT username FROM users WHERE id=? AND owner_ref=?",
        (uid, get_session_owner_bt()[0]),
        fetch=True
    )
    if not user or user[0][0] == SECURE_ADMIN_USER:
        flash("The owner account cannot be deleted", "danger")
    else:
        run_query(
            "DELETE FROM push_subscriptions WHERE owner=? AND username=?",
            (get_session_owner_bt()[0], user[0][0]),
            fetch=False
        )
        run_query(
            "DELETE FROM users WHERE id=? AND owner_ref=? AND username<>?",
            (uid, get_session_owner_bt()[0], SECURE_ADMIN_USER),
            fetch=False
        )
    return redirect("/staff")


@app.route("/branches", methods=["GET", "POST"])
def branches_page():
    if not require_login():
        return redirect("/")
    owner, bt = get_session_owner_bt()

    if request.method == "POST" and is_admin():
        run_query(
            "INSERT INTO branches(owner,business_type,name,district,phone,created_at) VALUES (?,?,?,?,?,?)",
            (owner, bt, request.form['name'], request.form['district'], request.form['phone'], datetime.now().isoformat()),
            fetch=False,
        )
    elif request.method == "POST" and not is_admin():
        flash("Staff cannot create branches - Admin only", "danger")

    if is_admin():
        branches, _ = run_query("SELECT * FROM branches WHERE owner=?", (owner,), fetch=True)
    else:
        branches, _ = run_query("SELECT * FROM branches WHERE owner=? AND id=?", (owner, session.get("branch_id")), fetch=True)

    return render_template("branches.html", branches=branches, is_admin=is_admin())


@app.route("/stock/manage", methods=["GET", "POST"])
def stock_manage():
    if not require_login():
        return redirect("/")
    owner, bt = get_session_owner_bt()
    my_branch = session.get("branch_id")

    if request.method == "POST":
        action = request.form.get("action")

        if action in ("add", "set"):
            if not is_owner():
                flash("Only the owner can edit stock quantities", "danger")
                return redirect("/stock/manage")
            try:
                bid = int(request.form["branch_id"])
                qty = int(request.form["qty"])
                if action == "add":
                    product_name = request.form.get("product_name", "").strip()
                    category = request.form.get("category", "").strip()[:100] or "General"
                    unit = request.form.get("unit", "").strip()[:30] or "Pcs"
                    buy_price = float(request.form.get("buy_price") or 0)
                    sell_price = float(request.form.get("sell_price") or 0)
                else:
                    pid = int(request.form["product_id"])
            except (KeyError, TypeError, ValueError):
                flash("Choose a branch and enter valid product, price, and stock details", "danger")
                return redirect("/stock/manage")
            if action == "add" and (not product_name or len(product_name) > 150):
                flash("Enter a product name of 1 to 150 characters", "danger")
                return redirect("/stock/manage")
            if action == "add" and (
                not math.isfinite(buy_price) or not math.isfinite(sell_price)
                or buy_price < 0 or sell_price < 0
            ):
                flash("Product prices must be zero or greater", "danger")
                return redirect("/stock/manage")
            if qty < 0 or (action == "add" and qty == 0):
                flash("Stock quantity must be positive when adding, or zero or greater when setting stock", "danger")
                return redirect("/stock/manage")
            valid_branch, _ = run_query("SELECT id,name FROM branches WHERE id=? AND owner=?", (bid, owner), fetch=True)
            if not valid_branch:
                flash("Choose a valid branch", "danger")
                return redirect("/stock/manage")

            if action == "add":
                db = get_db()
                cursor = db.cursor()
                db.commit()
                cursor.execute("BEGIN" if USE_POSTGRES else "BEGIN IMMEDIATE")

                def execute_stock_query(sql, params=()):
                    cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, params)

                try:
                    product_sql = (
                        "SELECT id FROM products WHERE owner=? AND LOWER(TRIM(name))=LOWER(TRIM(?))"
                        + (" FOR UPDATE" if USE_POSTGRES else "")
                    )
                    execute_stock_query(product_sql, (owner, product_name))
                    product_row = cursor.fetchone()
                    if product_row:
                        pid = product_row[0]
                    else:
                        insert_product_sql = (
                            "INSERT INTO products(name,category,unit,quantity,buy_price,sell_price,business_type,owner,barcode,min_sell_price) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)"
                        )
                        product_params = (
                            product_name, category, unit, 0, buy_price, sell_price,
                            bt, owner, request.form.get("barcode", "").strip() or None, sell_price
                        )
                        if USE_POSTGRES:
                            execute_stock_query(insert_product_sql + " RETURNING id", product_params)
                            pid = cursor.fetchone()[0]
                        else:
                            execute_stock_query(insert_product_sql, product_params)
                            pid = cursor.lastrowid

                    stock_sql = (
                        "SELECT qty FROM branch_stock WHERE owner=? AND branch_id=? AND product_id=?"
                        + (" FOR UPDATE" if USE_POSTGRES else "")
                    )
                    execute_stock_query(stock_sql, (owner, bid, pid))
                    stock_row = cursor.fetchone()
                    previous_qty = stock_row[0] if stock_row else None
                    current_qty = (previous_qty or 0) + qty
                    if stock_row:
                        execute_stock_query(
                            "UPDATE branch_stock SET qty=qty+? WHERE owner=? AND branch_id=? AND product_id=?",
                            (qty, owner, bid, pid)
                        )
                    else:
                        execute_stock_query(
                            "INSERT INTO branch_stock(owner,business_type,branch_id,product_id,qty) VALUES (?,?,?,?,?)",
                            (owner, bt, bid, pid, qty)
                        )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

                product_display_name = product_name
            else:
                valid_product, _ = run_query(
                    "SELECT id,name FROM products WHERE id=? AND owner=?",
                    (pid, owner),
                    fetch=True
                )
                if not valid_product:
                    flash("Choose a valid product", "danger")
                    return redirect("/stock/manage")
                rows, _ = run_query(
                    "SELECT qty FROM branch_stock WHERE owner=? AND branch_id=? AND product_id=?",
                    (owner, bid, pid),
                    fetch=True
                )
                previous_qty = rows[0][0] if rows else None
                current_qty = qty
                if rows:
                    run_query(
                        "UPDATE branch_stock SET qty=? WHERE owner=? AND branch_id=? AND product_id=?",
                        (qty, owner, bid, pid),
                        fetch=False
                    )
                else:
                    run_query(
                        "INSERT INTO branch_stock(owner,business_type,branch_id,product_id,qty) VALUES (?,?,?,?,?)",
                        (owner, bt, bid, pid, qty),
                        fetch=False
                    )
                product_display_name = valid_product[0][1]
            notify_low_stock_if_crossed(
                owner, valid_branch[0][1], product_display_name, previous_qty, current_qty
            )
            flash(f"Stock {'added' if action == 'add' else 'updated'} to {qty if action == 'set' else 'increase by ' + str(qty)}", "success")

        elif action == "transfer":
            if not is_owner():
                flash("Only the owner can transfer or edit stock between branches", "danger")
                return redirect("/stock/manage")
            try:
                fb = int(request.form["from_branch"])
                tb = int(request.form["to_branch"])
                pid = int(request.form["product_id"])
                qty = int(request.form["qty"])
            except (KeyError, TypeError, ValueError):
                flash("Choose valid branches and product, and enter a valid transfer quantity", "danger")
                return redirect("/stock/manage")
            if fb == tb or qty <= 0:
                flash("Choose different branches and a transfer quantity greater than zero", "danger")
                return redirect("/stock/manage")
            valid_branches, _ = run_query(
                "SELECT id,name FROM branches WHERE owner=? AND id IN (?,?)",
                (owner, fb, tb),
                fetch=True
            )
            valid_product, _ = run_query("SELECT id,name FROM products WHERE id=? AND owner=?", (pid, owner), fetch=True)
            branch_names = {row[0]: row[1] for row in valid_branches}
            if len(branch_names) != 2 or not valid_product:
                flash("Choose valid branches and product", "danger")
                return redirect("/stock/manage")
            stock, _ = run_query(
                "SELECT qty FROM branch_stock WHERE owner=? AND branch_id=? AND product_id=?",
                (owner, fb, pid),
                fetch=True
            )
            available = stock[0][0] if stock else 0
            if available < qty:
                flash("Not enough stock", "danger")
                return redirect("/stock/manage")

            db = get_db()
            cursor = db.cursor()
            db.commit()
            cursor.execute("BEGIN" if USE_POSTGRES else "BEGIN IMMEDIATE")
            def execute_transfer_query(sql, params):
                cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, params)
            try:
                execute_transfer_query(
                    "UPDATE branch_stock SET qty=qty-? WHERE owner=? AND branch_id=? AND product_id=? AND qty>=?",
                    (qty, owner, fb, pid, qty)
                )
                if cursor.rowcount != 1:
                    raise ValueError("Stock changed during transfer; please retry")
                execute_transfer_query(
                    "UPDATE branch_stock SET qty=qty+? WHERE owner=? AND branch_id=? AND product_id=?",
                    (qty, owner, tb, pid)
                )
                if cursor.rowcount == 0:
                    execute_transfer_query(
                        "INSERT INTO branch_stock(owner,business_type,branch_id,product_id,qty) VALUES (?,?,?,?,?)",
                        (owner, bt, tb, pid, qty)
                    )
                execute_transfer_query(
                    "INSERT INTO transfers(owner,business_type,from_branch,to_branch,product_id,qty,date,by_user) VALUES (?,?,?,?,?,?,?,?)",
                    (owner, bt, fb, tb, pid, qty, datetime.now().isoformat(), session.get("user"))
                )
                db.commit()
                notify_low_stock_if_crossed(
                    owner, branch_names[fb], valid_product[0][1], available, available - qty
                )
                flash("Stock transfer completed", "success")
            except ValueError as exc:
                db.rollback()
                flash(str(exc), "danger")

    if is_admin():
        branches, _ = run_query("SELECT * FROM branches WHERE owner=?", (owner,), fetch=True)
    else:
        branches, _ = run_query("SELECT * FROM branches WHERE owner=? AND id=?", (owner, my_branch), fetch=True)

    # IMPORTANT: align with stock_manage.html expectations (p[0..7] indices)
    # Template uses:
    # - p[0] id
    # - p[1] name
    # - p[4] (unit)
    # - p[6] (sell_price)
    # - p[7] (barcode)
    products, _ = run_query(
        "SELECT p.id, p.name, p.category, p.sell_price, p.unit, p.buy_price, p.sell_price, p.barcode, "
        "COALESCE(p.min_sell_price,p.sell_price) "
        "FROM products p WHERE p.owner=? ORDER BY p.name",
        (owner,),
        fetch=True
    )

    if is_admin():
        stock, _ = run_query(
            "SELECT b.name, p.name, s.qty, s.branch_id, s.product_id, p.barcode "
            "FROM branch_stock s "
            "JOIN branches b ON b.id=s.branch_id "
            "JOIN products p ON p.id=s.product_id "
            "WHERE s.owner=?",
            (owner,),
            fetch=True
        )
    else:
        stock, _ = run_query(
            "SELECT b.name, p.name, s.qty, s.branch_id, s.product_id, p.barcode "
            "FROM branch_stock s "
            "JOIN branches b ON b.id=s.branch_id "
            "JOIN products p ON p.id=s.product_id "
            "WHERE s.owner=? AND s.branch_id=?",
            (owner, my_branch),
            fetch=True
        )

    return render_template(
        "stock_manage.html",
        branches=branches,
        products=products,
        stock=stock,
        is_admin=is_admin(),
        is_owner=is_owner(),
        my_branch=my_branch,
        csrf_token=get_csrf_token()
    )


@app.route("/api/product/by-barcode")
def by_barcode():
    if not require_login():
        return jsonify({"error": "login"}), 401
    code = request.args.get("code", "").strip()
    owner, _ = get_session_owner_bt()
    rows, _ = run_query("SELECT id, name, sell_price, barcode FROM products WHERE owner=? AND barcode=?", (owner, code), fetch=True)
    if rows:
        return jsonify({"found": True, "id": rows[0][0], "name": rows[0][1], "price": rows[0][2], "barcode": rows[0][3]})
    return jsonify({"found": False})


@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()
    my_branch = session.get("branch_id")

    if is_admin():
        sales_data, _ = run_query("SELECT SUM(total), SUM(profit) FROM sales WHERE owner=?", (owner,), fetch=True)
        branch_totals, _ = run_query(
            "SELECT b.name, COALESCE(SUM(s.total),0), COALESCE(SUM(s.profit),0) "
            "FROM branches b LEFT JOIN sales s "
            "ON s.branch_id=b.id AND s.owner=? "
            "WHERE b.owner=? GROUP BY b.name",
            (owner, owner),
            fetch=True
        )
        low_stock, _ = run_query(
            "SELECT b.name, p.name, s.qty "
            "FROM branch_stock s "
            "JOIN branches b ON b.id=s.branch_id "
            "JOIN products p ON p.id=s.product_id "
            "WHERE s.owner=? AND s.qty<=5",
            (owner,),
            fetch=True
        )
        recent_sales, _ = run_query("SELECT date, total FROM sales WHERE owner=? ORDER BY date DESC LIMIT 7", (owner,), fetch=True)
    else:
        sales_data, _ = run_query(
            "SELECT SUM(total), SUM(profit) FROM sales WHERE owner=? AND branch_id=?",
            (owner, my_branch),
            fetch=True
        )
        branch_totals, _ = run_query(
            "SELECT b.name, COALESCE(SUM(s.total),0), COALESCE(SUM(s.profit),0) "
            "FROM branches b LEFT JOIN sales s "
            "ON s.branch_id=b.id AND s.owner=? "
            "WHERE b.id=? GROUP BY b.name",
            (owner, my_branch),
            fetch=True
        )
        low_stock, _ = run_query(
            "SELECT b.name, p.name, s.qty "
            "FROM branch_stock s "
            "JOIN branches b ON b.id=s.branch_id "
            "JOIN products p ON p.id=s.product_id "
            "WHERE s.owner=? AND s.branch_id=? AND s.qty<=5",
            (owner, my_branch),
            fetch=True
        )
        recent_sales, _ = run_query(
            "SELECT date, total FROM sales WHERE owner=? AND branch_id=? ORDER BY date DESC LIMIT 7",
            (owner, my_branch),
            fetch=True
        )

    revenue = sales_data[0][0] or 0
    profit = sales_data[0][1] or 0

    overdue, _ = run_query(
        "SELECT COUNT(*) FROM leads WHERE owner=? AND next_follow_up_at<? AND status NOT IN ('Won','Lost')",
        (owner, datetime.now().isoformat()),
        fetch=True
    )

    return render_template(
        "dashboard.html",
        revenue=revenue,
        profit=profit,
        expenses=0,
        net_profit=profit,
        recent_sales=recent_sales,
        overdue_lead_followups=overdue[0][0] if overdue else 0,
        due_today_followups=0,
        due_next7_followups=0,
        branch_totals=branch_totals,
        low_stock=low_stock,
        is_admin=is_admin(),
        push_csrf_token=get_csrf_token() if is_admin() else ""
    )


@app.route("/billing")
def billing_page():
    return redirect("/pos")


@app.route("/receipts")
def receipts_page():
    if not require_login():
        return redirect("/")

    owner, _ = get_session_owner_bt()
    query = (
        "SELECT o.id,o.sale_day,o.customer_display_name,"
        "COALESCE(NULLIF(o.branch_name,''),b.name),o.attendant_name,"
        "COALESCE(SUM(s.total),0),o.status "
        "FROM sales_orders o "
        "LEFT JOIN branches b ON b.id=o.branch_id AND b.owner=o.owner "
        "LEFT JOIN sales s ON s.order_id=o.id AND s.owner=o.owner "
        "WHERE o.owner=? AND o.status='FINALIZED'"
    )
    params = [owner]
    if not is_admin():
        query += " AND o.branch_id=?"
        params.append(session.get("branch_id"))
    query += (
        " GROUP BY o.id,o.sale_day,o.customer_display_name,o.branch_name,"
        "b.name,o.attendant_name,o.status ORDER BY o.id DESC LIMIT 200"
    )
    receipts, _ = run_query(query, tuple(params), fetch=True)
    return render_template("receipts.html", receipts=receipts)


@app.route("/pos", methods=["GET", "POST"])
def point_of_sale():
    if not require_login():
        return redirect("/")
    owner, bt = get_session_owner_bt()
    my_branch = session.get("branch_id")

    if request.method == "POST":
        customer = request.form.get("customer_name", "").strip()[:150] or "Walk-in customer"
        branch_value = request.form.get("branch_id", "")
        try:
            branch_id = int(branch_value)
        except (TypeError, ValueError):
            flash("Select a valid branch", "danger")
            return redirect("/pos")

        if not is_admin() and (not my_branch or branch_id != int(my_branch)):
            flash("You can only sell from your branch", "danger")
            return redirect("/pos")

        try:
            submitted_cart = json.loads(request.form.get("cart") or "[]")
            if not isinstance(submitted_cart, list) or not submitted_cart or len(submitted_cart) > 100:
                raise ValueError("Add products to the cart before checkout")

            quantities = {}
            for item in submitted_cart:
                if not isinstance(item, dict):
                    raise ValueError("The cart contains an invalid product")
                product_id = int(item.get("product_id"))
                quantity = int(item.get("quantity"))
                if product_id <= 0 or quantity <= 0:
                    raise ValueError("Product quantities must be greater than zero")
                submitted_price = item.get("unit_price")
                unit_price = None if submitted_price is None else float(submitted_price)
                if unit_price is not None and (not math.isfinite(unit_price) or unit_price < 0):
                    raise ValueError("Enter a valid unit price")
                if product_id in quantities:
                    existing_price = quantities[product_id][1]
                    if existing_price != unit_price:
                        raise ValueError("A product cannot have different prices in the same sale")
                    quantities[product_id][0] += quantity
                else:
                    quantities[product_id] = [quantity, unit_price]

            if any(quantity_and_price[0] > 10000 for quantity_and_price in quantities.values()):
                raise ValueError("A product quantity is too large")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            flash(str(exc) or "The cart is invalid", "danger")
            return redirect(f"/pos?branch_id={branch_id}")

        db = get_db()
        cursor = db.cursor()
        db.commit()
        cursor.execute("BEGIN" if USE_POSTGRES else "BEGIN IMMEDIATE")

        def execute_sale_query(sql, params=()):
            cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, params)

        low_stock_alerts = []

        try:
            execute_sale_query(
                "SELECT id,name FROM branches WHERE id=? AND owner=?",
                (branch_id, owner)
            )
            branch = cursor.fetchone()
            if not branch:
                raise ValueError("Selected branch was not found")

            cart_lines = []
            for product_id, (quantity, submitted_price) in quantities.items():
                lock_clause = " FOR UPDATE" if USE_POSTGRES else ""
                execute_sale_query(
                    "SELECT name,buy_price,sell_price,COALESCE(min_sell_price,sell_price) "
                    "FROM products WHERE id=? AND owner=?" + lock_clause,
                    (product_id, owner)
                )
                product = cursor.fetchone()
                if not product:
                    raise ValueError("A product in the cart was not found")
                sell_price = float(product[2] or 0)
                min_sell_price = float(product[3] if product[3] is not None else sell_price)
                unit_price = sell_price if submitted_price is None else submitted_price
                if unit_price < min_sell_price or unit_price > sell_price:
                    raise ValueError(
                        f"The price for {product[0]} must be between {min_sell_price:g} and {sell_price:g}"
                    )

                execute_sale_query(
                    "SELECT qty FROM branch_stock WHERE owner=? AND branch_id=? AND product_id=?" + lock_clause,
                    (owner, branch_id, product_id)
                )
                stock = cursor.fetchone()
                available = stock[0] if stock else 0
                if available < quantity:
                    raise ValueError(f"Not enough stock for {product[0]} (available: {available})")

                cart_lines.append((
                    product_id, quantity, product[0], float(product[1] or 0),
                    unit_price, available
                ))

            insert_sql = (
                "INSERT INTO sales_orders(owner,business_type,lead_id,sale_day,status,customer_display_name,branch_id,created_at,payment_status,branch_name,attendant_name) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            )
            insert_params = (
                owner, bt, None, date.today().isoformat(), "FINALIZED", customer, branch_id,
                datetime.now().isoformat(), "PAID", branch[1], session.get("user", "")
            )

            if USE_POSTGRES:
                execute_sale_query(insert_sql + " RETURNING id", insert_params)
                receipt_id = cursor.fetchone()[0]
            else:
                execute_sale_query(insert_sql, insert_params)
                receipt_id = cursor.lastrowid

            for product_id, quantity, name, buy_price, sell_price, available in cart_lines:
                total = quantity * sell_price
                profit = quantity * (sell_price - buy_price)
                execute_sale_query(
                    "INSERT INTO sales(product_id,product_name,quantity,total,profit,date,business_type,owner,order_id,branch_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (product_id, name, quantity, total, profit, datetime.now().isoformat(), bt, owner, receipt_id, branch_id)
                )
                execute_sale_query(
                    "UPDATE branch_stock SET qty=qty-? WHERE owner=? AND branch_id=? AND product_id=? AND qty>=?",
                    (quantity, owner, branch_id, product_id, quantity)
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"Stock changed during checkout for {name}; please retry")
                if available > 5 and available - quantity <= 5:
                    low_stock_alerts.append((branch[1], name, available, available - quantity))

            db.commit()
        except ValueError as exc:
            db.rollback()
            flash(str(exc), "danger")
            return redirect(f"/pos?branch_id={branch_id}")
        except Exception:
            db.rollback()
            raise

        for branch_name, product_name, previous_qty, current_qty in low_stock_alerts:
            notify_low_stock_if_crossed(
                owner, branch_name, product_name, previous_qty, current_qty
            )
        return redirect(f"/orders/{receipt_id}")

    if is_admin():
        branches, _ = run_query("SELECT id, name FROM branches WHERE owner=? ORDER BY name", (owner,), fetch=True)
        selected_branch = request.args.get("branch_id", type=int)
        allowed = [b[0] for b in branches]
        if selected_branch not in allowed:
            stocked_branches, _ = run_query(
                "SELECT b.id FROM branches b "
                "JOIN branch_stock s ON s.branch_id=b.id AND s.owner=b.owner "
                "WHERE b.owner=? AND s.qty>0 "
                "GROUP BY b.id ORDER BY SUM(s.qty) DESC, b.id LIMIT 1",
                (owner,),
                fetch=True
            )
            selected_branch = stocked_branches[0][0] if stocked_branches else (branches[0][0] if branches else None)
    else:
        branches, _ = run_query("SELECT id, name FROM branches WHERE owner=? AND id=?", (owner, my_branch), fetch=True)
        selected_branch = branches[0][0] if branches else None

    if selected_branch is not None:
        # IMPORTANT: align with billing.html expectations:
        # billing.html uses p[3]=Price, p[4]=Available, p[5]=Barcode
        # so return: id,name,category,sell_price,available,barcode
        products, _ = run_query(
            "SELECT p.id, p.name, p.category, p.sell_price, COALESCE(s.qty,0) AS available, p.barcode, "
            "COALESCE(p.min_sell_price,p.sell_price) "
            "FROM products p "
            "LEFT JOIN branch_stock s "
            "ON s.product_id=p.id AND s.owner=p.owner AND s.branch_id=? "
            "WHERE p.owner=? "
            "ORDER BY p.name",
            (selected_branch, owner),
            fetch=True
        )
    else:
        products = []

    products = [tuple(product) for product in products]
    return render_template("billing.html", branches=branches, products=products, selected_branch=selected_branch)


@app.route("/leads", methods=["GET", "POST"])
def leads_page():
    if not require_login():
        return redirect("/")
    owner, bt = get_session_owner_bt()

    if request.method == "POST" and request.form.get("action") == "add_lead":
        run_query(
            "INSERT INTO leads(owner,business_type,lead_name,phone,source,status,next_follow_up_at) VALUES (?,?,?,?,?,?,?)",
            (
                owner,
                bt,
                request.form.get("lead_name"),
                request.form.get("phone"),
                request.form.get("source"),
                request.form.get("status", "New"),
                request.form.get("next_follow_up_at"),
            ),
            fetch=False
        )
        send_admin_push(
            owner,
            "New lead",
            f"{request.form.get('lead_name', 'A lead')} was added by {session.get('user', 'a staff member')}.",
            "/leads"
        )
        return redirect("/leads")

    leads, _ = run_query(
        "SELECT id, lead_name, contact_name, phone, email, source, status, priority, created_at, last_contacted_at, next_follow_up_at "
        "FROM leads WHERE owner=? ORDER BY created_at DESC",
        (owner,),
        fetch=True
    )
    return render_template("leads.html", leads=leads, business_config={"Incubators Business": {}})


@app.route("/leads/<int:lead_id>")
def lead_detail(lead_id):
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()
    rows, _ = run_query("SELECT * FROM leads WHERE id=? AND owner=?", (lead_id, owner), fetch=True)
    if not rows:
        return redirect("/leads")
    acts, _ = run_query("SELECT * FROM lead_activities WHERE lead_id=? AND owner=? ORDER BY activity_at DESC", (lead_id, owner), fetch=True)
    return render_template("lead_detail.html", lead=rows[0], activities=acts)


@app.route("/leads/<int:lead_id>/whatsapp")
def lead_whatsapp(lead_id):
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()
    rows, _ = run_query("SELECT * FROM leads WHERE id=? AND owner=?", (lead_id, owner), fetch=True)
    if not rows:
        return redirect("/leads")

    lead_row = rows[0]
    phone = normalize_whatsapp_number(lead_row[6] if len(lead_row) > 6 else "")
    if not phone:
        flash("This lead has no valid WhatsApp number saved.", "danger")
        return redirect(f"/leads/{lead_id}")

    message = build_lead_followup_message(lead_row)
    return redirect(f"https://wa.me/{phone}?text={quote(message)}")


@app.route("/leads/<int:lead_id>/status", methods=["POST"])
def lead_status(lead_id):
    if not require_login():
        return redirect("/")
    owner, bt = get_session_owner_bt()
    new_status = request.form.get("status")
    run_query("UPDATE leads SET status=? WHERE id=? AND owner=?", (new_status, lead_id, owner), fetch=False)

    if new_status == "Won":
        # staff sale goes to his branch
        b_id = session.get("branch_id") or 1
        run_query(
            "INSERT INTO sales_orders(owner,business_type,lead_id,sale_day,status,customer_display_name,branch_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (owner, bt, lead_id, date.today().isoformat(), "DRAFT", f"Lead {lead_id}", b_id, datetime.now().isoformat()),
            fetch=False
        )
        rows, _ = run_query("SELECT id FROM sales_orders WHERE lead_id=? ORDER BY id DESC LIMIT 1", (lead_id,), fetch=True)
        if rows:
            return redirect(f"/orders/{rows[0][0]}")

    return redirect("/leads")


@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()
    my_branch = session.get("branch_id")

    order, _ = run_query(
        "SELECT o.id,o.business_type,o.sale_day,o.status,o.customer_display_name,o.branch_id,o.payment_status,b.name,"
        "o.branch_name,o.attendant_name "
        "FROM sales_orders o LEFT JOIN branches b ON b.id=o.branch_id "
        "WHERE o.id=? AND o.owner=?",
        (order_id, owner),
        fetch=True
    )
    if not order or (not is_admin() and order[0][5] != my_branch):
        return redirect("/billing")

    lines, _ = run_query(
        "SELECT s.id,s.product_name,s.quantity,s.total,s.profit,s.branch_id,b.name "
        "FROM sales s LEFT JOIN branches b ON b.id=s.branch_id "
        "WHERE s.order_id=? AND s.owner=? ORDER BY s.id",
        (order_id, owner),
        fetch=True
    )

    products, _ = run_query(
        "SELECT id,name,sell_price,COALESCE(min_sell_price,sell_price) "
        "FROM products WHERE owner=? ORDER BY name",
        (owner,),
        fetch=True
    )
    invoice_total, _ = run_query("SELECT COALESCE(SUM(total),0) FROM sales WHERE order_id=? AND owner=?", (order_id, owner), fetch=True)

    return render_template(
        "order_detail.html",
        order=order[0],
        lines=lines,
        products=products,
        invoice_total=invoice_total[0][0],
        is_owner=is_owner(),
        is_admin=is_admin(),
        csrf_token=get_csrf_token()
    )


@app.route("/orders/<int:order_id>/lines", methods=["POST"])
def order_add_line(order_id):
    if not require_login():
        return redirect("/")
    owner, bt = get_session_owner_bt()

    order, _ = run_query("SELECT branch_id,status FROM sales_orders WHERE id=? AND owner=?", (order_id, owner), fetch=True)
    if not order or order[0][1] != "DRAFT":
        flash("Only your draft invoices can be changed", "danger")
        return redirect("/billing")

    bid = order[0][0]
    if not is_admin() and (not session.get("branch_id") or int(bid) != int(session["branch_id"])):
        flash("You can only sell from your branch", "danger")
        return redirect("/billing")

    try:
        pid = int(request.form["product_id"])
        qty = int(request.form["quantity"])
        submitted_price = request.form.get("unit_price", "").strip()
        unit_price = None if not submitted_price else float(submitted_price)
    except (KeyError, TypeError, ValueError):
        flash("Choose a product and enter valid quantity and price details", "danger")
        return redirect(f"/orders/{order_id}")

    if qty <= 0:
        flash("Quantity must be greater than zero", "danger")
        return redirect(f"/orders/{order_id}")

    prod, _ = run_query(
        "SELECT name,buy_price,sell_price,COALESCE(min_sell_price,sell_price) "
        "FROM products WHERE id=? AND owner=?",
        (pid, owner),
        fetch=True
    )
    if prod:
        sell_price = float(prod[0][2] or 0)
        min_sell_price = float(prod[0][3] if prod[0][3] is not None else sell_price)
        unit_price = sell_price if unit_price is None else unit_price
        if not math.isfinite(unit_price) or unit_price < min_sell_price or unit_price > sell_price:
            flash(f"Unit price must be between {min_sell_price:g} and {sell_price:g}", "danger")
            return redirect(f"/orders/{order_id}")
        total = qty * unit_price
        profit = qty * (unit_price - float(prod[0][1] or 0))
        run_query(
            "INSERT INTO sales(product_id,product_name,quantity,total,profit,date,business_type,owner,order_id,branch_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, prod[0][0], qty, total, profit, datetime.now().isoformat(), bt, owner, order_id, bid),
            fetch=False
        )
    else:
        flash("Product was not found", "danger")

    return redirect(f"/orders/{order_id}")


@app.route("/sales/<int:sale_id>/delete", methods=["POST"])
def delete_sold_line(sale_id):
    if not require_login():
        return redirect("/")
    if not is_owner():
        flash("Only the owner can remove a sold item", "danger")
        return redirect("/dashboard")
    if not valid_csrf_token():
        flash("Invalid request token. Refresh and try again.", "danger")
        return redirect(request.referrer or "/dashboard")

    owner, bt = get_session_owner_bt()
    db = get_db()
    cursor = db.cursor()
    db.commit()
    cursor.execute("BEGIN" if USE_POSTGRES else "BEGIN IMMEDIATE")
    try:
        sql = (
            "SELECT s.product_id,s.quantity,s.branch_id,s.order_id,o.status "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.id=? AND s.owner=?"
        )
        cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, (sale_id, owner))
        line = cursor.fetchone()
        if not line:
            raise ValueError("Sold item was not found")
        product_id, quantity, branch_id, order_id, status = line
        if status != "FINALIZED":
            raise ValueError("Only items in completed sales can be removed")

        sql = "SELECT qty FROM branch_stock WHERE owner=? AND branch_id=? AND product_id=?"
        cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, (owner, branch_id, product_id))
        stock = cursor.fetchone()
        if stock:
            sql = "UPDATE branch_stock SET qty=qty+? WHERE owner=? AND branch_id=? AND product_id=?"
            cursor.execute(
                sql.replace("?", "%s") if USE_POSTGRES else sql,
                (quantity, owner, branch_id, product_id)
            )
        else:
            sql = "INSERT INTO branch_stock(owner,business_type,branch_id,product_id,qty) VALUES (?,?,?,?,?)"
            cursor.execute(
                sql.replace("?", "%s") if USE_POSTGRES else sql,
                (owner, bt, branch_id, product_id, quantity)
            )

        sql = "DELETE FROM sales WHERE id=? AND owner=? AND order_id=?"
        cursor.execute(
            sql.replace("?", "%s") if USE_POSTGRES else sql,
            (sale_id, owner, order_id)
        )
        if cursor.rowcount != 1:
            raise ValueError("Sold item changed before removal; please retry")

        sql = "SELECT COUNT(*) FROM sales WHERE order_id=? AND owner=?"
        cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, (order_id, owner))
        if cursor.fetchone()[0] == 0:
            sql = "UPDATE sales_orders SET status='VOID' WHERE id=? AND owner=?"
            cursor.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, (order_id, owner))

        db.commit()
    except ValueError as exc:
        db.rollback()
        flash(str(exc), "danger")
        return redirect(request.referrer or "/dashboard")
    except Exception:
        db.rollback()
        raise

    flash(f"Sold item removed and {quantity} unit(s) restored to its branch", "success")
    return redirect(f"/orders/{order_id}")


@app.route("/orders/<int:order_id>/finalize", methods=["POST"])
def order_finalize(order_id):
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()

    order, _ = run_query("SELECT branch_id,status FROM sales_orders WHERE id=? AND owner=?", (order_id, owner), fetch=True)
    if not order or order[0][1] != "DRAFT":
        flash("Only your draft invoices can be finalized", "danger")
        return redirect("/billing")

    if not is_admin() and (not session.get("branch_id") or int(order[0][0]) != int(session["branch_id"])):
        flash("You can only finalize invoices for your branch", "danger")
        return redirect("/billing")

    lines, _ = run_query("SELECT product_id, quantity, branch_id FROM sales WHERE order_id=? AND owner=?", (order_id, owner), fetch=True)
    if not lines:
        flash("Add at least one product before finalizing", "danger")
        return redirect(f"/orders/{order_id}")

    required = {}
    for pid, qty, bid in lines:
        required[(pid, bid)] = required.get((pid, bid), 0) + qty

    low_stock_alerts = []
    for (pid, bid), qty in required.items():
        stock, _ = run_query("SELECT qty FROM branch_stock WHERE owner=? AND branch_id=? AND product_id=?", (owner, bid, pid), fetch=True)
        available = stock[0][0] if stock else 0
        if available < qty:
            flash(f"Not enough in branch {bid}", "danger")
            return redirect(f"/orders/{order_id}")
        if available > 5 and available - qty <= 5:
            product_info, _ = run_query(
                "SELECT p.name,b.name FROM products p JOIN branches b ON b.id=? "
                "WHERE p.id=? AND p.owner=? AND b.owner=?",
                (bid, pid, owner, owner),
                fetch=True
            )
            if product_info:
                low_stock_alerts.append(
                    (product_info[0][1], product_info[0][0], available, available - qty)
                )

    for (pid, bid), qty in required.items():
        run_query(
            "UPDATE branch_stock SET qty=qty-? WHERE owner=? AND branch_id=? AND product_id=?",
            (qty, owner, bid, pid),
            fetch=False
        )

    run_query(
        "UPDATE sales_orders SET status='FINALIZED' WHERE id=? AND owner=? AND status='DRAFT'",
        (order_id, owner),
        fetch=False
    )
    for branch_name, product_name, previous_qty, current_qty in low_stock_alerts:
        notify_low_stock_if_crossed(
            owner, branch_name, product_name, previous_qty, current_qty
        )
    flash("Finalized - stock deducted from branch", "success")
    return redirect(f"/orders/{order_id}")


@app.route("/orders/<int:order_id>/paid", methods=["POST"])
def order_mark_paid(order_id):
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()

    order, _ = run_query(
        "SELECT branch_id,status,payment_status FROM sales_orders WHERE id=? AND owner=?",
        (order_id, owner),
        fetch=True
    )
    if not order or (not is_admin() and (not session.get("branch_id") or int(order[0][0]) != int(session["branch_id"]))):
        flash("Invoice not found", "danger")
        return redirect("/billing")

    if order[0][1] != "FINALIZED":
        flash("Finalize the invoice before recording payment", "danger")
    elif order[0][2] != "PAID":
        run_query(
            "UPDATE sales_orders SET payment_status='PAID' WHERE id=? AND owner=? AND status='FINALIZED'",
            (order_id, owner),
            fetch=False
        )
        flash("Invoice marked as paid", "success")

    return redirect(f"/orders/{order_id}")


@app.route("/reports/sales")
def sales_report():
    if not require_login():
        return redirect("/")
    owner, _ = get_session_owner_bt()
    my_branch = session.get("branch_id")

    from_day = request.args.get("from", (date.today() - timedelta(days=30)).isoformat())
    to_day = request.args.get("to", date.today().isoformat())

    if is_admin():
        totals, _ = run_query(
            "SELECT COALESCE(SUM(s.total),0), COALESCE(SUM(s.profit),0), COUNT(DISTINCT o.id) "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.owner=? AND o.status='FINALIZED' AND o.sale_day BETWEEN? AND?",
            (owner, from_day, to_day),
            fetch=True
        )
        daily, _ = run_query(
            "SELECT o.sale_day, SUM(s.total), SUM(s.profit) "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.owner=? AND o.status='FINALIZED' AND o.sale_day BETWEEN? AND? "
            "GROUP BY o.sale_day ORDER BY o.sale_day",
            (owner, from_day, to_day),
            fetch=True
        )
        top, _ = run_query(
            "SELECT s.product_name, SUM(s.total), SUM(s.profit), SUM(s.quantity) "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.owner=? AND o.status='FINALIZED' AND o.sale_day BETWEEN? AND? "
            "GROUP BY s.product_name ORDER BY SUM(s.total) DESC LIMIT 10",
            (owner, from_day, to_day),
            fetch=True
        )
    else:
        totals, _ = run_query(
            "SELECT COALESCE(SUM(s.total),0), COALESCE(SUM(s.profit),0), COUNT(DISTINCT o.id) "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.owner=? AND s.branch_id=? AND o.status='FINALIZED' AND o.sale_day BETWEEN? AND?",
            (owner, my_branch, from_day, to_day),
            fetch=True
        )
        daily, _ = run_query(
            "SELECT o.sale_day, SUM(s.total), SUM(s.profit) "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.owner=? AND s.branch_id=? AND o.status='FINALIZED' AND o.sale_day BETWEEN? AND? "
            "GROUP BY o.sale_day ORDER BY o.sale_day",
            (owner, my_branch, from_day, to_day),
            fetch=True
        )
        top, _ = run_query(
            "SELECT s.product_name, SUM(s.total), SUM(s.profit), SUM(s.quantity) "
            "FROM sales s JOIN sales_orders o ON o.id=s.order_id "
            "WHERE s.owner=? AND s.branch_id=? AND o.status='FINALIZED' AND o.sale_day BETWEEN? AND? "
            "GROUP BY s.product_name ORDER BY SUM(s.total) DESC LIMIT 10",
            (owner, my_branch, from_day, to_day),
            fetch=True
        )

    if not is_admin():
        report_url = f"/reports/sales?from={quote(from_day)}&to={quote(to_day)}"
        send_admin_push(
            owner,
            "Sales report run",
            f"{session.get('user', 'A staff member')} ran a sales report for {from_day} to {to_day}.",
            report_url
        )

    return render_template(
        "reports_sales.html",
        from_day=from_day,
        to_day=to_day,
        revenue=totals[0][0],
        profit=totals[0][1],
        orders_count=totals[0][2],
        daily=daily,
        top_products=top,
        is_admin=is_admin()
    )


@app.route("/products/<int:product_id>/pricing", methods=["POST"])
def update_product_pricing(product_id):
    if not require_login():
        return redirect("/")
    if not is_admin():
        flash("Only an administrator can set staff discount limits", "danger")
        return redirect("/stock/manage")

    owner, _ = get_session_owner_bt()
    try:
        min_sell_price = float(request.form.get("min_sell_price", ""))
    except (TypeError, ValueError):
        flash("Enter a valid minimum selling price", "danger")
        return redirect("/stock/manage")
    if not math.isfinite(min_sell_price) or min_sell_price < 0:
        flash("Minimum selling price must be zero or greater", "danger")
        return redirect("/stock/manage")

    product, _ = run_query(
        "SELECT sell_price FROM products WHERE id=? AND owner=?",
        (product_id, owner),
        fetch=True
    )
    if not product:
        flash("Product was not found", "danger")
        return redirect("/stock/manage")
    sell_price = float(product[0][0] or 0)
    if min_sell_price > sell_price:
        flash("Minimum selling price cannot be higher than the regular selling price", "danger")
        return redirect("/stock/manage")

    run_query(
        "UPDATE products SET min_sell_price=? WHERE id=? AND owner=?",
        (min_sell_price, product_id, owner),
        fetch=False
    )
    flash("Staff discount limit updated", "success")
    return redirect("/stock/manage")


@app.route("/products/add", methods=["POST"])
def add_product():
    if not require_login():
        return redirect("/")

    owner, bt = get_session_owner_bt()
    name = request.form.get("name", "").strip()[:150]
    category = request.form.get("category", "").strip()[:100]
    try:
        buy_price = float(request.form.get("buy_price", ""))
        sell_price = float(request.form.get("sell_price", ""))
    except (TypeError, ValueError):
        flash("Enter valid buy and sell prices", "danger")
        return redirect("/stock/manage")
    if not name or not category or not math.isfinite(buy_price) or not math.isfinite(sell_price) or buy_price < 0 or sell_price < 0:
        flash("Enter a product name, category, and valid non-negative prices", "danger")
        return redirect("/stock/manage")

    run_query(
        "INSERT INTO products(name,category,unit,quantity,buy_price,sell_price,business_type,owner,barcode,min_sell_price) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            name,
            category,
            "Pcs",
            0,
            buy_price,
            sell_price,
            bt,
            owner,
            request.form.get('barcode', '').strip() or f"BC{int(datetime.now().timestamp())}",
            sell_price
        ),
        fetch=False
    )
    flash("Product added. By default, staff cannot discount it until an admin sets a lower minimum price.", "success")
    return redirect("/stock/manage")


if __name__ == "__main__":
    app.run(debug=False, port=PORT, host="0.0.0.0")
