# Infrastructure

The dev environment runs in docker-compose.

# Services

Postgres (pgvector), Qdrant, MinIO, and a mock Confluence.

## Postgres

The pgvector extension powers the vector index.

## Qdrant

Qdrant serves nearest-neighbour search over embedding collections.

# Networking

Services are exposed on localhost with fixed ports.
