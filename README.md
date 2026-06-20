# Deepchart CQG Proxy Toolkit

A man-in-the-middle proxy that lets you use **Deepchart** with **CQG** data feeds (AMP demo accounts). Intercepts WebSocket traffic to `demoapi.cqg.com` and forwards it through a local proxy with automatic reconnection.

## How It Works

```
Deepchart ──→ Local Proxy (:443) ──→ CQG (demoapi.cqg.com:443)
                   │
              Vol Hist Server (:12010)
```

The `hosts` file redirects `demoapi.cqg.com` and `api.cqg.com` to your local machine. The proxy listens on port 443, decrypts TLS traffic, and forwards WebSocket frames between Deepchart and CQG. If CQG disconnects (TCP RST), the proxy reconnects transparently and replays the initial login/subscription frames — Deepchart doesn't notice.

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
1. Find Python or **auto-download a portable copy** if not installed
2. Install required packages
3. Set up the hosts file (redirect CQG domains to localhost)
4. Run the patcher if `patched_run/` hasn't been created yet
5. Start the proxy servers
6. Launch Deepchart with your saved templates

Just close the window to stop everything.

### One-Click Setup (separate)

If you want to run setup without launching:

Right-click **`setup.ps1`** → **Run with PowerShell** (as Administrator). It will:

1. Install Python dependencies
2. Set up the hosts file
3. Run the patcher (copies Deepchart + templates)
4. Trust the MITM certificate

Then use `start.bat` anytime to launch.

### Manual Setup

**1. Install Python packages**
```powershell
pip install -r requirements.txt
```

**2. Redirect CQG domains to your machine**
Right-click **`fix_hosts.bat`** → Run as Administrator. Or manually add to hosts:
```
192.168.x.x  demoapi.cqg.com
192.168.x.x  api.cqg.com
192.168.x.x  depth-it.historical.deepcharts.com
192.168.x.x  data-b.historical.deepcharts.com
```

**3. Patch Deepchart**
```powershell
.\patcher.ps1 -DeepchartPath "C:\Program Files\Deepchart"
```
This copies Deepchart files into `patched_run/`, compiles the custom launcher, copies profiles (templates, workspaces, settings), and applies roaming config.

**4. Start the servers**
```powershell
# Terminal 1: CQG Proxy
python bridge_mitm_proxy.py

# Terminal 2: Historical Data Server
python vol_hist_server.py

# Terminal 3: Launch Deepchart
.\run_patched_deepchart.ps1
```

**5. Configure Deepchart**
- Go to Connections → Add New
- Data Feed: **CQG**
- Check **Use demo credentials**
- Enter your AMP CQG demo account details

## Files

| File | Purpose |
|------|---------|
| `bridge_mitm_proxy.py` | Main proxy — intercepts CQG WebSocket traffic |
| `vol_hist_server.py` | Mock historical data server (responds to chart requests) |
| `ipc_mitm.py` | Optional IPC monitor between Deepchart and its bridge |
| `setup.ps1` | **One-click installer** — run first |
| `patcher.ps1` | Copies Deepchart files and compiles the custom launcher |
| `Launcher.cs` | C# source for the patched Deepchart.exe (compiled by patcher) |
| `start_servers.ps1` | Launches all servers and Deepchart |
| `fix_hosts.bat` | Adds CQG domains to your hosts file |
| `toggle-proxy-hosts.bat` | Toggle hosts for using other trading software (MW, QT) |
| `run_patched_deepchart.ps1` | Launches Deepchart with bridge |
| `deepchart_watchdog.ps1` | Auto-restarts Deepchart if it crashes |
| `start.bat` | **One-click launcher** — double-click and go |
| `Start Deepchart.bat` | Simple batch launcher |
| `profiles/` | User templates, workspaces, indicator configs, settings |

## Reconnection Behavior

The proxy handles CQG disconnections transparently:

1. CQG sends TCP RST or closes the WebSocket
2. `forward_cqg_to_client` detects the closed connection and exits
3. `handle()` detects the task completion and calls `upstream.reconnect()`
4. A new TCP+TLS connection to CQG is established
5. The HTTP WebSocket upgrade and initial LOGON/subscription frames are replayed from the capture buffer
6. Data flows again — Deepchart never notices

## Switching Between Deepchart and Other Trading Software

Since the hosts file redirects CQG domains to your proxy, other software (MotiveWave, QuantTower) using CQG demo accounts won't work while the proxy is active.

Run **`toggle-proxy-hosts.bat`** as Administrator to remove the hosts entries, then run it again to add them back.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Permission denied" / can't bind port 443 | Run PowerShell **as Administrator** |
| "Python not recognized" | Reinstall Python, check "Add to PATH" |
| "No module named..." | Run `pip install -r requirements.txt` |
| Deepchart connects but no data | Update hosts file with your current IP (`ipconfig`) |
| CQG keeps disconnecting | The proxy reconnects automatically — check the bridge log |
| `patcher.ps1` compilation fails | Install .NET Framework SDK or manually compile `Launcher.cs` with `csc.exe` |
| `Deepchart.exe not found` in patched_run | Run `patcher.ps1` to copy your Deepchart installation |

## Legal

This repository does **not** distribute Deepchart binaries — you must own a licensed copy of Deepchart and patch it locally via `patcher.ps1`. For educational purposes only. Use with your own CQG account.
