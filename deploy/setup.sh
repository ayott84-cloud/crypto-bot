#!/usr/bin/env bash
# Crypto Trading Bots — one-shot deployment script for Ubuntu 22.04.
#
# What it does:
#   1. Creates a non-root user `bot` with sudo, locks down SSH
#   2. Installs Python 3.11 + git + build deps + ufw
#   3. Configures firewall (SSH only — dashboard is tunneled via Render)
#   4. Clones the bot repo to /home/bot/crypto-bot
#   5. Creates a venv and installs pinned requirements
#   6. Prompts you to populate .env with WEEX/SMTP creds
#   7. Installs systemd units for the momentum bot + whale bot + dashboard pusher
#   8. Enables + starts services
#   9. Configures logrotate for bot.log
#
# Usage (as root on fresh Ubuntu 22.04 droplet):
#   curl -sSL https://raw.githubusercontent.com/ayott84-cloud/crypto-bot/main/deploy/setup.sh | sudo bash
#
# Or with a non-default branch:
#   BOT_BRANCH=develop curl ... | sudo bash

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ayott84-cloud/crypto-bot.git}"
BOT_BRANCH="${BOT_BRANCH:-main}"
BOT_USER="bot"
BOT_HOME="/home/${BOT_USER}"
BOT_DIR="${BOT_HOME}/crypto-bot"
VENV_DIR="${BOT_DIR}/venv"

echo "==============================================================="
echo "  Crypto Trading Bots — DigitalOcean deployment"
echo "  Repo:   ${REPO_URL}"
echo "  Branch: ${BOT_BRANCH}"
echo "==============================================================="

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this script must be run as root. Use: sudo bash setup.sh"
  exit 1
fi

# ─── 1. System packages ─────────────────────────────────────────────
echo ""
echo "[1/9] Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-pip python3-dev \
  git curl ufw logrotate build-essential \
  ca-certificates tzdata

# ─── 2. Create bot user ─────────────────────────────────────────────
echo ""
echo "[2/9] Creating bot user..."
if ! id -u "${BOT_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${BOT_USER}"
  usermod -aG sudo "${BOT_USER}"
  # Propagate the root user's authorized SSH keys so you can still log in as 'bot'
  mkdir -p "${BOT_HOME}/.ssh"
  if [[ -f /root/.ssh/authorized_keys ]]; then
    cp /root/.ssh/authorized_keys "${BOT_HOME}/.ssh/authorized_keys"
  fi
  chown -R "${BOT_USER}:${BOT_USER}" "${BOT_HOME}/.ssh"
  chmod 700 "${BOT_HOME}/.ssh"
  chmod 600 "${BOT_HOME}/.ssh/authorized_keys" 2>/dev/null || true
  echo "  Created user '${BOT_USER}' with sudo + SSH key access."
else
  echo "  User '${BOT_USER}' already exists — skipping."
fi

# Passwordless sudo for bot — the user has no password (SSH-key-only) so
# sudo would otherwise prompt forever. Idempotent: overwrites on re-run.
echo "${BOT_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${BOT_USER}"
chmod 440 "/etc/sudoers.d/${BOT_USER}"
echo "  Enabled passwordless sudo for '${BOT_USER}'."

# Git author identity — required for the dashboard-push service to make commits
# to the render-dashboard branch. Idempotent.
sudo -u "${BOT_USER}" git config --global user.email "${BOT_USER}@$(hostname)"
sudo -u "${BOT_USER}" git config --global user.name "Crypto Bot Droplet"
echo "  Set git author identity for '${BOT_USER}'."

# ─── 3. SSH hardening ───────────────────────────────────────────────
echo ""
echo "[3/9] Hardening SSH (disabling root login + password auth)..."
SSHD_CONF=/etc/ssh/sshd_config
# Idempotent edits
sed -i -E 's/^#?PermitRootLogin.*/PermitRootLogin no/' "${SSHD_CONF}"
sed -i -E 's/^#?PasswordAuthentication.*/PasswordAuthentication no/' "${SSHD_CONF}"
sed -i -E 's/^#?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' "${SSHD_CONF}"
# Service is `ssh` on Ubuntu 24.04, `sshd` on most others. Reload whichever exists.
if systemctl list-unit-files ssh.service >/dev/null 2>&1; then
  systemctl reload ssh
elif systemctl list-unit-files sshd.service >/dev/null 2>&1; then
  systemctl reload sshd
