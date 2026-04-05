# Web Deployment Plan

## Overview

Two services to deploy:

| Service | Tech | Host |
|---|---|---|
| Client (web app) | Expo / React Native Web (static) | Cloudflare Pages |
| Worker (API) | Cloudflare Worker (Hono) | Cloudflare Workers |

Keeping everything on Cloudflare means the Worker and R2 bucket are co-located — no cross-region latency, no extra CORS complexity, and a single dashboard to manage it all.

---

## Domain Options

**Option A — Use existing domain**
- App: `openshelf.johannkoeh.io`
- API: `api.openshelf.johannkoeh.io`

**Option B — Dedicated domain (recommended for branding)**
- Register `openshelf.app` or `openshelf.audio` via Cloudflare Registrar (~$10–$14/yr)
- App: `openshelf.app`
- API: `api.openshelf.app`

Either way, point the domain's nameservers to Cloudflare so DNS and SSL are managed in one place.

---

## Step 1 — Cloudflare Account Setup

1. Sign up / log in at [dash.cloudflare.com](https://dash.cloudflare.com)
2. Add your domain (transfer or add existing) → set nameservers if needed
3. Ensure the R2 bucket `openshelf` already exists (created by the pipeline)

---

## Step 2 — Deploy the Worker (API)

The worker already has staging/production environments in `wrangler.toml`.

### One-time setup

```bash
cd worker
npx wrangler login          # authenticates via browser
npx wrangler whoami         # confirm account
```

### Deploy

```bash
# Staging
npm run deploy:staging

# Production
npm run deploy:production
```

This publishes to `openshelf-api.<your-subdomain>.workers.dev` by default.

### Custom domain for the API

In Cloudflare dashboard → Workers & Pages → `openshelf-api` → Settings → Domains & Routes → Add Custom Domain:

```
api.openshelf.app   (or api.openshelf.johannkoeh.io)
```

Cloudflare issues a cert and routes traffic automatically.

---

## Step 3 — Build & Deploy the Client (Expo Web → Cloudflare Pages)

### 3a. Build the static export

```bash
cd client
npm run build:web       # runs: expo export --platform web
```

Output goes to `client/dist/`.

### 3b. Deploy to Cloudflare Pages

**Option 1 — CLI (manual / one-off)**

```bash
cd client
npx wrangler pages deploy dist --project-name openshelf
```

**Option 2 — Git integration (recommended for ongoing deploys)**

1. Push repo to GitHub (already a git repo).
2. In Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git.
3. Select the `openshelf` repo.
4. Configure the build:

   | Setting | Value |
   |---|---|
   | Root directory | `client` |
   | Build command | `npm run build:web` |
   | Output directory | `dist` |
   | Node version | `22` |

5. Save — Cloudflare Pages will build and deploy on every push to `main`.

### 3c. Custom domain for the client

In the Pages project → Custom Domains → Add:

```
openshelf.app   (or openshelf.johannkoeh.io)
```

---

## Step 4 — Connect Client → API

The client needs to know the API URL. Set it as an environment variable during the Pages build.

In Cloudflare Pages → Settings → Environment Variables:

```
EXPO_PUBLIC_API_URL = https://api.openshelf.app
```

Ensure the client code reads this (e.g. `lib/api.ts`) from `process.env.EXPO_PUBLIC_API_URL`. The `EXPO_PUBLIC_` prefix makes it available at build time in Expo's static export.

---

## Step 5 — CORS

The worker must allow requests from the Pages domain. In `worker/src/index.ts` add the production origin:

```ts
app.use('*', cors({
  origin: ['https://openshelf.app', 'https://openshelf.johannkoeh.io'],
}))
```

Re-deploy the worker after this change.

---

## Step 6 — CI/CD with GitHub Actions (optional but recommended)

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy-worker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci
        working-directory: worker
      - run: npm run deploy:production
        working-directory: worker
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}

  # Client deploys automatically via Cloudflare Pages git integration
  # No action needed here unless you want to run tests first
  test-client:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci
        working-directory: client
      - run: npm test
        working-directory: client
```

Generate a `CLOUDFLARE_API_TOKEN` in the Cloudflare dashboard (Account → API Tokens → Create Token → "Edit Cloudflare Workers" template) and add it as a GitHub secret.

---

## Cost Estimate

| Resource | Free tier | Paid if exceeded |
|---|---|---|
| Cloudflare Pages | 500 builds/mo, unlimited bandwidth | $5/mo (Pro) |
| Cloudflare Workers | 100k req/day | $5/mo (Paid plan) |
| R2 storage | 10 GB storage, 1M reads/mo | $0.015/GB, $0.36/M reads |
| Domain (new) | — | ~$10–14/yr |

For a new project this is effectively free until you hit meaningful traffic.

---

## Launch Checklist

- [ ] Domain added to Cloudflare and nameservers updated
- [ ] `CLOUDFLARE_API_TOKEN` secret set (for wrangler CLI or GitHub Actions)
- [ ] Worker deployed to production (`npm run deploy:production` in `worker/`)
- [ ] API custom domain set: `api.<your-domain>`
- [ ] `EXPO_PUBLIC_API_URL` env var set in Pages project
- [ ] Client connected to GitHub via Cloudflare Pages
- [ ] CORS updated in worker to allow Pages domain
- [ ] First Pages build succeeds and site loads at custom domain
- [ ] R2 bucket has at least one book loaded (run the pipeline) so the app has content
