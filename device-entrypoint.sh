#!/bin/sh
set -e

sleep 1

if [ "$ZONE" = "admin" ]; then
  echo "Configuring static routes for admin device..."
  ip route add 172.24.0.0/16 via ${ROUTER_ADMIN} dev eth0 onlink || true
  ip route add 172.25.0.0/16 via ${ROUTER_ADMIN} dev eth0 onlink || true

elif [ "$ZONE" = "factory" ]; then
  echo "Configuring static routes for factory device..."
  ip route add 172.26.0.0/16 via ${ROUTER_FACTORY} dev eth0 onlink || true
  ip route add 172.25.0.0/16 via ${ROUTER_FACTORY} dev eth0 onlink || true

elif [ "$ZONE" = "guest" ]; then
  echo "Configuring static routes for guest device..."
  ip route add 172.26.0.0/16 via ${ROUTER_GUEST} dev eth0 onlink || true
  ip route add 172.24.0.0/16 via ${ROUTER_GUEST} dev eth0 onlink || true
fi

echo "Static routes configured:"
ip route

exec "$@"
