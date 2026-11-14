#!/bin/sh
# Used by `dfir-lab check` (check 10). Writes the SHA-256 of sample.txt to
# analysis/readiness.txt so the checker can confirm the command really ran.
mkdir -p analysis
sha256sum sample.txt > analysis/readiness.txt
echo "readiness result written to analysis/readiness.txt"
