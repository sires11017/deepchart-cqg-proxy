# Deepchart CQG Proxy Toolkit

A man-in-the-middle proxy that lets you use **Deepchart** with **CQG** data feeds (AMP demo accounts). Intercepts WebSocket traffic to `demoapi.cqg.com` and forwards it through a local proxy with automatic reconnection.

## How It Works

```
Deepchart ──→ TLS ──→ Bridge MITM (:443) ──→ TLS ──→ CQG (demoapi.cqg.com:443)
                          │
                    Historical Proxy (:12010) ──→ TLS ──→ Real Historical Server
```

The `hosts` file redirects CQG and Deepchart historical domains to your local machine. The bridge listens on port 443, decrypts TLS, identifies the target (CQG real-time vs historical), and forwards accordingly. If CQG disconnects, the proxy reconnects transparently and replays the login/subscription frames — Deepchart doesn't notice.

## Requirements

- **Windows** PC (Deepchart runs on Windows only)
- **Deepchart** already installed
- **CQG demo account** (username/password from your broker)
- **Administrator access** (needed to bind port 443 and edit the hosts file)

No Python installation needed — `start.bat` downloads a portable Python automatically if not found.

## Quick Start

### 🚀 One Click — `start.bat`

**Download ZIP → Extract → Double-click `start.bat`** (accept Admin prompt). That's it.

It will:
1. Resolve real CQG/historical server IPs (before hosts redirect)
2. Find Python or **auto-download a portable copy** if not installed
3. Install required packages
4. Set up the hosts file (redirect CQG domains to localhost)
5. Run the patcher if `patched_run/` hasn't been created yet
6. Apply your saved profiles (templates, workspaces, indicator colors, settings)
7. Start the proxy servers (hidden — no console windows)
8. Launch Deepchart with your saved templates

You see **only** the Deepchart window — no consoles, no Python windows, no bridge windows.

### One-Click Setup (separate)

Right-click **`setup.ps1`** → **Run with PowerShell** (as Administrator). It will:

1. Install Python dependencies
2. Set up the hosts file
3. Run the patcher (copies Deepchart + templates)
4. Trust the MITM certificate

Then use `start.bat` anytime to launch.

## Files

| File | Purpose |
|------|---------|
| `bridge_mitm_proxy.py` | Main proxy — intercepts CQG + historical WebSocket traffic |
| `vol_hist_server.py` | Historical data TCP proxy — forwards to real historical server via TLS |
| `ipc_mitm.py` | Optional IPC monitor between Deepchart and its bridge |
| `setup.ps1` | **One-click installer** — run first |
| `patcher.ps1` | Copies Deepchart files and compiles the custom launcher |
| `Launcher.cs` | C# source for the patched Deepchart.exe (compiled by patcher) |
| `start_servers.ps1` | Launches all servers and Deepchart |
| `fix_hosts.bat` | Adds CQG domains to your hosts file |
| `toggle-proxy-hosts.bat` | Toggle hosts for using other trading software (MW, QT) |
| `run_patched_deepchart.ps1` | Launches Deepchart with bridge (hidden) |
| `deepchart_watchdog.ps1` | Auto-restarts Deepchart if it crashes |
| `start.bat` | **One-click launcher** — double-click and go |
| `Start Deepchart.bat` | Simple batch launcher |
| `run_hidden.ps1` | Utility to launch any process fully hidden |
| `profiles/` | User templates, indicator colors, workspaces, sim accounts, settings |

## Reconnection Behavior

The proxy handles CQG disconnections transparently:

1. CQG closes the WebSocket or sends TCP RST
2. `forward_cqg_to_client` detects the closed connection and exits
3. The main loop detects task exit and calls `upstream.reconnect()`
4. A new TCP+TLS connection to CQG is established
5. The HTTP WebSocket upgrade and initial LOGON/subscription frames are replayed
6. Client data sent during reconnection is buffered and replayed
7. Data flows again — Deepchart never notices

## Switching Between Deepchart and Other Trading Software

Since the hosts file redirects CQG domains to your proxy, other software (MotiveWave, QuantTower) using CQG demo accounts won't work while the proxy is active.

Run **`toggle-proxy-hosts.bat`** as Administrator to remove the hosts entries, then run it again to add them back.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Permission denied" / can't bind port 443 | Run PowerShell **as Administrator** |
| "Python not recognized" | Reinstall Python, check "Add to PATH" |
| "No module named..." | Run `pip install -r requirements.txt` |
| Deepchart connects but no data | Run `start.bat` — it auto-detects your IP and updates hosts |
| Historical data (charts) not loading | The proxy now forwards to the real historical server — check `vol_hist_*.log` |
| CQG keeps disconnecting | The proxy reconnects automatically — check the bridge log at `logs/bridge_mitm_*.log` |
| `patcher.ps1` compilation fails | Install .NET Framework SDK or manually compile `Launcher.cs` with `csc.exe` |
| `Deepchart.exe not found` in patched_run | Run `patcher.ps1` to copy your Deepchart installation |
| Console windows appearing | `start.bat` uses `pythonw.exe` (no-console Python) — ensure pythonw is installed |
| Port 443 already in use | Run `net stop iphlpsvc` as Admin, or check if another program uses port 443 |

## Architecture Notes

- **No console windows** — All Python servers use `pythonw.exe`, VolumetricaBridge launches hidden, the batch script re-launches itself as a hidden worker. Only Deepchart's own window is visible.
- **No hardcoded paths** — Everything works from the project root directory. Python is auto-detected (system PATH, common install locations, or portable download).
- **DNS bypass** — start.bat resolves CQG and historical server IPs using public DNS (8.8.8.8) before modifying the hosts file, so the proxy always connects to the real servers.
- **Certificate SANs** — The MITM certificate covers all intercepted domains: `demoapi.cqg.com`, `api.cqg.com`, `depth-it.historical.deepcharts.com`, `data-b.historical.deepcharts.com`.
- **Your chart colors & profiles** — All indicator templates, workspace layouts, color schemes, sim accounts, and trading account settings are included in `profiles/` and applied automatically on every launch.

## Legal

This repository does **not** distribute Deepchart binaries — you must own a licensed copy of Deepchart and patch it locally via `patcher.ps1`. For educational purposes only. Use with your own CQG account.
