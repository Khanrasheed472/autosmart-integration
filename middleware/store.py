"""The middleware's own logbook, one SQLite file.

Every webhook is written here BEFORE we reply to Qiscus. Its stamp
(room id + message id) is the PRIMARY KEY, so the database itself refuses a
second copy in one step, even when two copies arrive at the same instant (R3).
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    dedupe_key   TEXT PRIMARY KEY,
    customer_key TEXT,
    body         TEXT NOT NULL,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    lead_id      TEXT,
    phone_raw    TEXT,
    phone_e164   TEXT,
    note         TEXT,
    received_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    customer_key TEXT PRIMARY KEY,
    lead_id      TEXT NOT NULL,
    last_lead_at TEXT NOT NULL
);
"""

# received -> processing -> sent | needs_attention | unconfirmed | failed | returning_customer
FIELDS = {"status", "attempts", "lead_id", "phone_raw", "phone_e164", "note"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    con = sqlite3.connect(os.getenv("MIDDLEWARE_DB", "middleware.db"), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with db() as con:
        con.executescript(SCHEMA)


def add_event(key: str, customer_key: str, body: str) -> bool:
    """True if this webhook is new, False if we already have it (a redelivery)."""
    with db() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO events (dedupe_key, customer_key, body, status, received_at, updated_at)"
            " VALUES (?, ?, ?, 'received', ?, ?)",
            (key, customer_key, body, now(), now()),
        )
        return cur.rowcount == 1


def claim(key: str, allowed=("received",)):
    """Mark an event as processing, but only if it is in an allowed status.

    One step, so two workers can never both pick up the same event.
    Returns the stored body, or None if someone else has it or it is finished.
    """
    marks = ",".join("?" * len(allowed))
    with db() as con:
        cur = con.execute(
            f"UPDATE events SET status='processing', updated_at=? WHERE dedupe_key=? AND status IN ({marks})",
            (now(), key, *allowed),
        )
        if cur.rowcount != 1:
            return None
        return con.execute("SELECT body FROM events WHERE dedupe_key=?", (key,)).fetchone()["body"]


def update(key: str, **fields):
    fields = {k: v for k, v in fields.items() if k in FIELDS}
    if not fields:
        return
    columns = ", ".join(f"{k}=?" for k in fields)
    with db() as con:
        con.execute(f"UPDATE events SET {columns}, updated_at=? WHERE dedupe_key=?",
                    (*fields.values(), now(), key))


def get(key: str):
    with db() as con:
        row = con.execute("SELECT * FROM events WHERE dedupe_key=?", (key,)).fetchone()
        return dict(row) if row else None


def events(status: str | None = None) -> list[dict]:
    with db() as con:
        if status:
            rows = con.execute("SELECT * FROM events WHERE status=? ORDER BY received_at DESC", (status,))
        else:
            rows = con.execute("SELECT * FROM events ORDER BY received_at DESC")
        return [dict(r) for r in rows]


def keys_with_status(status: str) -> list[str]:
    with db() as con:
        return [r["dedupe_key"] for r in
                con.execute("SELECT dedupe_key FROM events WHERE status=? ORDER BY received_at", (status,))]


def customer_lead(customer_key: str):
    with db() as con:
        row = con.execute("SELECT * FROM customers WHERE customer_key=?", (customer_key,)).fetchone()
        return dict(row) if row else None


def remember_customer(customer_key: str, lead_id: str, contact_at: str):
    with db() as con:
        con.execute(
            "INSERT INTO customers (customer_key, lead_id, last_lead_at) VALUES (?, ?, ?)"
            " ON CONFLICT(customer_key) DO UPDATE SET lead_id=excluded.lead_id, last_lead_at=excluded.last_lead_at",
            (customer_key, lead_id or "", contact_at),
        )


def reset():
    with db() as con:
        con.execute("DELETE FROM events")
        con.execute("DELETE FROM customers")
