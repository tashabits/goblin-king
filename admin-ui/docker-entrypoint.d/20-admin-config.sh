#!/bin/sh
set -eu

cat > /usr/share/nginx/html/admin/config.json <<EOF
{
  "deploymentScope": "${DEPLOYMENT_SCOPE:-docker}",
  "longHelloUrl": "${LONG_HELLO_URL:-http://long-hello:8080}"
}
EOF
