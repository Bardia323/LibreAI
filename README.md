<div align="center">

# LibreAI

**A free, open, mirrorable index of AI models. No gatekeepers. No takedowns.**

[![Live](https://img.shields.io/badge/live-bardia323.github.io%2FLibreAI-0a0a0a)](https://bardia323.github.io/LibreAI/)
[![License: MIT](https://img.shields.io/badge/license-MIT-1d4ed8)](LICENSE)
[![Static](https://img.shields.io/badge/backend-none-15803d)](#how-it-works)

</div>

---

LibreAI is a static directory of AI models — LLMs, diffusion, and everything else — each linked to **BitTorrent magnets** and its HuggingFace page. The site is just files: no server, no database. It mirrors with one `wget`, so there's nothing central to seize or take down.

> Think of it as The Pirate Bay for open weights — the index is the meat, distribution is the swarm.

## How it works

```
data/models.json   →   build step   →   data/index/   →   static pages
  (source of truth)                    slim + shards       (browse / search / mirror)
```

- **Weights** distribute over BitTorrent (magnets). The site only holds the index.
- **Browse/search/sort** run in the browser over a slim index; only the visible page renders.
- **Detail** (magnets, file lists) loads on demand per model.
- Scales to git's ~100k-model ceiling with no backend; beyond that the shards go on IPFS.

## Submit a model

Use the [**Submit page**](https://bardia323.github.io/LibreAI/submit.html) — it generates the JSON and opens a pre-filled issue. A bot then:

| Check | Result |
|---|---|
| Magnet resolves to only `.safetensors` / `.gguf` / text, no duplicates | ✅ **auto-merged** via PR |
| Unverifiable, unsafe files, or needs eyes | 🔍 **review PR** opened |
| Duplicate HuggingFace URL or magnet infohash | ❌ rejected |

Magnet contents are fetched from the swarm and inspected in CI before anything merges.

## Mirror it

```bash
wget -r -k -p -np https://bardia323.github.io/LibreAI/   # full static mirror
git clone https://github.com/Bardia323/LibreAI            # canonical source
```

The more mirrors exist, the harder this is to kill. Host the files anywhere — Pages, IPFS, a VPS.

## Run locally

```bash
python -m http.server 8080          # serve the site
python scripts/build_index.py       # regenerate data/index/ after editing models.json
python scripts/scrape_hf.py --limit 100   # pull models from HuggingFace
```

## Layout

```
*.html              browse · model · submit · mirror
static/             style.css · app.js
data/models.json    source of truth
data/index/         generated: meta · slim · record shards
scripts/            build_index · scrape_hf · process_submission · check_magnet · update_swarm
.github/workflows/  submission (parse → PR) · swarm (seeder counts)
```

## License

[MIT](LICENSE). The index data is community-contributed.
