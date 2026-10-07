#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
/usr/bin/python3 -m unittest discover -s tests
mypy src/ultra_power scripts/install.py
node --input-type=module --check < extension/extension.js
/usr/bin/python3 -m json.tool extension/metadata.json >/dev/null
