# dev-stack

`dev-stack` is our private remote-working infrastructure for development
machines. It connects services such as Code Server, Porterminal, File Browser,
and go2rtc behind one management layer and makes them available securely over
Tailscale. From another device on the tailnet, we can develop code on a machine,
open a terminal, browse files, inspect camera streams, and monitor or manage the
services that provide those capabilities.

The `dev_stack` CLI is the control plane for service lifecycle, credentials,
status, and Tailscale publication. Its authenticated dashboard provides a
single view of the machine and a deliberately limited set of remote operations.
The frontend has no build step, while the Python-standard-library backend
delegates service truth to `dev_stack status --json`.

The default local port is **1080**. The server binds to loopback and is published
privately at `/dev-stack` with Tailscale Serve unless `--tailscale-no-serve` is
used. It is not configured to start at boot.

## Setting up a fresh machine

On a brand-new Ubuntu 24.04 (Noble) host, `bootstrap.sh` installs everything
dev-stack needs and then installs the checkout. Ubuntu 24.04 ships without
`git`, so install it before cloning:

```bash
sudo apt-get update && sudo apt-get install --yes git
git clone https://github.com/mpkuse/dev-stack.git
cd dev-stack
./bootstrap.sh
```

`bootstrap.sh` runs the installers in `scripts/` in dependency order, then hands
off to `init.sh`:

| Step | Action | Detail |
| --- | --- | --- |
| 1 | `scripts/install_prerequisites.sh` | apt runtime dependencies, `git`, `zellij`, Docker Engine + Compose, `python3-qrcode` |
| 2 | `scripts/install_tailscale.sh` | only when Tailscale is not already up. **Interactive**: prints a URL to authenticate the machine |
| 3 | `tailscale set --operator` | only when no operator is recorded |
| 4 | `scripts/install_dev_stack_tools.sh` | code-server, File Browser, Porterminal |
| 5 | shell environment | `PATH` and `DEV_STACK_PORTERMINAL_COMMAND` in `~/.bashrc` and `~/.profile` |
| 6 | `./init.sh --prefix PATH` | the checkout itself, plus the readiness report |
| 7 | `dev_stack admin password reset` | only when no master password is set. **Interactive**, and skipped when stdin is not a terminal |

Every step is idempotent, so re-running on a configured host changes nothing.
Steps can be skipped with `--skip-prerequisites`, `--skip-tailscale`,
`--skip-tools`, `--skip-shell-env`, `--no-init` and `--skip-admin-password`;
`--prefix` is passed through to `init.sh`. Each script can also be run on its
own, in the order above.

Requires `sudo` for steps 1-4. Steps 2 and 7 need a terminal.

Step 5 appends to `~/.bashrc` and `~/.profile`. It is the only step that edits
files outside the prefix, it is guarded so a second run does not append twice,
and `--skip-shell-env` opts out.

`bootstrap.sh` exits 0 when everything passed, and `2` when the install
completed but `init.sh` still reports a failing mandatory check — usually
Tailscale not being up. Exit `2` is not a failed install: the remaining steps
still run, and the closing note repeats what is outstanding.

### Nuances worth knowing

**The Tailscale operator grant is invisible when missing.** dev-stack writes
Serve config every time a service starts, which requires operator rights.
*Reading* Serve status succeeds without them, so a host missing the grant looks
healthy to `init.sh --check` while every service start fails to publish its
route with `Access denied: serve config denied`. Step 3 handles it; the recorded
operator is read from `tailscale debug prefs`, since probing Serve cannot detect
this.

**Porterminal is found as a bare command on `PATH`.** It installs to
`~/.local/bin`, which Ubuntu adds to `PATH` only from `~/.profile` — so an
interactive non-login shell never sees it and `porterminal start` fails with
`Missing Porterminal command`. Step 5 writes an absolute
`DEV_STACK_PORTERMINAL_COMMAND` into both `~/.bashrc` and `~/.profile` to make
this PATH-independent. Shells already open when bootstrap ran need to be
reloaded, and `dev_stack www` needs a restart to inherit it, because the
dashboard passes its own environment to the lifecycle actions it invokes.

**Porterminal's first launch is slow.** It runs through `uv` from a checkout, and
a cold cache resolves and builds around 40 packages — long enough that
`dev_stack porterminal start` times out waiting for the port and leaves an
unmanaged process holding it. Step 4 warms the environment with `uv sync` to
avoid that.

**Docker group membership needs a new login.** `usermod` never changes an
already-running session, so `docker` stays unreachable without `sudo` until you
log out and back in, or run `newgrp docker`.

