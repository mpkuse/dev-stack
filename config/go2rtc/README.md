# Local go2rtc deployment

This deployment exposes four streams:

- `brio`: optional Logitech Brio video and microphone
- `reolink_main`: browser-compatible H.264 1080p version of the main stream
- `reolink_main_4k_hevc`: original 4K HEVC Reolink main stream
- `reolink_sub`: the Reolink sub RTSP stream

The Reolink URLs are loaded from the host-specific environment file recorded in
`<prefix>/.dev-stack/config/settings.json`. `init.sh` seeds that path as
`$HOME/workspace/iap_rosws/.env`; edit the setting if the file lives elsewhere.
The URLs are referenced by variable name in `go2rtc.yaml`, so credentials are
not copied into this directory.

## Access

The WebUI/API and republished RTSP server listen only on localhost:

- WebUI: `http://127.0.0.1:1984/`
- Brio RTSP: `rtsp://127.0.0.1:8554/brio`
- Reolink main: `rtsp://127.0.0.1:8554/reolink_main`
- Reolink main 4K HEVC: `rtsp://127.0.0.1:8554/reolink_main_4k_hevc`
- Reolink sub: `rtsp://127.0.0.1:8554/reolink_sub`

`dev_stack go2rtc start` publishes the WebUI/API privately through Tailscale
Serve by default:

```text
https://<magicdns-name>/go2rtc/
```

This exposes the go2rtc administration API to permitted devices on the
tailnet. The WebUI/API additionally requires managed HTTP Basic authentication.
The RTSP listener remains bound to localhost.

The username is `admin`. Generate or rotate the password with:

```bash
dev_stack go2rtc password reset
```

Recover the saved credential through the explicit credential command:

```bash
dev_stack credentials show --service go2rtc --json
```

The password's manager record lives under `<prefix>/.dev-stack/state`; its
container environment file lives under `<prefix>/.dev-stack/secrets`. Both use
mode `0600`, and neither is included in status output. Because Tailscale Serve
reaches go2rtc through localhost,
`api.local_auth` remains enabled so proxy requests cannot bypass authentication.
Tailscale identity and ACLs provide an additional access boundary.

Use `--tailscale-no-serve` to keep the WebUI/API local-only. In that mode, use
an SSH tunnel if the browser is on another machine:

```bash
ssh -L 1984:127.0.0.1:1984 <host>
```

Then open `http://127.0.0.1:1984/` locally.

## Operations with `dev_stack`

Use the managed CLI from any directory:

```bash
# Start (or create) the server in the background and verify local health
dev_stack go2rtc start

# Start without publishing the WebUI/API through Tailscale Serve
dev_stack go2rtc start --tailscale-no-serve

# Publish or unpublish an already-running WebUI/API
dev_stack go2rtc publish
dev_stack go2rtc unpublish

# Generate or rotate the WebUI/API password
dev_stack go2rtc password reset

# Show reconciled container and WebUI/API status
dev_stack go2rtc status

# Emit the secret-free machine-readable status contract
dev_stack go2rtc status --json

# Follow logs (Ctrl+C only exits the log viewer)
dev_stack go2rtc logs --follow

# Show the most recent 100 log lines
dev_stack go2rtc logs --tail 100

# Stop without removing the container, or restart it
dev_stack go2rtc stop
dev_stack go2rtc restart

# Remove a stopped container; configuration and the image are preserved
dev_stack go2rtc clean
```

Docker Compose remains the runtime source of truth. The go2rtc manager state
does not duplicate container runtime state; it stores only the desired
Tailscale publication setting and generated Basic Auth credential in owner-only
state. `clean` refuses to remove a running container and preserves that
credential, so stop it first. Normal lifecycle operations should use
`dev_stack`, which supplies the host-specific Compose variables from runtime
settings.

The base Compose deployment starts without the Brio. When the configured video
and sound devices are present, `dev_stack` adds `compose.webcam.yaml`
automatically. Reolink streams therefore remain available while the Brio is
disconnected. The Brio may also be mapped into `p8s-perception-webcam`; only one
active capture process should use it at a time.
