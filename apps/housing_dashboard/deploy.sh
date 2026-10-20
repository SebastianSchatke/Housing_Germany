#!/usr/bin/env bash
# Vendor the shared housing_germany package into the app folder, then deploy.
# Streamlit in Snowflake can't pip-install local code, so the package ships as
# an artifact (see snowflake.yml). The vendored copy is gitignored.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$APP_DIR/../.." && pwd)"

rm -rf "$APP_DIR/housing_germany"
cp -R "$REPO_DIR/src/housing_germany" "$APP_DIR/housing_germany"
find "$APP_DIR/housing_germany" -name "__pycache__" -type d -prune -exec rm -rf {} +

if [[ "${1:-}" == "--vendor-only" ]]; then
  echo "Vendored housing_germany into $APP_DIR"
  exit 0
fi

cd "$APP_DIR"
snow streamlit deploy --replace "$@"
