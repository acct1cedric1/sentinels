# Security Policy

## Reporting a vulnerability

If you discover a security issue — especially anything that could leak an API
key, bypass the token gate, or forge a wallet session — please **do not** open a
public issue. Instead, open a private GitHub Security Advisory on this repository
so it can be fixed before disclosure.

## Handling of secrets

- API keys and the session secret live only in `config.json` / `.session_secret`,
  which are git-ignored and never committed.
- Error messages returned by the API are sanitized so upstream URLs (which embed
  the Helius key) are never echoed to clients.
- Wallet authentication uses an ed25519 **message** signature (no transaction, no
  approval). Sessions are HMAC-signed cookies; token-gated data is withheld
  server-side rather than hidden in the client.

## Scope notes

This is an analytics tool. "Smart money" labels are transparent on-chain
heuristics, not investment advice, and may include bots/snipers. Nothing in this
project should be construed as financial advice.
