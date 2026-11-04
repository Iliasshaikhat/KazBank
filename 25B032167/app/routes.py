"""All HTTP endpoints."""
import random
import time
from decimal import Decimal, InvalidOperation

import psycopg
from flask import Blueprint, jsonify, request
from psycopg import IsolationLevel
from psycopg import errors as pg

from . import db
from .errors import ApiError

bp = Blueprint("api", __name__)


# ---------------------------------------------------------------- helpers
def _optional_int(name: str):
    """Read an optional integer query parameter; 400 if it is not an integer."""
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ApiError(f"Query parameter '{name}' must be an integer", 400)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object", 400)
    return data


def _int_field(data: dict, name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(f"Field '{name}' is required and must be an integer", 400)
    return value


def _amount_field(data: dict) -> Decimal:
    value = data.get("amount")
    if value is None or isinstance(value, bool):
        raise ApiError("Field 'amount' is required", 400)
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ApiError("Field 'amount' must be a number", 400)
    if not amount.is_finite() or amount <= 0:
        raise ApiError("Field 'amount' must be a positive number", 400)
    if amount.as_tuple().exponent < -2:
        raise ApiError("Field 'amount' can have at most 2 decimal places", 400)
    return amount


# ---------------------------------------------------------------- customers
@bp.get("/customers")
def list_customers():
    city = request.args.get("city")          # None if not provided
    sql = "SELECT customer_id, full_name, city, email FROM customers"
    args: tuple = ()
    if city:
        sql += " WHERE city = %s"            # parameterised: no SQL injection
        args = (city,)
    sql += " ORDER BY customer_id"
    with db.pool.connection() as conn:
        rows = conn.execute(sql, args).fetchall()
    return jsonify(rows)


@bp.post("/customers")
def create_customer():
    data = _json_body()
    name = data.get("full_name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ApiError("Field 'full_name' must be a non-empty string", 400)
    # A missing full_name goes to the DB as NULL -> NotNullViolation -> 400.
    with db.pool.connection() as conn:
        try:
            row = conn.execute(
                """INSERT INTO customers (full_name, city, email)
                   VALUES (%s, COALESCE(%s, 'Almaty'), %s)
                   RETURNING customer_id, full_name, city, email""",
                (name, data.get("city"), data.get("email")),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return jsonify(row), 201


# ---------------------------------------------------------------- accounts
@bp.get("/accounts")
def list_accounts():
    customer_id = _optional_int("customer_id")
    with db.pool.connection() as conn:
        if customer_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM customers WHERE customer_id = %s", (customer_id,)
            ).fetchone()
            if not exists:
                raise ApiError(f"Customer {customer_id} not found", 404)
            rows = conn.execute(
                """SELECT account_id, customer_id, account_number, balance
                   FROM accounts WHERE customer_id = %s ORDER BY account_id""",
                (customer_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT account_id, customer_id, account_number, balance
                   FROM accounts ORDER BY account_id"""
            ).fetchall()
    return jsonify(rows)


# ---------------------------------------------------------------- transfer
RETRYABLE = (pg.SerializationFailure, pg.DeadlockDetected)


def with_retry(fn, max_retries: int = 5):
    """Re-run `fn` when PostgreSQL aborts it with 40001 / 40P01.

    SERIALIZABLE transactions may fail with serialization_failure even though
    nothing is wrong; the application is expected to simply try again.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except RETRYABLE:
            if attempt == max_retries - 1:
                raise
            time.sleep(0.05 * (2 ** attempt) * (0.5 + random.random()))  # backoff + jitter


def _transfer_once(from_id: int, to_id: int, amount: Decimal) -> dict:
    with db.pool.connection() as conn:
        conn.isolation_level = IsolationLevel.SERIALIZABLE
        try:
            # 1. Lock both rows ALWAYS in ascending id order -> no deadlocks.
            balances = {}
            for acc_id in sorted((from_id, to_id)):
                row = conn.execute(
                    "SELECT balance FROM accounts WHERE account_id = %s FOR UPDATE",
                    (acc_id,),
                ).fetchone()
                if row is None:
                    raise ApiError(f"Account {acc_id} not found", 404)
                balances[acc_id] = row["balance"]

            # 2. Business rule: enough money?
            if balances[from_id] < amount:
                raise ApiError("Insufficient funds", 422)

            # 3. Move the money (both UPDATEs or none).
            new_from = conn.execute(
                "UPDATE accounts SET balance = balance - %s WHERE account_id = %s RETURNING balance",
                (amount, from_id),
            ).fetchone()["balance"]
            new_to = conn.execute(
                "UPDATE accounts SET balance = balance + %s WHERE account_id = %s RETURNING balance",
                (amount, to_id),
            ).fetchone()["balance"]

            # 4. Audit trail: one debit row and one credit row.
            debit = conn.execute(
                """INSERT INTO transactions (account_id, amount, tx_type, related_account_id, description)
                   VALUES (%s, %s, 'debit', %s, 'Transfer out') RETURNING tx_id""",
                (from_id, amount, to_id),
            ).fetchone()["tx_id"]
            credit = conn.execute(
                """INSERT INTO transactions (account_id, amount, tx_type, related_account_id, description)
                   VALUES (%s, %s, 'credit', %s, 'Transfer in') RETURNING tx_id""",
                (to_id, amount, from_id),
            ).fetchone()["tx_id"]

            conn.commit()      # may itself raise SerializationFailure
        except Exception:
            conn.rollback()    # atomicity: neither UPDATE persists
            raise
        finally:
            # The connection goes back to the shared pool: restore default level.
            if not conn.closed and conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
                conn.isolation_level = None

    return {
        "status": "ok",
        "from_account": from_id,
        "to_account": to_id,
        "amount": amount,
        "from_balance": new_from,
        "to_balance": new_to,
        "transaction_ids": [debit, credit],
    }


@bp.post("/transfer")
def transfer():
    data = _json_body()
    from_id = _int_field(data, "from_account")
    to_id = _int_field(data, "to_account")
    amount = _amount_field(data)
    if from_id == to_id:
        raise ApiError("Cannot transfer to the same account", 400)

    try:
        result = with_retry(lambda: _transfer_once(from_id, to_id, amount))
    except RETRYABLE:
        raise ApiError("Too much contention, please retry", 503)
    return jsonify(result), 200


# ---------------------------------------------------------------- transactions
@bp.get("/transactions")
def list_transactions():
    account_id = _optional_int("account_id")
    with db.pool.connection() as conn:
        if account_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM accounts WHERE account_id = %s", (account_id,)
            ).fetchone()
            if not exists:
                raise ApiError(f"Account {account_id} not found", 404)
            rows = conn.execute(
                """SELECT tx_id, account_id, amount, tx_type, related_account_id,
                          description, created_at
                   FROM transactions WHERE account_id = %s
                   ORDER BY tx_id DESC LIMIT 100""",
                (account_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT tx_id, account_id, amount, tx_type, related_account_id,
                          description, created_at
                   FROM transactions ORDER BY tx_id DESC LIMIT 100"""
            ).fetchall()
    return jsonify(rows)


# ---------------------------------------------------------------- health
@bp.get("/health")
def health():
    try:
        db.pool.check()
        with db.pool.connection() as conn:
            conn.execute("SELECT 1")
        return jsonify({"status": "ok", "pool": db.pool.get_stats()})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 503
