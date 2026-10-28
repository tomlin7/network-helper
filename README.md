# Network Helper

A small Windows loopback proxy for HTTP/HTTPS applications when the default DNS path returns unusable addresses. Standard-library Python, no TLS interception, bounded resources, and optional sign-in startup.

## Why I built this

I was on my college Ethernet when several apps and development tools started having connectivity trouble. WhatsApp Desktop said there was no internet connection. It looked like a general outage, but testing the individual paths told a more specific story.

Default Windows DNS lookups returned `192.0.2.1` for public sites. Queries sent directly to the college's IPv4 DNS server and Google DNS returned real addresses, and HTTPS requests using those real addresses succeeded. Windows was preferring a link-local IPv6 DNS server configured on Ethernet that returned the unusable address.

There was a separate issue in the command execution environment: `HTTP_PROXY` and `HTTPS_PROXY` pointed to `127.0.0.1:18888`, but no local proxy was listening. **Those were not saved proxy environment variables I had configured on my PC.** Windows proxy was originally disabled, and the saved user proxy variables were absent. We did not establish where the command-only variables came from.

An earlier Python proxy already demonstrated a useful workaround: ask public DNS directly, then connect to the real destination. This helper turns that approach into a bounded, reusable process. Starting it restored GitHub access for the affected command-line tools.

Enabling a Windows-wide proxy was not enough to fix WhatsApp Desktop, so that setting was restored to its original disabled state. We then reset Ethernet's static IPv6 DNS list and flushed the DNS cache. After that troubleshooting and reconnecting, WhatsApp, Edge, Codex, and Spotify were confirmed working. IPv6 itself remained enabled. No additional router-advertisement setting was changed.

That does not prove the college deliberately blocked anything, or identify who configured every DNS entry. Some diagnostic lookups remained inconsistent, so a successful proxy test is not proof that every system DNS issue is permanently gone. The practical lesson was to distinguish a broken resolver path, an unavailable local proxy, and an actual lack of internet connectivity.

I wanted the workaround to survive closing a terminal or signing in again without turning into an unbounded collection of processes. That is what this repo provides.

## How it works

```text
Proxy-aware app -> 127.0.0.1:18888 -> public DNS lookup -> destination
                     |
                  HTTPS CONNECT: encrypted bytes pass through unchanged
```

- Listens only on loopback, never the LAN.
- Resolves public IPv4 addresses through Google DNS, then Cloudflare DNS.
- Rejects placeholder/non-public DNS answers instead of silently falling back to the same broken resolver. Explicit IP targets are supported.
- Supports ordinary HTTP proxy requests and HTTPS CONNECT tunnels.
- Leaves TLS and certificate validation to the client. It does not decrypt HTTPS, install certificates, or disable certificate checks.

## Run on Windows

Requires Python 3.10+ and Windows PowerShell. Clone or download the repo, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-NetworkHelper.ps1
```

Or double-click `Start.cmd`. A healthy existing instance is reused. An unrelated service on port 18888 is left untouched.

Python is discovered from a local uv installation, PATH, or the usual Python installer directory. You can choose a real interpreter explicitly:

```powershell
.\Start-NetworkHelper.ps1 -Python 'C:\path\to\python.exe'
```

Set `NETWORK_HELPER_PYTHON` to its absolute path to retain an override. Avoid the Microsoft Store alias or a launcher that spawns a different interpreter process.

To use the proxy in one PowerShell session without changing saved settings:

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:18888'
$env:HTTPS_PROXY = 'http://127.0.0.1:18888'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
```

### Optional installation

```powershell
.\Install-NetworkHelper.ps1
```

This starts the helper, backs up existing settings, configures user proxy variables with localhost bypasses, and adds a hidden sign-in startup shortcut. Restart already-running applications to refresh inherited variables.

`-WindowsProxy` additionally enables the Windows user proxy. It is optional and does not guarantee compatibility with every desktop application. Try the session-scoped setup first. **The proxy installer does not change adapter DNS settings.**

To run the server in a terminal directly:

```powershell
python .\network_helper.py
```

The Python server also accepts `--port`, but the supplied Windows installation and health scripts use port 18888.

### Status and logs

Status is available at `http://127.0.0.1:18888/health`; access it directly without another proxy. The response includes the PID, connection count and configured limits.

Logs record error categories, not request URLs, headers, credentials or payloads. They rotate at 512 KiB with two backups.

### Undo

Run `Uninstall.cmd` or `Uninstall-NetworkHelper.ps1` to restore backed-up user settings, stop this helper and remove its sign-in shortcut. Files and logs are preserved. Restart existing apps afterward to refresh inherited variables.

Keep the original settings backup: it is specific to the first installation. It is deliberately excluded from Git.

## Resource limits

| Resource | Limit |
| --- | --- |
| Processes | One helper; no per-request workers or child processes |
| Simultaneous connections | 32; excess connections are rejected |
| DNS cache | 2,048 entries; DNS TTL capped at five minutes |
| Request headers | 16 KiB |
| Stream chunks | 32 KiB with backpressure |
| Idle connections | Five minutes |
| Windows Job memory | Hard 256 MiB cap |

Observed working-set memory on the original Windows machine was roughly 25–30 MB. There is no automatic restart or spawn loop. If it exits, run `Start.cmd` and inspect the log.

The hard Windows Job limit is installed before the server starts. On other platforms, the Python server can run but that Windows-specific memory limit does not apply.

## Tests

```powershell
python -m unittest -v test_network_helper
```

The five tests cover DNS response validation and placeholder rejection, health and malformed requests, connection-limit rejection, opaque tunnel forwarding including half-close, and HTTP request rewriting without forwarding proxy credentials.

## Scope

This is a proxy for proxy-aware HTTP/HTTPS applications, not a VPN or a system-wide DNS server. It needs a working route to public DNS and the destination. It cannot restore an unavailable uplink or guarantee that every app honors proxy settings.

Machine-specific DNS repair scripts, settings backups, logs and runtime files are not published in this repo. License: MIT.
