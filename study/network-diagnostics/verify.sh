#!/bin/sh
set -eu
cd "$(dirname "$0")"
"${PYTHON:-python3}" -m unittest discover -s tests -v
