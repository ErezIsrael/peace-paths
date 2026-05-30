# 🕊 Peace Paths

An AI-powered tracker of concrete peace initiatives across the Middle East.

[Live Site](https://peace-paths.pages.dev)

---

## What is Peace Paths?

Peace Paths monitors ~29 RSS feeds across the Middle East, classifies articles with AI, and groups them into solution categories. You see a dashboard showing which peace initiatives are advancing, stalling, or emerging — in real time.

Each category tracks a phase progression (e.g., Active Fighting → Ceasefire Talks → Draft Agreement → Signed → Holding), a momentum direction (advancing / stable / stalling), and an event count.

---

## How It Works

```
RSS Feeds → AI Classification → Solution Cards → Dashboard
```

1. **RSS feeds** from regional news, think tanks, UN sources, and OSINT are fetched
2. **LLM inference** (local llama.cpp) determines Middle East relevance and classifies each article into a category
3. **Articles are grouped** by category with phase tracking, sentiment analysis, and momentum scoring
4. **Data is served** via Cloudflare KV and displayed on a static frontend

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js + npm (for Wrangler)
- A llama.cpp server running on your local network

### Setup

```bash
# Clone and install
git clone https://github.com/ErezIsrael/peace-paths.git
cd peace-paths

# Configure environment
cp .env.example .env
# Edit .env with your llama.cpp URL and Cloudflare credentials

# Configure RSS feeds
cp rss-feeds.example.json rss-feeds.json
# Edit with your feed URLs

# Configure categories
cp categories.example.json categories.json
```

### Local Development

```bash
python dev-serve.py
# → http://localhost:8765
```

### Running the AI Pipeline

```bash
# Quick update (last 2 hours, merges into existing data)
python ai-analyze-prod.py --fast

# Full run (7-day window, overwrites data)
python ai-analyze-prod.py --daily

# Skip upload (local file only)
python ai-analyze-prod.py --fast --skip-upload
```

---

## Architecture

| Component | Technology |
|-----------|------------|
| AI inference | llama.cpp (self-hosted) |
| Frontend | Static HTML/JS/CSS |
| Hosting | Cloudflare Pages |
| Data storage | Cloudflare KV |
| Data delivery | Cloudflare Pages Function |

---

## License

[MIT](LICENSE)
