"""End-to-end checks against a running server.

    python tests/check_api.py            # uses http://127.0.0.1:5000
    BASE_URL=http://host:port python tests/check_api.py
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
failed = 0


def call(method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name, got, expected):
    global failed
    ok = got == expected
    failed += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got {got}, expected {expected}")


# --- reads -----------------------------------------------------------------
s, body = call("GET", "/customers")
check("GET /customers -> 200", s, 200)
check("GET /customers returns >= 10 rows", len(body) >= 10, True)

s, body = call("GET", "/customers?city=Almaty")
check("GET /customers?city=Almaty -> 200", s, 200)
check("city filter works", all(c["city"] == "Almaty" for c in body) and len(body) > 0, True)

s, body = call("GET", "/accounts?customer_id=1")
check("GET /accounts?customer_id=1 -> 200", s, 200)
check("customer 1 has accounts", len(body) >= 1, True)
check("GET /accounts?customer_id=9999 -> 404", call("GET", "/accounts?customer_id=9999")[0], 404)
check("GET /accounts?customer_id=abc -> 400", call("GET", "/accounts?customer_id=abc")[0], 400)

s, body = call("GET", "/transactions?account_id=1")
check("GET /transactions?account_id=1 -> 200", s, 200)
check("GET /transactions?account_id=9999 -> 404", call("GET", "/transactions?account_id=9999")[0], 404)

# --- SQL injection must be harmless --------------------------------------------
inj = urllib.parse.quote("' OR '1'='1")
s, body = call("GET", f"/customers?city={inj}")
check("SQL injection in ?city -> 200 with 0 rows", (s, body), (200, []))
inj2 = urllib.parse.quote("Almaty'; DROP TABLE customers; --")
call("GET", f"/customers?city={inj2}")
check("customers table still exists after DROP attempt", call("GET", "/customers")[0], 200)

# --- POST /customers ---------------------------------------------------------------
import random
mail = f"test{random.randint(0, 10**9)}@example.com"
s, body = call("POST", "/customers", {"full_name": "Test User", "city": "Almaty", "email": mail})
check("POST /customers -> 201", s, 201)
s, body = call("POST", "/customers", {"full_name": "Dup", "email": mail})
check("duplicate email (UniqueViolation) -> 409", s, 409)
s, body = call("POST", "/customers", {"city": "Almaty"})
check("missing full_name (NotNullViolation) -> 400", s, 400)
check("error message names the column", "full_name" in body.get("error", ""), True)
check("invalid JSON body -> 400", call("POST", "/customers", raw=b"not json")[0], 400)

# --- POST /transfer ---------------------------------------------------------------
def balance(acc):
    return next(a for a in call("GET", "/accounts")[1] if a["account_id"] == acc)["balance"]

b1, b2 = balance(1), balance(2)
s, body = call("POST", "/transfer", {"from_account": 1, "to_account": 2, "amount": 100.50})
check("POST /transfer -> 200", s, 200)
check("sender debited", float(balance(1)), float(b1) - 100.50)
check("receiver credited", float(balance(2)), float(b2) + 100.50)
b9, b1_now = balance(9), balance(1)
check("insufficient funds -> 422",
      call("POST", "/transfer", {"from_account": 9, "to_account": 1, "amount": 10**9})[0], 422)
check("sender balance unchanged after failed transfer", balance(9), b9)
check("receiver balance unchanged after failed transfer", balance(1), b1_now)
check("unknown account -> 404",
      call("POST", "/transfer", {"from_account": 1, "to_account": 9999, "amount": 1})[0], 404)
check("same account -> 400",
      call("POST", "/transfer", {"from_account": 1, "to_account": 1, "amount": 1})[0], 400)
check("negative amount -> 400",
      call("POST", "/transfer", {"from_account": 1, "to_account": 2, "amount": -5})[0], 400)
check("3 decimals -> 400",
      call("POST", "/transfer", {"from_account": 1, "to_account": 2, "amount": 1.001})[0], 400)
check("missing field -> 400", call("POST", "/transfer", {"from_account": 1})[0], 400)

s, body = call("GET", "/transactions?account_id=1")
check("transfer visible in history", body[0]["tx_type"], "debit")

# --- health ------------------------------------------------------------------------
s, body = call("GET", "/health")
check("GET /health -> 200", s, 200)
check("health has pool stats", "pool_size" in body.get("pool", {}), True)
print("pool stats:", body.get("pool"))

print("\nALL PASSED" if not failed else f"\n{failed} CHECK(S) FAILED")
sys.exit(1 if failed else 0)
