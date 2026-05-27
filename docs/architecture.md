# Architecture

The initial implementation uses a Python `src` layout, GitHub Actions for scheduling, and Postgres for persistence.

The most important operational rule is that free API limits are handled by deterministic tier-based refresh planning rather than by forcing a full same-day refresh of every symbol.
