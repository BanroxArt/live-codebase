# Benchmarks

Run them yourself:

```
make bench
```

or `PYTHONPATH=src python bench/run.py`. The script uses `timeit` from the
standard library and reports the best of five repeats, so the numbers are a
floor for one CPU core, not a throughput promise. Compare runs on the same
machine only.

Last recorded run, Nov 14, 2026, on a shared cloud VM (Intel Xeon, 1 core in use):

Python 3.13.5 on x86_64 (linux)

| case | µs/op | ops/s |
|---|---:|---:|
| audit append (HMAC chain, SQLite in memory) | 12.09 | 82,679 |
| audit verify, 10,000-row chain | 61,136.53 | 16 |
| flag evaluate (25% rollout) | 1.33 | 749,776 |
| rollout bucket hash | 0.54 | 1,835,356 |
| bearer token authenticate | 4.68 | 213,667 |
| histogram observe | 0.57 | 1,745,126 |
