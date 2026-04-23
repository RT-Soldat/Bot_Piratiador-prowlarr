#!/bin/sh
set -e
mkdir -p /app/data/registry
chown -R botuser:botuser /app/data
exec gosu botuser "$@"
