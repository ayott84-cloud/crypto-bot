# Crypto Bots — 24/7 deployment runbook

This directory contains everything needed to run both bots on a DigitalOcean droplet
with a public dashboard on Render.

## What gets deployed

- **crypto-momentum** (systemd) — runs `main.py`, the 4H/1D momentum strategies
- **crypto-whale** (systemd) — runs `whale_main.py`, the Hyperliquid whale tracker
- **dashboard-push.timer** (systemd) — every 10 min, pushes `dashboard.html`
  to the `render-dashboard` branch. Render auto-redeploys on push.

DRY_RUN stays ON (`config.py` `DRY_RUN = True`). No real trades are placed.

---

## One-time setup

### 1. Create the DigitalOcean droplet

1. Log in to https://cloud.digitalocean.com/
2. Click **Create → Droplets**
3. Settings:
   - **Image**: Ubuntu 22.04 (LTS) x64
   - **Plan**: Basic → Regular CPU → **1 GB / 1 vCPU ($6/mo)**
   - **Datacenter**: any — NYC3 or SFO3 are good US defaults
   - **Authentication**: SSH Key (click "New SSH Key" and paste your `~/.ssh/id_ed25519.pub`
     — or use `Password` if you don't have a key, but we'll lock that down)
   - **Hostname**: `crypto-bots`
4. Create. Wait ~60s.
5. Copy the public IP shown on the droplet page.

### 2. Add droplet IP to WEEX whitelist

Go to WEEX API key settings, add the droplet's public IP alongside your home IP.
(Or replace your home IP — once the bot runs on the droplet, you won't need local
WEEX access.)

### 3. Run the setup script

From your local machine (PowerShell or WSL):

```bash
ssh root@<DROPLET-IP>
```

On the droplet:

```bash
curl -sSL https://raw.githubusercontent.com/ayott84-cloud/crypto-bot/main/deploy/setup.sh | sudo bash
```

The script takes ~3-5 minutes. It installs Python, creates the `bot` user,
clones the repo, installs deps, and stages systemd units.

### 4. Fill in `.env` with real credentials

```bash
sudo -u bot nano /home/bot/crypto-bot/.env
```

Required fields (values from your current local `.env`):

```
SMTP_USER=ayott84@gmail.com
SMTP_PASS=<gmail app password>
WEEX_API_KEY=<37-char key>
WEEX_API_SECRET=<64-char secret>
WEEX_API_PASSPHRASE=<11-char passphrase>
TRADING_ENABLED=true
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

### 5. Start the bots

```bash
sudo systemctl enable --now crypto-momentum crypto-whale
sudo systemctl status crypto-momentum crypto-whale
```

You should see both `active (running)`. Tail the logs to confirm they're working:

```bash
sudo journalctl -u crypto-momentum -f
# Ctrl+C, then:
sudo journalctl -u crypto-whale -f
```

---

## Setting up the Render dashboard

### 6. Configure GitHub write access on the droplet

The dashboard-push script needs to push a branch to your GitHub repo. The cleanest
way is an SSH deploy key (scoped to this one repo — zero blast radius).

On the droplet:

```bash
sudo -u bot ssh-keygen -t ed25519 -N "" -f /home/bot/.ssh/id_ed25519 -C "crypto-bot-droplet"
sudo -u bot cat /home/bot/.ssh/id_ed25519.pub
```

Copy the output. Then on GitHub:

1. Go to https://github.com/ayott84-cloud/crypto-bot/settings/keys
2. Click **Add deploy key**
3. Title: `crypto-bot droplet`
4. Key: paste the public key
5. **Check "Allow write access"**
6. Add key

Switch the repo's git remote on the droplet from HTTPS to SSH:

```bash
sudo -u bot git -C /home/bot/crypto-bot remote set-url origin git@github.com:ayott84-cloud/crypto-bot.git
```

Test it works:

```bash
sudo -u bot ssh -T git@github.com
# You should see: "Hi ayott84-cloud/crypto-bot! You've successfully authenticated..."
```

### 7. Seed the `render-dashboard` branch (once)

Wait until at least one bot cycle has run and `dashboard.html` exists
(`ls -la /home/bot/crypto-bot/dashboard.html`). Then trigger the first push
manually to create the branch:

```bash
sudo systemctl start dashboard-push.service
sudo journalctl -u dashboard-push --since "1 minute ago"
```

You should see `Created branch and pushed.` On GitHub, check the branch list
— `render-dashboard` should now exist with `dashboard.html` and `index.html`.

### 8. Enable the 10-min timer

```bash
sudo systemctl enable --now dashboard-push.timer
sudo systemctl list-timers dashboard-push.timer
```

### 9. Create the Render Static Site

1. Go to https://dashboard.render.com/
2. Click **New + → Static Site**
3. Connect your GitHub account and select `ayott84-cloud/crypto-bot`
4. Settings:
   - **Name**: `crypto-dashboard` (or whatever you want — this becomes the URL)
   - **Branch**: `render-dashboard` ← **important, not main**
   - **Root Directory**: (leave blank)
   - **Build Command**: (leave blank — nothing to build)
   - **Publish Directory**: `.` (just a period)
5. Click **Create Static Site**

Render builds in ~30s. You'll get a URL like `https://crypto-dashboard.onrender.com`.

