# EstateIQ — DarGlobal + Wasalt AI Assistant

A Dockerised real-estate research chatbot. It collects publicly accessible HTML pages from the official DarGlobal and Wasalt domains, stores extracted text in SQLite, retrieves relevant passages using lightweight lexical ranking, and asks an OpenRouter model to answer with source URLs.

## Features
- DarGlobal and Wasalt source separation
- Respect for each site's `robots.txt`; same-domain crawling only
- Sitemap discovery plus seed pages; bounded refresh (25 candidate pages/site by default)
- SQLite persistence in a Docker volume
- OpenRouter chat completions; default model `recursive/flint-v1.0:free`
- Source links in answers, health and source endpoints
- Responsive chat UI

## Run locally with Docker
1. Copy `.env.example` to `.env`.
2. Add an OpenRouter API key from https://openrouter.ai/keys. The key is required for AI responses. The OpenRouter free-model router may select a currently available free model; free-model availability and rate limits are controlled by OpenRouter.
3. Start:
   ```bash
   docker compose up --build -d
   ```
4. Open http://localhost:8000
5. Collect source pages by calling:
   ```bash
   curl -X POST http://localhost:8000/api/refresh
   ```
   Or refresh only one source:
   ```bash
   curl -X POST 'http://localhost:8000/api/refresh?company=DarGlobal'
   ```
6. Check status at `/api/health`; collected URLs at `/api/sources`.

## Deploy
Deploy this repository to any Docker-capable host (Render, Railway, Fly.io, an AWS/GCP VM, etc.). Configure:
- `OPENROUTER_API_KEY` (secret)
- `OPENROUTER_MODEL=recursive/flint-v1.0:free`
- Persistent disk mounted at `/app/data` (recommended; otherwise the SQLite index may be lost on redeploy)
- Health check path `/api/health`
- Expose port `8000`

For a public URL, the hosting provider must build the included Dockerfile and assign a domain. Do not commit `.env` or expose the OpenRouter key in frontend code.

## API
- `GET /api/health`
- `GET /api/sources`
- `POST /api/refresh` (optional query parameter `company=DarGlobal|Wasalt`)
- `POST /api/chat` with JSON `{ "message": "...", "history": [] }`

## Data and accuracy notes
This is a public-information assistant, not a property transaction system. Wasalt search results, prices, and inventory may be dynamic or login-gated; the crawler does not bypass authentication, CAPTCHAs, paywalls, or access controls. Verify current availability and commercial terms with the source website. The crawler uses a bounded page count and must be refreshed to capture later changes.

