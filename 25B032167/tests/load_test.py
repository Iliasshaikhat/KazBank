"""Concurrency test: 20+ simultaneous transfers (opposite directions too).

Checks that (1) every request ends with 200/422 and never 500, (2) the total
amount of money in the bank is unchanged (atomicity + isolation), (3) the pool
is reused (pool_size never exceeds max_size=8).

    python tests/load_test.py
"""
import json
import os
import threading
import urllib.error
import urllib.request
from collections import Counter

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def total_money():
    return sum(float(a["balance"]) for a in call("GET", "/accounts")[1])


N = 40
before = total_money()
statuses, lock = [], threading.Lock()


def worker(i):
    # Even workers send 1 -> 2, odd workers send 2 -> 1: the classic deadlock pattern.
    a, b = (1, 2) if i % 2 == 0 else (2, 1)
    s, _ = call("POST", "/transfer", {"from_account": a, "to_account": b, "amount": 10})
    with lock:
        statuses.append(s)


threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
for t in threads: t.start()
for t in threads: t.join()

after = total_money()
stats = call("GET", "/health")[1]["pool"]
print("status codes:", dict(Counter(statuses)))
print("total money before/after:", before, after)
print("pool stats:", stats)
assert all(s in (200, 422) for s in statuses), "unexpected status code (500/503?)"
assert abs(before - after) < 0.001, "money was created or destroyed!"
assert stats["pool_size"] <= 8, "pool grew beyond max_size"
print("LOAD TEST PASSED")