**No service starts, and nothing survives a reboot.** `bootstrap.sh` installs and
configures only. Start services yourself, and expect to start them again after
a reboot; there are no systemd units.

**A fresh install enables every service, including go2rtc**, whose host paths in
`init.sh` are defaults that will not match a new machine. `init.sh --check`
reports them as missing until they are corrected in
`<prefix>/.dev-stack/config/settings.json`, or the service is disabled with
`./init.sh --no-go2rtc`. These paths are written only on a *fresh* install, so
re-enabling later does not re-prompt for them.

**`init.sh` does not upgrade.** After a `git pull`, the checkout and the
installed copy differ and `init.sh` refuses with `the source and installed code
differ`. Change service flags *before* pulling, or reinstall by removing only
the code directory and command symlink it names — the runtime tree, with its
credentials and state, is separate and is never touched.

## Installation

`init.sh` installs the current checkout; it never clones a repository, downloads
software, installs packages, or starts or stops a service. The checkout can live
anywhere, including `~/Downloads`. Installing software is `bootstrap.sh`'s job,
never `init.sh`'s.

On a machine where the old `~/.bin/dev_stack` still exists, preserve it with the
one-off manual rename first:

```bash
mv ~/.bin/dev_stack ~/.bin/dev_stack.legacy
```

Then install the checkout:

```bash
./init.sh --prefix ~/.bin
```

The default prefix is `~/.bin`, so `./init.sh` is equivalent. The resulting
layout is:

```text
~/.bin/
├── dev_stack -> ~/.bin/src/dev-stack/dev_stack
├── src/dev-stack/                 copied repository
└── .dev-stack/
    ├── config/settings.json       enabled services and host-specific paths
    ├── state/                     lifecycle state
    ├── data/                      File Browser DB and Porterminal snippets
    ├── secrets/                   generated service secrets
    └── logs/                      managed process logs
```

The code copy and runtime data are deliberately separate. A fresh installation
does not read or alter the legacy `~/.bin/state/dev-stack` directory.

Fresh installs enable every service. Disable optional integrations explicitly:

```bash
./init.sh --prefix ~/.bin \
  --no-code-server \
  --no-filebrowser \
  --no-porterminal \
  --no-go2rtc \
  --no-www
```

The matching positive flags re-enable a service on a later run, for example
`--code-server`. Omitted flags preserve existing choices. Disabled services
return a clear CLI error and are omitted from the dashboard status payload.
Tailscale and the core CLI/Python dependencies are mandatory and cannot be
disabled.

The installer reports dependencies in separate groups:

- mandatory command/runtime dependencies;
- mandatory Tailscale daemon/authentication/Serve readiness;
- dependencies for each enabled service;
- optional diagnostics.

Missing dependencies are only reported. Exit status `2` means the files were
installed but a mandatory dependency or readiness check failed. Missing tools
for an optional enabled service do not fail the core installation; that service
remains unavailable until its dependency is supplied.

Run a read-only recheck with:

```bash
./init.sh --check --prefix ~/.bin
```

Rerunning the same checkout is idempotent. The script reconciles settings and
the command symlink without resetting state. If the target contains unrelated,
partial, locally modified, or different-version code, it stops and prints the
exact code and command paths to inspect and manually move or delete. It never
overwrites changed code or removes `.dev-stack`.

Porterminal is discovered through the `porterminal` command on `PATH`. In the
local Porterminal checkout, install that command with:

```bash
./install_on_localhost.sh
```

The command is normally installed as `~/.local/bin/porterminal`. If it has a
different name or path, set `DEV_STACK_PORTERMINAL_COMMAND` before using the CLI
or starting the web dashboard. The dashboard inherits that setting and the
current `PATH` for its allowlisted lifecycle actions.

## Lifecycle

```bash
dev_stack admin password reset
dev_stack admin status
dev_stack www start
dev_stack www status
dev_stack www restart
dev_stack www stop
dev_stack www clean
```

`dev_stack www start` refuses to run until the master password is configured.
The reset prompt does not echo input and stores only a salted scrypt verifier in
`state-admin.json` with mode `0600`.

The live dashboard is then available locally at `http://127.0.0.1:1080/` and,
when Tailscale Serve is enabled, at
`https://<magicdns-name>/dev-stack/`.

The status surface is read-only. Its normal polling subprocess is
the fixed argument array:

```text
dev_stack status --json
```

