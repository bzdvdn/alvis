# Alvis Overview

Alvis is an ingestion engine: sources → extract → chunk → embed → index.

# Quick Start

Install with pip, then describe a pipeline in YAML and run it.

## Installing

pip install alvis[documents,pgindex]

## First Run

alvis run examples/pgvector.yaml

# Architecture

The pipeline is five stages, each an async protocol.

## Sources

GitHub, GitLab, S3, Confluence, and the filesystem are built in.

## Indexes

Qdrant and pgvector are supported vector backends.
