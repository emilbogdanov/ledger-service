# ledger-service

A double-entry ledger behind an HTTP API. Clients open accounts, move money with balanced
journal entries, and retry any request safely with an idempotency key. Every change is
published as an event, and a reconciliation command proves the books still add up.

[![ci](https://github.com/emilbogdanov/ledger-service/actions/workflows/ci.yml/badge.svg)](https://github.com/emilbogdanov/ledger-service/actions/workflows/ci.yml)
[![stack](https://github.com/emilbogdanov/ledger-service/actions/workflows/stack.yml/badge.svg)](https://github.com/emilbogdanov/ledger-service/actions/workflows/stack.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Features

- **Accounts** in one of 14 currencies, each with its own overdraft policy: no overdraft, a
  fixed limit, or no floor at all for funding and settlement accounts.
- **Journal entries** of up to 50 postings that must net to zero in every currency they touch.
  A transfer is an entry with two postings.
- **Exactly-once posting.** Every write takes an `Idempotency-Key`. A retry gets the original
  entry back, even when it arrives while the first attempt is still running. Reusing a key for a
  different request is refused.
- **No lost updates, no deadlocks.** Accounts are locked with `SELECT … FOR UPDATE` in id order,
  so concurrent entries over the same accounts queue up instead of deadlocking.
- **Events** (`account.opened`, `entry.posted`) are written to a transactional outbox in the
  same commit as the change, then published to a Redis stream at least once.
- **Reconciliation.** `ledger reconcile` checks every balance against its postings, every entry
  against zero and every posting against its account, on one consistent snapshot, while the
  API keeps serving traffic.
- **Multi-client.** Each API client sees only its own accounts and entries; anything else is a
  404, so ids can't be probed.
- **Observability.** One JSON log line per request, tied to an `X-Request-ID`, and Prometheus
  metrics from the API and the worker.

## Tech stack

Python 3.13, FastAPI, SQLAlchemy 2 (async) with asyncpg, Alembic, PostgreSQL 18, Redis 8
streams, Prometheus client. Tests: pytest, Hypothesis, testcontainers. Tooling: uv, ruff,
mypy (strict), pre-commit, GitHub Actions, Docker Compose.

## Quickstart

```bash
docker compose up --build -d
docker compose exec api ledger create-client --name "Acme Payments"
```

The second command prints an API key (`lk_…`). It is shown once; only its SHA-256 hash is
stored.

```bash
KEY=lk_...   # from the command above
API=http://localhost:8000

# A funding account with no floor, and a wallet that can't go below zero.
curl -s -X POST $API/accounts -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"name": "Bank settlement", "currency": "EUR", "overdraft_limit": null}'
curl -s -X POST $API/accounts -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"name": "Customer wallet", "currency": "EUR"}'

# 25.00 EUR from one to the other. Send it twice: the second answer is 200 with the same entry.
curl -s -X POST $API/transfers -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: top-up-0001" \
  -d '{"source_account_id": "<bank id>", "destination_account_id": "<wallet id>", "amount": 2500, "currency": "EUR"}'
```

Amounts are integers in the currency's minor unit: `2500` EUR is 25.00 EUR, `2500` JPY is
2500 yen. The interactive API docs are at http://localhost:8000/docs.

## API

All endpoints except `/health` and `/metrics` need `Authorization: Bearer <api key>`.

| Method | Path | |
|---|---|---|
| `POST` | `/accounts` | Open an account: `name`, `currency`, `overdraft_limit` (default `0`; `null` for no floor). |
| `GET` | `/accounts/{id}` | Balance, currency and overdraft limit. |
| `PATCH` | `/accounts/{id}` | Change the overdraft limit. Applies to future spending only. |
| `GET` | `/accounts/{id}/postings` | History, newest first. `limit` 1–100 (default 50), `cursor` from the previous page. |
| `POST` | `/transfers` | Move `amount` from one account to another. Needs `Idempotency-Key`. |
| `POST` | `/entries` | Post up to 50 postings that balance per currency. Needs `Idempotency-Key`. |
| `GET` | `/entries/{id}` | An entry with its postings. |

A new entry is `201` with a `Location` header. A retry with the same key and body is `200`
with `Idempotent-Replayed: true` and the original entry.

Every error has the same shape, with a stable `code` to branch on:

```json
{"detail": "account 6a1e… cannot spend 100.01 EUR from a balance of 100.00 EUR", "code": "insufficient_funds"}
```

| Status | Codes |
|---|---|
| 401 | `invalid_api_key` |
| 404 | `account_not_found`, `entry_not_found` |
| 409 | `idempotency_key_conflict` (a third request racing on the same key; retry it) |
| 422 | `invalid_request`, `invalid_value`, `invalid_entry`, `unbalanced_entry`, `currency_mismatch`, `insufficient_funds`, `idempotency_key_reused` |

### Events

The worker publishes to the `ledger:events` stream. Each message has `event_id`, `type`,
`occurred_at` and a JSON `payload`:

```json
{"entry_id": "e2b7…", "client_id": "0b6f…", "memo": "Order 10432",
 "postings": [{"account_id": "6a1e…", "amount": -2499, "currency": "EUR"},
              {"account_id": "d4f8…", "amount": 2499, "currency": "EUR"}]}
```

Delivery is at least once: a batch that was sent but not yet marked as sent goes out again
after a crash. Consumers deduplicate on `event_id`.

### CLI

| Command | |
|---|---|
| `ledger create-client --name NAME` | Register an API client and print its key. |
| `ledger reconcile [--limit N]` | Check the books. Exits `0` when they add up, `2` when they don't, `1` when it couldn't run. |
| `ledger worker` | Publish outbox events until stopped. Backs off while Redis or Postgres is down. |
| `ledger openapi` | Print the OpenAPI schema. |

## How it stays correct

**Money is never a float.** Amounts are integers in minor units, limited to the BIGINT range,
so an overflow is refused in the domain rather than halfway through a transaction.

**Posting an entry** runs in one transaction:

1. Lock every account it touches with `SELECT … FOR UPDATE ORDER BY id`. Ordering the locks is
   what prevents deadlocks between, say, A→B and B→A.
2. Look up the idempotency key. Doing this after the locks means a retry of a request still in
   flight waits for it and then sees its key.
3. Check every posting against its account before moving any balance, so a refused entry
   changes nothing.
4. Write the entry, the key and the outbox event, and commit.

Two different requests can still insert the same key at the same moment. The loser's
`INSERT … ON CONFLICT DO NOTHING` finds the key taken, the service retries once, and the retry
answers as a replay or as `idempotency_key_reused`.

**Tests that would catch a regression:**

- Hypothesis property tests on money arithmetic and entry balancing, plus a stateful test
  that no sequence of entries creates or destroys money.
- A concurrency test that fires 400 random transfers and splits at once against Postgres and
  then reconciles. Locking accounts one at a time in the order a request lists them makes it
  fail with `deadlock detected`; dropping `FOR UPDATE` makes it fail with balances that no
  longer sum to zero.
- Race tests that hold a key in an uncommitted transaction and check both outcomes.
- A migration test that fails if the migrations and the mapped tables ever disagree, and a
  test that the history query is answered from its index without a sort.

## Performance

Measured with `python -m loadtest.run` against `docker compose up` on a 16-thread Windows 11
machine (Docker Desktop), with the load generator on the same machine. 50 wallets, 32
concurrent clients, 30 s per scenario, 10% of transfers retried with the same key. Full output:
[loadtest/RESULTS.md](loadtest/RESULTS.md).

| Scenario | Requests/s | Entries posted/s | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| Transfers between random wallets | 72 | 65 | 374 ms | 818 ms | 2.5 s |
| Every transfer from one hot account | 64 | 58 | 300 ms | 1.4 s | 3.1 s |

After both scenarios the balances summed to zero, there were no unexpected responses,
`ledger reconcile` found nothing, and the outbox had already published every event.

These numbers are from an untuned setup: a single uvicorn process, SQLAlchemy's default pool
of 15 connections shared by 32 clients, and two transactions per transfer (one to check the API
key, one to post: 8 SQL statements besides `BEGIN` and `COMMIT`). Throughput was not the goal
of this version; correctness under concurrency was. Run it yourself with:

```bash
uv run python -m loadtest.run --api-key lk_... --output loadtest/RESULTS.md
```

## Project structure

```
src/ledger/
├── domain/           Money, accounts, overdraft policies, journal entries. No I/O.
├── application/      Use cases and the ports they need (repositories, unit of work, clock,
│                     event publisher, audit). Depends only on the domain.
├── infrastructure/   Postgres repositories and unit of work, Redis stream publisher, clock.
├── api/              FastAPI routes, schemas, the one error-to-HTTP table, logging, metrics.
├── worker.py         Outbox relay loop with backoff.
├── cli.py            create-client, reconcile, worker, openapi.
└── main.py           Wires the pieces together for each process.
migrations/           Alembic revisions.
tests/
├── unit/             Domain and application tests against in-memory fakes.
├── api/              HTTP tests against the app with fakes.
├── integration/      Repositories, services and the worker against real Postgres and Redis.
└── stack/            Tests that drive a running docker compose stack from outside.
loadtest/             The load test and its last recorded results.
```

Dependencies point inwards: the domain imports nothing from the other layers, and the
application layer talks to the database and Redis only through protocols in
`application/ports.py`.

## Configuration

Environment variables, also read from `.env` (see [.env.example](.env.example)):

| Variable | Default | |
|---|---|---|
| `LEDGER_DATABASE_URL` | required | `postgresql+asyncpg://…` |
| `LEDGER_REDIS_URL` | required | `redis://…` |
| `LEDGER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `LEDGER_LOG_FORMAT` | `json` | `json` or `text` |
| `LEDGER_EVENTS_STREAM` | `ledger:events` | Redis stream the worker publishes to |
| `LEDGER_EVENTS_STREAM_MAX_LENGTH` | `1000000` | Approximate cap on the stream's length |
| `LEDGER_OUTBOX_BATCH_SIZE` | `100` | Events per relay pass |
| `LEDGER_OUTBOX_POLL_INTERVAL_SECONDS` | `1.0` | Wait after a pass that found nothing |
| `LEDGER_OUTBOX_MAX_BACKOFF_SECONDS` | `30.0` | Longest wait between retries while Redis or Postgres is down |
| `LEDGER_WORKER_METRICS_PORT` | `9100` | Where the worker serves `/metrics`; `0` turns it off |

## Running tests

```bash
uv sync
uv run pytest
```

The integration tests start Postgres and Redis with testcontainers, so Docker must be running.
To use servers you already have, set `LEDGER_TEST_DATABASE_URL` and `LEDGER_TEST_REDIS_URL`;
the tests drop and recreate the schema and flush Redis, so point them at nothing you want to
keep.

The stack tests skip unless `LEDGER_STACK_API_KEY` is set. The `stack` CI job sets it after
starting everything with Docker Compose, then also runs `ledger reconcile`, checks that the
worker stops cleanly and that every log line is JSON.

`uv run ruff check`, `uv run ruff format --check` and `uv run mypy` run in CI on every push,
and in pre-commit (`uv run pre-commit install`).

## Operating it

- **Alert on** `ledger_outbox_last_success_timestamp_seconds` going stale. It also catches a
  worker that has hung or stopped, which a failure counter would not.
- **Schedule** `ledger reconcile` and alert on exit code 2. It reads one snapshot and is safe
  to run against a live database.
- **Keep `/metrics` off the public network.** It has no API key, like most scrape targets.
- **Scale** the API by running more replicas, and the worker as well: relays claim events with
  `FOR UPDATE SKIP LOCKED`, so they share the outbox without sending the same batch twice at
  once. With more than one relay, events can reach the stream out of order.

## Limitations

- No currency conversion: an FX entry is accepted when the caller supplies both legs, but the
  ledger knows no rates.
- Accounts can't be closed and API keys can't be rotated or revoked yet.
- Published outbox rows are kept forever; there is no cleanup job.
- Performance is untuned (see above).

## License

[MIT](LICENSE)
