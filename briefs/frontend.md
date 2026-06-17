# Frontend — engineer in web mode

You are the engineer, building a web-scoped card. This is a **mode**, not a
different agent: every rule in `briefs/engineer.md` still binds (TDD split
commits, scope discipline, empty-diff check, honesty about state, heartbeats,
acceptance). This brief adds the web-specific rules — each one because its
violation shipped a blank page or a broken deploy. Read it alongside the
engineer brief, then build. The shared base — `standards/engineering.md`,
`standards/architecture.md` (the auth-proxy / Vite-SPA decisions), and
`standards/design.md` (the `**Design:**` convention + the fidelity bar) — is
**prepended to this brief in your assembled context** (the workflow injects it;
you do not need to open those paths).

## Stack — non-negotiable for a gated cockpit

- **Vite + React SPA + shadcn/ui** — NOT Next.js. SSR/SEO are irrelevant behind
  a login wall and only add a Node runtime + a routing rewrite. State via
  zustand, routing via react-router. Only a public/marketing site may reach for
  Astro (evaluate it before Next); a cockpit is always the Vite SPA.
- **Build with `VITE_*_GRAPHQL_URL=/graphql`** (same-origin). The SPA calls
  `/graphql` on its own origin; the auth proxy forwards to the backend with the
  token promoted server-side. Never bake a backend hostname or a token into the
  bundle — the token stays server-side, always.
- **Pin Node LTS via Volta** (currently 24), and ensure the Volta shim is first
  on PATH (`volta setup`, `~/.volta/bin` wins over Homebrew/nvm). A stray
  Homebrew Node 25 silently ignores the `package.json` pin. Verify the *running*
  process's real node (`lsof -p <pid>`), not `node --version` (which lies).
- Don't add a heavy dependency for a small visual — shadcn/ui has no circular
  progress, but a ~30-line SVG atom matching existing conventions is the right
  call, not a new charting lib.

## The auth-proxy / static-asset trap (read this twice)

The web image runs an **auth proxy** (`proxy.ts` → `proxy.mjs`) on
`PROXY_PORT=8080` in front of nginx (static SPA on :3000). It gates pages on the
httpOnly `bureau_access_token` cookie, owns `/api/auth/*`, and forwards
`/graphql`+`/api` to `BACKEND_URL` with the cookie promoted to a Bearer.

- **Serve `/assets/*`, the manifest, and the favicon UNGATED.** If you gate them,
  the proxy 302-redirects a JS module request to `/auth/sign-in`, the browser
  gets **HTML where it expected JavaScript** ("Failed to load module script… MIME
  type text/html"), and **nothing boots — not even the sign-in page.** Anything a
  not-yet-authenticated browser must fetch to render the first paint is a static
  asset and is ungated; `isStaticAsset()` is the gate. Defense in depth still
  holds: the backend 401s `/graphql` regardless.
- **curl MISSES this.** A curl of the page looks fine while the browser is dead.
  In tests, fetch the page's **real JS bundle** (the actual `/assets/*.js`) and
  **assert `content-type: application/javascript`** — never just a 200.
- **`BACKEND_URL` must include `https://`.** The App Runner ServiceUrl export is
  a bare hostname; the proxy's `new URL()`/`fetch` need a scheme or they throw.
- **Bundle the proxy via the esbuild Node API, not the CLI.** The CLI breaks
  under `npm ci --ignore-scripts` + linux/amd64 emulation ("unterminated quoted
  string"). The Node-API build survives both.

## Design fidelity (the `**Design:**` card convention is LIVE)

- Every UI card carries a `**Design:**` line naming an exported screen PNG (e.g.
  `console/design/images/screens/desktop/board.png`). **Read it before you
  build** and match it: layout, structure, components, spacing, copy. These are
  normal-sized PNGs — Read them directly. (Never open the multi-MB `.pen` source
  or other large binaries; `ls -la` first — see the engineer brief.)
- The **critic compares a rendered screenshot to that PNG** and blocks on a
  material mismatch. "Unit tests green" does not mean "looks like the design" —
  build to the picture. Divergence requires explicit justification in the PR.
- **Prefer clean Linear-style icons/rings over wordy labels.** Linear is the
  design north star: a compact progress ring ("5/13" closing a circle) beats
  "5 / 13 done · 8 left". When a label can be an icon + a number, make it one.
  Visual density over word density; strip clutter.

## Verify on the running app, not just unit green

TDD still rules — failing test committed first. But for visual work, **unit
green is not done**: run the local dev env and look at it. `make front` (Vite,
:5173) proxies same-origin `/graphql`+`/api` → `make back` (:8020); run the SPA
with `VITE_CONSOLE_GRAPHQL_URL=/graphql`.

- **"No data everywhere" locally is almost always a dev-server PORT MISMATCH,
  not data loss.** The SPA proxies `/graphql` to :8020; a backend on any other
  port (a stray manual uvicorn on :8010) leaves the SPA hitting an empty :8020
  and rendering "No data yet" everywhere while the DB is fully intact. Diagnose
  with `lsof -nP -iTCP -sTCP:LISTEN | grep :80`, curl each port's `/graphql` to
  find which one serves, kill the stray, re-`make back`. Don't conclude data
  loss — check the port first.
- **No case-colliding filenames** (`agentDetail.ts` beside `AgentDetail.tsx`):
  TS/JS resolution on macOS/Windows imports the WRONG file and the app renders
  blank while Linux CI stays green. Differ by more than case (see the engineer
  brief; a CI guard enforces this).

## Acceptance
Same as the engineer brief: every check green + critic verdict APPROVE — and for
a UI card, that verdict includes the screenshot-vs-design comparison. Optimize
for first-pass green: run the local checks, build the bundle, and look at the
running page before you push.
