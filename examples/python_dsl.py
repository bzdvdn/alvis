"""Python DSL example - S3 + Qdrant pipeline built entirely from code.

Equivalent to examples/s3-qdrant.yaml, but described in Python:

    python examples/python_dsl.py

Requires the docker-compose stack (MinIO on :9000, Qdrant on :6333):

    docker compose up -d --build
    export MINIO_ACCESS_KEY=minioadmin
    export MINIO_SECRET_KEY=minioadmin
"""

from winnow import describe, dsl, run

config = dsl.pipeline(
    dsl.s3(
        url="http://localhost:9000",
        bucket="winnow",
        access_key_env="MINIO_ACCESS_KEY",
        secret_key_env="MINIO_SECRET_KEY",
        exclude_globs=["**/*.mp4"],
    ),
    chunk=dsl.chunk(max_tokens=80),
    index=dsl.qdrant(url="http://localhost:6333", collection="winnow-s3"),
)

print(describe(config))
print()

result = run(config)
print(
    f"done: {result.documents_ingested} documents, "
    f"{result.chunks_indexed} chunks indexed"
)