fi
echo "  SSH hardened: key-only auth, no root login."

# ─── 4. Firewall ────────────────────────────────────────────────────
echo ""
echo "[4/9] Configuring firewall..."
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw --force enable >/dev/null
echo "  ufw: inbound 22 only, all others blocked."

# ─── 5. Clone repo ──────────────────────────────────────────────────
echo ""
echo "[5/9] Cloning bot repository..."
if [[ -d "${BOT_DIR}/.git" ]]; then
  echo "  Repo exists; pulling latest..."
  sudo -u "${BOT_USER}" git -C "${BOT_DIR}" fetch origin
  sudo -u "${BOT_USER}" git -C "${BOT_DIR}" checkout "${BOT_BRANCH}"
  sudo -u "${BOT_USER}" git -C "${BOT_DIR}" pull --ff-only origin "${BOT_BRANCH}"
else
  sudo -u "${BOT_USER}" git clone -b "${BOT_BRANCH}" "${REPO_URL}" "${BOT_DIR}"
fi

# ─── 6. Python venv + deps ──────────────────────────────────────────
echo ""
echo "[6/9] Creating venv and installing Python dependencies..."
sudo -u "${BOT_USER}" python3 -m venv "${VENV_DIR}"
sudo -u "${BOT_USER}" "${VENV_DIR}/bin/pip" install --quiet --upgrade pip wheel
sudo -u "${BOT_USER}" "${VENV_DIR}/bin/pip" install --quiet -r "${BOT_DIR}/requirements.txt"
echo "  Deps installed to ${VENV_DIR}"

# ─── 7. .env template ───────────────────────────────────────────────
echo ""
echo "[7/9] Setting up .env template..."
ENV_FILE="${BOT_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  sudo -u "${BOT_USER}" cp "${BOT_DIR}/.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  chown "${BOT_USER}:${BOT_USER}" "${ENV_FILE}"
  echo "  Created ${ENV_FILE} from template."
  echo ""
  echo "  >>> IMPORTANT: edit ${ENV_FILE} with your real secrets before starting:"
  echo "        sudo -u bot nano ${ENV_FILE}"
  echo ""
  echo "      Required: WEEX_API_KEY, WEEX_API_SECRET, WEEX_API_PASSPHRASE,"
  echo "                SMTP_USER, SMTP_PASS"
  echo ""
else
  echo "  .env already exists — leaving alone."
fi

# ─── 8. systemd units ───────────────────────────────────────────────
echo ""
echo "[8/9] Installing systemd units..."
for unit in crypto-momentum.service crypto-whale.service dashboard-push.service dashboard-push.timer; do
  src="${BOT_DIR}/deploy/${unit}"
  dst="/etc/systemd/system/${unit}"
  if [[ -f "${src}" ]]; then
    cp "${src}" "${dst}"
    echo "  Installed ${unit}"
  else
    echo "  WARNING: ${src} missing — skipping ${unit}"
  fi
done
systemctl daemon-reload

# ─── 9. logrotate ───────────────────────────────────────────────────
echo ""
echo "[9/9] Configuring logrotate..."
if [[ -f "${BOT_DIR}/deploy/logrotate-crypto-bot" ]]; then
  cp "${BOT_DIR}/deploy/logrotate-crypto-bot" /etc/logrotate.d/crypto-bot
  echo "  Installed /etc/logrotate.d/crypto-bot"
fi

# ─── Final instructions ─────────────────────────────────────────────
echo ""
echo "==============================================================="
echo "  Setup complete. Next steps:"
echo "==============================================================="
echo ""
echo "  1. Edit your .env with real credentials:"
echo "       sudo -u bot nano ${ENV_FILE}"
echo ""
echo "  2. Add this droplet's public IP to your WEEX API whitelist."
echo ""
echo "  3. Start the bots:"
echo "       systemctl enable --now crypto-momentum crypto-whale"
echo ""
echo "  4. (Optional) Enable dashboard auto-push to Render branch:"
echo "       see deploy/README.md for the render-dashboard branch setup,"
echo "       then: systemctl enable --now dashboard-push.timer"
echo ""
echo "  5. Watch the logs:"
echo "       journalctl -u crypto-momentum -f"
echo "       journalctl -u crypto-whale -f"
echo ""
echo "  DRY_RUN mode is ON by default (config.py DRY_RUN=True)."
echo "  No real trades will be placed until you flip it."
echo ""
