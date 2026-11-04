# KazBank Transaction API

REST API (Flask + psycopg3 + PostgreSQL) for CSCI2204 Database Management Systems, KBTU Fall 2026.

Customers own accounts; money moves between accounts through an atomic, serializable
`POST /transfer`; every movement is written to `transactions`.

## Requirements
Python 3.10+, PostgreSQL 15+.

## Run

```bash
# 1. environment
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. configuration (never commit .env)
cp .env.example .env                # then edit DATABASE_URL

# 3. database: create an empty DB once, then create schema + seed from Python
createdb kazbank                    # or: CREATE DATABASE kazbank; in psql
python schema.py                    # python schema.py --reset  -> drop and recreate

# 4. start the server (http://127.0.0.1:5000)
python run.py
```

## Tests
With the server running, in another terminal:

```bash
python tests/check_api.py           # every endpoint + every error path + SQL injection
python tests/load_test.py           # 40 concurrent opposite-direction transfers
```

## Endpoints

| Method | Path | Description | Codes |
|---|---|---|---|
| GET | `/customers[?city=X]` | list / filter customers | 200 |
| POST | `/customers` | create customer `{full_name, city?, email?}` | 201, 400, 409 |
| GET | `/accounts[?customer_id=n]` | accounts (of one customer) | 200, 400, 404 |
| POST | `/transfer` | `{from_account, to_account, amount}` | 200, 400, 404, 422, 503 |
| GET | `/transactions[?account_id=n]` | last 100 transactions | 200, 400, 404 |
| GET | `/health` | DB reachability + pool statistics | 200, 503 |

Error mapping: `UniqueViolation` -> 409, `NotNullViolation` -> 400,
`OperationalError` / `PoolTimeout` -> 503, `ForeignKeyViolation` -> 404,
`CheckViolation` -> 422. Errors are always `{"error": "..."}`.

## Sample curl commands

```bash
curl localhost:5000/customers
curl "localhost:5000/customers?city=Almaty"
curl -X POST localhost:5000/customers -H "Content-Type: application/json" \
     -d '{"full_name": "Aliya Nur", "city": "Almaty", "email": "aliya@example.com"}'
curl -X POST localhost:5000/customers -H "Content-Type: application/json" \
     -d '{"full_name": "Aliya Nur", "email": "aliya@example.com"}'      # 409 duplicate email
curl -X POST localhost:5000/customers -H "Content-Type: application/json" \
     -d '{"city": "Almaty"}'                                            # 400 missing full_name
curl "localhost:5000/accounts?customer_id=1"
curl -X POST localhost:5000/transfer -H "Content-Type: application/json" \
     -d '{"from_account": 1, "to_account": 2, "amount": 1500.50}'
curl -X POST localhost:5000/transfer -H "Content-Type: application/json" \
     -d '{"from_account": 9, "to_account": 1, "amount": 99999999}'      # 422 insufficient funds
curl "localhost:5000/transactions?account_id=1"
curl localhost:5000/health
# SQL injection attempt: returns [] and the table is untouched
curl -G localhost:5000/customers --data-urlencode "city=' OR '1'='1"
```

## Design notes

- **Pool** (`app/db.py`): one `ConnectionPool(min=2, max=8)` created at startup in
  `create_app()`; handlers borrow connections with `with db.pool.connection()`.
  `timeout=5` so an exhausted pool returns 503 instead of hanging.
- **Atomic transfer** (`app/routes.py::_transfer_once`): both `UPDATE`s and both
  `INSERT`s are in one transaction; any exception triggers `conn.rollback()`.
  `CHECK (balance >= 0)` in the schema is a second line of defence.
- **SERIALIZABLE + retry**: the transfer runs at `SERIALIZABLE`; `with_retry()` repeats it on
  `SerializationFailure` (40001) and `DeadlockDetected` (40P01) with exponential back-off.
  The isolation level is reset before the connection returns to the pool so it cannot leak
  into other requests.
- **No deadlocks**: accounts are locked with `SELECT ... FOR UPDATE` in ascending `account_id`
  order, regardless of transfer direction.
- **Money** uses `NUMERIC(15,2)` and Python `Decimal` (never `float`); JSON returns amounts as strings.
- **Injection-safe**: every query uses `%s` placeholders; no string formatting of SQL with user input.
