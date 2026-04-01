#!/usr/bin/env bash
set -euo pipefail

CONTROL_PLANE_PUBLIC_DOMAIN="${CONTROL_PLANE_PUBLIC_DOMAIN:?CONTROL_PLANE_PUBLIC_DOMAIN is required}"
CONTROL_PLANE_INTERNAL_HOST="${CONTROL_PLANE_INTERNAL_HOST:-127.0.0.1}"
CONTROL_PLANE_INTERNAL_PORT="${CONTROL_PLANE_INTERNAL_PORT:-8787}"
CONTROL_PLANE_CADDY_EMAIL="${CONTROL_PLANE_CADDY_EMAIL:-}"
CONTROL_PLANE_CADDYFILE_PATH="${CONTROL_PLANE_CADDYFILE_PATH:-/etc/caddy/Caddyfile}"

if ! command -v caddy >/dev/null 2>&1; then
  apt-get update
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

mkdir -p "$(dirname "${CONTROL_PLANE_CADDYFILE_PATH}")"

if [[ -n "${CONTROL_PLANE_CADDY_EMAIL}" ]]; then
  cat > "${CONTROL_PLANE_CADDYFILE_PATH}" <<EOF
{
	email ${CONTROL_PLANE_CADDY_EMAIL}
}

${CONTROL_PLANE_PUBLIC_DOMAIN} {
	encode zstd gzip
	reverse_proxy ${CONTROL_PLANE_INTERNAL_HOST}:${CONTROL_PLANE_INTERNAL_PORT}
}
EOF
else
  cat > "${CONTROL_PLANE_CADDYFILE_PATH}" <<EOF
${CONTROL_PLANE_PUBLIC_DOMAIN} {
	encode zstd gzip
	reverse_proxy ${CONTROL_PLANE_INTERNAL_HOST}:${CONTROL_PLANE_INTERNAL_PORT}
}
EOF
fi

systemctl enable caddy
systemctl restart caddy
systemctl --no-pager --full status caddy
