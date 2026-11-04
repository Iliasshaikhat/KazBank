"""Create the KazBank schema and seed it. Run:  python schema.py [--reset]

--reset drops the three tables first (use it to start from scratch).
Seeding only happens when the customers table is empty, so the script is safe
to run more than once.
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    full_name   TEXT NOT NULL,
    city        TEXT DEFAULT 'Almaty',
    email       TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id     SERIAL PRIMARY KEY,
    customer_id    INTEGER NOT NULL REFERENCES customers(customer_id),
    account_number TEXT NOT NULL UNIQUE,
    balance        NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id              SERIAL PRIMARY KEY,
    account_id         INTEGER NOT NULL REFERENCES accounts(account_id),
    amount             NUMERIC(15,2) NOT NULL CHECK (amount > 0),
    tx_type            TEXT NOT NULL CHECK (tx_type IN ('debit', 'credit')),
    related_account_id INTEGER REFERENCES accounts(account_id),
    description        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_accounts_customer     ON accounts(customer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account  ON transactions(account_id);
"""

SEED_CUSTOMERS = [
    ("Arman Bekov",      "Almaty",     "arman.bekov@kazbank.kz"),
    ("Zarina Seiit",     "Astana",     "zarina.seiit@kazbank.kz"),
    ("Daniyar Nurlan",   "Shymkent",   "daniyar.nurlan@kazbank.kz"),
    ("Aigerim Kassym",   "Almaty",     "aigerim.kassym@kazbank.kz"),
    ("Timur Abenov",     "Karaganda",  "timur.abenov@kazbank.kz"),
    ("Madina Omarova",   "Almaty",     "madina.omarova@kazbank.kz"),
    ("Nurzhan Tleu",     "Aktobe",     "nurzhan.tleu@kazbank.kz"),
    ("Dinara Sadykova",  "Astana",     "dinara.sadykova@kazbank.kz"),
    ("Yerlan Zhaksylyk", "Pavlodar",   "yerlan.zhaksylyk@kazbank.kz"),
    ("Saltanat Ibraim",  "Almaty",     "saltanat.ibraim@kazbank.kz"),
]

# 15 accounts: (index of owner in SEED_CUSTOMERS, opening balance)
SEED_ACCOUNTS = [
    (0, 500000), (1, 250000), (2, 120000), (3, 980000), (4, 45000),
    (5, 310000), (6, 75000),  (7, 640000), (8, 15000),  (9, 220000),
    (0, 100000), (3, 400000), (5, 60000),  (7, 90000),  (9, 5000),
]


def main(reset: bool = False) -> None:
    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS transactions, accounts, customers CASCADE")
        conn.execute(DDL)

        already = conn.execute("SELECT count(*) FROM customers").fetchone()[0]
        if already:
            conn.commit()
            print(f"Schema ready. customers already has {already} rows, seed skipped.")
            return

        cur = conn.cursor()   # executemany() lives on the cursor, not on the connection
        cur.executemany(
            "INSERT INTO customers (full_name, city, email) VALUES (%s, %s, %s)",
            SEED_CUSTOMERS,
        )
        ids = [r[0] for r in conn.execute(
            "SELECT customer_id FROM customers ORDER BY customer_id").fetchall()]

        accounts = [
            (ids[owner], f"KZ86{n:012d}", balance)
            for n, (owner, balance) in enumerate(SEED_ACCOUNTS, start=1)
        ]
        cur.executemany(
            "INSERT INTO accounts (customer_id, account_number, balance) VALUES (%s, %s, %s)",
            accounts,
        )
        # opening-balance history so GET /transactions is not empty
        conn.execute(
            """INSERT INTO transactions (account_id, amount, tx_type, description)
               SELECT account_id, balance, 'credit', 'Opening balance'
               FROM accounts WHERE balance > 0"""
        )
        conn.commit()
        print(f"Schema created. Seeded {len(SEED_CUSTOMERS)} customers "
              f"and {len(SEED_ACCOUNTS)} accounts.")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