The snapshot collector obtains go2rtc's Docker-backed service record through
the fixed `dev_stack go2rtc status --json` contract. File Browser, Porterminal,
go2rtc, and Code Server lifecycle controls use a separate fixed CLI allowlist.
PUT, PATCH, DELETE, and unrecognized POST requests are rejected with HTTP 405.
Protected credential reads also use a fixed CLI allowlist. Request values are
never turned into a shell command.

The lifecycle bridge can execute only these CLI shapes:

```text
dev_stack filebrowser start
dev_stack filebrowser stop
dev_stack filebrowser restart
dev_stack filebrowser clean
dev_stack porterminal start
dev_stack porterminal stop
dev_stack porterminal restart
dev_stack porterminal clean
dev_stack go2rtc start
dev_stack go2rtc stop
dev_stack go2rtc restart
dev_stack go2rtc clean
dev_stack go2rtc publish
dev_stack go2rtc unpublish
dev_stack code-server start --instance-name <name>
dev_stack code-server stop --instance-name <name>
dev_stack code-server restart --instance-name <name>
dev_stack code-server clean
dev_stack code-server profile save <running-instance>
dev_stack code-server profile list [--json]
dev_stack code-server profile start <profile-name>
dev_stack code-server profile delete <profile-name>
dev_stack code-server start --instance-name <name> --workspace-dir <absolute-path>
```

Saved Code Server profiles live separately from runtime instances in
`state-code-server.json`. Each profile stores only its name and canonical
absolute workspace path. Saving the same running instance updates its profile.
Starting a profile delegates to the normal named Code Server start path.
Deleting a profile never stops an instance, and `code-server clean` never
removes saved profiles.

Stop, Restart, profile Delete, and Clean require confirmation in the frontend. Start reuses
manager state or the CLI defaults. Clean removes stopped runtime/configuration
state while preserving the File Browser database and saved admin credential.
Porterminal Clean likewise preserves its saved password, YAML configuration,
and snippets. go2rtc Clean removes only its stopped Compose container while
preserving its configuration, image, and saved WebUI/API credential. Logs and
password reset remain CLI-only. All lifecycle operations for other services
remain unavailable over HTTP.

## Master-password authentication

Only the login page, its minimal assets, the login endpoint, and `/health` are
available without a session. The dashboard HTML and assets, `/api/status`, host
details, credential reveal endpoint, and all lifecycle endpoints require the
same master-password session on localhost and over Tailscale.

Successful login creates a random 30-minute server-side session. Its cookie is
HttpOnly and SameSite=Strict, and is Secure and scoped to `/dev-stack/` over
Tailscale HTTPS. Status refreshes do not extend the session. Logout, expiry,
server restart, or master-password reset invalidates access. Five failed logins
within a minute temporarily rate-limit further attempts.

New master passwords must contain at least 8 characters, including at least
one number and one non-whitespace special character.

## Protected credentials

The auto-refreshed `/api/status` response never contains a password. It reports
only whether a saved credential is available. Code Server reports this for each
individual instance; File Browser, Porterminal, and go2rtc report singleton
availability.

The Password button performs an explicit no-cache POST to
`/api/credentials/reveal`. The response is removed from the page after 30
seconds and when the page becomes hidden. It is never stored in local storage,
placed in a URL, or written to the server log.

The credential bridge can execute only these CLI shapes:

```text
dev_stack credentials show --service code-server --instance-name <name> --json
dev_stack credentials show --service filebrowser --json
dev_stack credentials show --service porterminal --json
dev_stack credentials show --service go2rtc --json
```

If the original File Browser bootstrap password was not captured, reset it once
with `dev_stack filebrowser password reset`. The manager briefly stops File
Browser, updates the `admin` user, saves the replacement in owner-only state,
and restores the prior running state.

Reset go2rtc's generated WebUI/API password from the CLI with
`dev_stack go2rtc password reset`. The HTTP dashboard can reveal the saved
credential but cannot rotate it.

## Demo fixture

```bash
cd ~/.bin/src/dev-stack/frontend
python3 -m http.server 1080 --bind 127.0.0.1
```

Open the explicit demo mode:

```text
http://localhost:1080/?demo=1
```

Without `?demo=1`, the frontend requests the live status endpoint.

## Localhost and tailnet paths

All assets and API requests use document-relative URLs. The same frontend can
therefore run in both intended locations:

| Page URL | Resolved status URL |
| --- | --- |
| `http://localhost:1080/` | `http://localhost:1080/api/status` |
| `https://node.example.ts.net/dev-stack/` | `https://node.example.ts.net/dev-stack/api/status` |