Every 10 min, the droplet force-pushes to `render-dashboard` (if the dashboard
actually changed), and Render auto-redeploys within ~2 min.

---

## Monitoring & operations

### Check bot status
```bash
sudo systemctl status crypto-momentum crypto-whale dashboard-push.timer
```

### Live log tail
```bash
sudo journalctl -u crypto-momentum -f          # momentum bot
sudo journalctl -u crypto-whale -f             # whale bot
sudo journalctl -u dashboard-push --since "1 hour ago"   # dashboard pushes
```

### Restart a bot (e.g. after a config edit)
```bash
sudo systemctl restart crypto-whale
```

### Stop everything
```bash
sudo systemctl stop crypto-momentum crypto-whale dashboard-push.timer
```

### Update to latest code from GitHub
```bash
cd /home/bot/crypto-bot
sudo -u bot git pull
sudo systemctl restart crypto-momentum crypto-whale
```

### Flip to LIVE trading (when you're ready — NOT YET)
```bash
sudo -u bot nano /home/bot/crypto-bot/config.py
# Change DRY_RUN = True  →  DRY_RUN = False
sudo systemctl restart crypto-momentum crypto-whale
```

### Kill switch (pause new entries without stopping the bots)
```bash
sudo -u bot sed -i 's/^TRADING_ENABLED=.*/TRADING_ENABLED=false/' /home/bot/crypto-bot/.env
sudo systemctl restart crypto-whale
# Note: currently only whale bot reads TRADING_ENABLED. Bot 1 would need to stop
# via `systemctl stop crypto-momentum`.
```

---

## Troubleshooting

**Bot crashloops on startup** — check journalctl for the traceback. Most common:
- `.env` missing a value → `sudo -u bot nano /home/bot/crypto-bot/.env`
- WEEX IP not whitelisted → add droplet IP at weex.com account settings

**"Invalid IP address" from WEEX** — droplet IP not in WEEX whitelist. Add it.

**Dashboard never updates on Render** — check the push timer:
```bash
sudo systemctl status dashboard-push.service
sudo journalctl -u dashboard-push --since "30 min ago"
```
If you see `Permission denied (publickey)`, the SSH deploy key isn't set up correctly.

**Out of memory** — unlikely on $6 tier, but if `journalctl -u crypto-momentum`
shows `Killed`, add swap:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## File layout reference

```
/home/bot/crypto-bot/
├── main.py                  # momentum bot entry
├── whale_main.py            # whale bot entry
├── config.py                # DRY_RUN lives here
├── .env                     # secrets (600 perms)
├── dashboard.html           # generated, force-pushed to render-dashboard branch
├── state.json               # shared position state
├── bot.log                  # rotated daily
├── whale_signals.jsonl      # signal audit log
├── venv/                    # Python 3.11 venv
└── deploy/                  # this directory

/etc/systemd/system/
├── crypto-momentum.service
├── crypto-whale.service
├── dashboard-push.service
└── dashboard-push.timer

/etc/logrotate.d/crypto-bot
```
