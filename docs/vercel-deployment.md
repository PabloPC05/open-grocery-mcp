# Vercel deployment

## Production topology

The Vercel project `open-grocery-mcp` hosts the Python ASGI adapter in
`api/index.py` using stateless Streamable HTTP:

- production MCP: `https://open-grocery-mcp.vercel.app/mcp`;
- public, non-sensitive health check: `/health`;
- production branch: `main`;
- feature branches: isolated preview deployments.

Vercel builds from `PabloPC05/open-grocery-mcp`. A successful push to `main`
updates the stable production domain automatically. The running deployment is a
remote artifact: it does not read this working directory and continues working
when the owner's computer is off.

The native GitHub integration was verified on 2026-08-27: the Vercel project is
linked to this repository with `main` as its production branch. The binding
lives in Vercel and GitHub, so removing a local clone does not disconnect it.

## Authentication and safety

`OPEN_GROCERY_MCP_ACCESS_TOKEN` is configured as a sensitive Preview and
Production environment variable. MCP clients send it as a Bearer token. The
server fails closed if it is missing or wrong. Do not write the value to a config
file, commit, issue, log or chat.

The hosted service deliberately keeps these flags unset:

```text
OPEN_GROCERY_ENABLE_RETAILER_WRITES
OPEN_GROCERY_ENABLE_ORDER_SUBMISSION
OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION
```

Consequently, Vercel is suitable for catalogue, comparison and local-draft
tools, not authenticated retailer checkout. Browser profiles and retailer
sessions remain on the owner's machine.

Codex can reference the secret without storing its value in configuration:

```powershell
codex mcp add open-grocery `
  --url https://open-grocery-mcp.vercel.app/mcp `
  --bearer-token-env-var OPEN_GROCERY_MCP_ACCESS_TOKEN
```

## Git deployment workflow

1. Develop and run `pytest`, `python -m compileall -q src tests tools api`, and
   `ruff check .`.
2. Push a branch to obtain a Vercel preview.
3. Merge or fast-forward the reviewed commit to `main`.
4. Vercel builds production and moves `open-grocery-mcp.vercel.app` only after a
   successful build.
5. Check `/health`, verify unauthenticated `/mcp` returns `401`, then initialize
   an authenticated MCP client and list its tools.

Use `vercel inspect <deployment-url>` and `vercel logs <deployment-url>` for
diagnosis. Use `vercel rollback` if a production regression escapes verification.

## Removing or restoring a local clone

Once `git status` is clean and `main` is pushed, the local clone is not required
to use or operate the deployed read-only MCP. It can be restored later with:

```powershell
git clone https://github.com/PabloPC05/open-grocery-mcp.git
cd open-grocery-mcp
vercel link --yes --project open-grocery-mcp --team pablopc05s-projects
```

`.vercel/`, `.env.local`, retailer sessions and captures are intentionally absent
from Git. Deleting a clone does not delete the Windows user-level Bearer token,
but moving to another machine requires securely transferring or rotating it.
