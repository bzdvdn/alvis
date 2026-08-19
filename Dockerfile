# syntax=docker/dockerfile:1

# Winnow as a container. The product surface is YAML, so the image is a
# fixed `winnow` CLI with every optional extra preinstalled — users configure
# it purely by mounting pipeline files and a state volume, never by rebuilding:
#
#   docker build -t winnow:dev .
#   docker run --rm \
#     -v "$PWD/pipeline.yaml:/workspace/pipeline.yaml:ro" \
#     -v "$PWD/.winnow:/workspace/.winnow" \
#     winnow:dev run --incremental pipeline.yaml
#
# State defaults to `.winnow/state.json` under the workdir (`/workspace`),
# which is exactly the mounted volume above.

FROM python:3.12-slim AS builder

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .[documents,pgindex,observability]

FROM python:3.12-slim

COPY --from=builder /usr/local /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN adduser --disabled-password --gecos "" --home /workspace winnow \
    && chown -R winnow:winnow /workspace

USER winnow
WORKDIR /workspace

VOLUME ["/workspace"]

ENTRYPOINT ["winnow"]
CMD ["--help"]