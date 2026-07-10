# Deploying Broadcast Layer

Broadcast Layer is a single FastAPI service. It runs anywhere Python 3.12
runs: your cloud, your VPC, or on-prem. No managed dependencies are required
for a live broadcast; the service holds live track state in memory and emits
signed cues.

## Run locally

    pip install -r requirements.txt
    uvicorn broadcastlayer.api:app --host 0.0.0.0 --port 8000

Open:
- `/caption`   captioner / interpreter console
- `/dashboard` operations view
- `/player`    sample-player demo (paste a track id)

## Configuration

- `BL_SIGNING_KEY` (required in production): HMAC key used to sign every cue
  receipt. Set a strong random value; rotating it invalidates prior
  signatures, so keep it stable for the life of a broadcast's records.

## Deployment environments

- **Cloud / container:** build from the provided image or run uvicorn behind
  your TLS terminator. Front it with a reverse proxy that upgrades WebSocket.
- **Customer VPC / on-prem:** the service has no outbound dependencies and can
  run fully inside a customer network so the broadcast feed never leaves it.
- **High availability:** run multiple instances behind a load balancer with
  sticky sessions for WebSocket affinity; a shared pub/sub (e.g. Redis) fans
  cues across instances. Single-instance is sufficient for one broadcast.

## Security posture

- Content is not persisted beyond the live broadcast.
- Transport is TLS in production (terminate at your proxy or the container).
- No personal data is collected from viewers.
- Cue receipts are HMAC-signed; export a per-broadcast compliance report from
  `/v1/broadcasts/{id}/report`.
- SSO / access control for operator consoles integrates with your identity
  provider at deployment.

## Service levels

Broadcast Layer is designed for low-latency live operation and continuous
availability during a broadcast. Formal latency and uptime SLAs are set per
deployment contract and depend on the hosting environment you choose.
