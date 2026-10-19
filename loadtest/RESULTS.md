# Load test results

Measured 2026-10-19 08:00 UTC with `python -m loadtest.run` against `docker compose up`: one API process, one worker, Postgres and Redis on the same machine as the load generator.

- Machine: Windows 11, 16 logical CPUs
- 50 wallets, 32 concurrent clients, 30 s per scenario, 10% of transfers retried with the same key

| Scenario | Requests | Requests/s | Entries posted/s | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| spread | 2168 | 72 | 65 | 373.8 | 818.4 | 2520.5 |
| hot account | 1948 | 64 | 58 | 300.4 | 1437.5 | 3127.0 |

Responses:

- spread (random transfers between wallets; contention only when two pick the same account): 187 x 200, 1981 x 201
- hot account (every transfer pays out of the one funding account, so its row lock serializes them): 180 x 200, 1768 x 201

Checks after the run:

- Sum of every balance the test touched: 0 (must be 0)
- Unexpected responses: 0 (must be 0)
- Outbox drained 0.0 s after the last request
