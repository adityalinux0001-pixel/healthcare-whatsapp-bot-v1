#!/usr/bin/env bash
# One-time setup on a fresh Oracle Cloud "Always Free" Ubuntu VM.
set -euo pipefail

sudo apt-get update && sudo apt-get upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"

# Oracle's default iptables setup blocks inbound traffic — open 80/443 for Caddy.
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

echo "Docker installed. Log out and back in for the group change to apply, then:"
echo "  git clone <your-repo-url> && cd <your-repo>"
echo "  cp .env.example .env   # fill in real secrets"
echo "  edit Caddyfile          # replace yourdomain.com with your real domain"
echo "  docker compose up -d --build"