Tailscale Serve removes the `/dev-stack` prefix before proxying to the local
server. Document-relative asset and API URLs keep both access paths working.
No hostname, port, or tailnet name is hard-coded in the frontend.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`, `HEAD` | `/login` and login assets | Public master-password form |
| `GET`, `HEAD` | `/health` | Lightweight server health; does not invoke the CLI |
| `POST` | `/api/session/login` | Verify the master password and create a session |
| `POST` | `/api/session/logout` | Revoke the current session |
| `GET`, `HEAD` | `/` and application assets | Authenticated dashboard frontend |
| `GET`, `HEAD` | `/api/status` | Authenticated `dev_stack status --json` output |
| `POST` | `/api/credentials/reveal` | Authenticated, explicit credential read |
| `POST` | `/api/services/filebrowser/actions` | Authenticated Start, Stop, Restart, or Clean |
| `POST` | `/api/services/porterminal/actions` | Authenticated Start, Stop, Restart, or Clean; Start and Restart publish automatically |
| `POST` | `/api/services/go2rtc/actions` | Authenticated Start, Stop, Restart, Clean, Publish, or Unpublish |
| `POST` | `/api/services/code-server/actions` | Authenticated lifecycle, saved-profile operations, path start, or global Clean |

The API response is briefly cached to avoid launching duplicate status probes
when several browser requests arrive together. API responses use
`Cache-Control: no-store`.

## Status API contract

The frontend accepts `schema_version: 1` with this top-level shape:

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-16T09:42:18+02:00",
  "host": {
    "hostname": "p8s-mkuse-lenovo",
    "username": "developer",
    "display_name": "Development laptop",
    "interfaces": [
      {
        "name": "enx98a44e85b014",
        "type": "ethernet",
        "physical": true,
        "state": "down",
        "addresses": [],
        "configured_addresses": ["192.168.50.10/24"]
      }
    ]
  },
  "tailnet": {
    "connected": true,
    "dns_name": "p8s-mkuse-lenovo.example.ts.net",
    "ipv4": "100.64.0.11",
    "dashboard_url": "https://p8s-mkuse-lenovo.example.ts.net/dev-stack/"
  },
  "services": []
}
```

The host dropdown shows the local username, hostname, `tailnet.dns_name`, a
copyable SSH command, and addresses from physical wired or
wireless interfaces. Bridge, Docker, loopback, tunnel, and other virtual
interfaces are excluded even if the backend includes them in `host.interfaces`.
`addresses` contains currently assigned addresses; `configured_addresses`
allows an inactive physical adapter to remain visible without implying that its
address is currently active.

`tailnet.dashboard_url` is encoded into the host panel's QR code locally in the
browser. The URL is never sent to a third-party QR service.

Supported service states mirror the current `dev_stack` vocabulary:

- `running`
- `stopped`
- `drifted`
- `unknown`
- `not_found`

A singleton service can provide:

```json
{
  "id": "filebrowser",
  "name": "File Browser",
  "description": "Private file access for this machine",
  "status": "running",
  "summary": "The service is available.",
  "bind_host": "127.0.0.1",
  "port": 9900,
  "uptime_seconds": 120,
  "url": "https://node.example.ts.net/filebrowser/",
  "route": {
    "enabled": true,
    "present": true,
    "path": "/filebrowser"
  },
  "health": {
    "state": "healthy",
    "message": "Process, listener, health check, and route agree"
  }
}
```

Code Server uses the same service fields plus `instances` and `profiles` arrays. Each
instance can provide `name`, `status`, `workspace`, `port`, `uptime_seconds`,
and `url`. Each saved profile provides only `name` and `workspace`. Values
received from the API are inserted as text, and URLs are
limited to HTTP or HTTPS before they become links.

## Current scope

The current implementation includes live status display, refresh, protected
credential reveal, File Browser, Porterminal, and go2rtc lifecycle controls,
per-instance Code Server Start/Stop/Restart, saved profiles, absolute-path
starts, global Code Server Clean, lifecycle management for the dashboard
itself, and private Tailscale publication. It does not include:

- HTTP lifecycle controls for other services;
- arbitrary CLI execution from HTTP requests;
- boot-time startup;
- Tailscale Funnel or other public internet exposure.

Manager state may contain saved service passwords. The snapshot collector
constructs every response from an explicit field allowlist and never serializes
raw state objects. The HTTP bridge independently rejects snapshots containing
common secret field names.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q backend
bash -n dev_stack
bash -n init.sh

cd frontend/e2e
npm install
npm test
```
