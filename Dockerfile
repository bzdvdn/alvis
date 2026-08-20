# syntax=docker/dockerfile:1

# Alvis as a container. The product surface is YAML, so the image is a
# fixed `alvis` CLI with every optional extra preinstalled — users configure
# it purely by mounting pipeline files and a state volume, never by rebuilding:
#
#   docker build -t alvis:dev .
#   docker run --rm \
#     -v "$PWD/pipeline.yaml:/workspace/pipeline.yaml:ro" \
#     -v "$PWD/.alvis:/workspace/.alvis" \
#     alvis:dev run --incremental pipeline.yaml
#
# State defaults to `.alvis/state.json` under the workdir (`/workspace`),
# which is exactly the mounted volume above.

FROM python:3.12-slim AS builder

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .[documents,pgindex,observability]

FROM python:3.12-slim

COPY --from=builder /usr/local /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN adduser --disabled-password --gecos "" --home /workspace alvis \
    && chown -R alvis:alvis /workspace

USER alvis
WORKDIR /workspace

VOLUME ["/workspace"]

ENTRYPOINT ["alvis"]
CMD ["--help"]