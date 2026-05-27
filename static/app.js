// ── Escaping ────────────────────────────────────────────────────────────────
// Submissions are community-supplied, so every field is treated as hostile.

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function safeUrl(u) {
  if (!u) return '';
  try {
    const proto = new URL(u, location.href).protocol;
    return ['http:', 'https:', 'magnet:'].includes(proto) ? u : '';
  } catch {
    return '';
  }
}

// ── Data layer (sharded static index) ────────────────────────────────────────
// meta.json  — counts + page size
// slim.json  — lean record per model; the in-memory browse/search/sort dataset
// records/<bucket>.json — full records, loaded on demand for detail pages

const INDEX = './data/index';
let _meta = null, _slim = null;
const _recordCache = {};

async function loadMeta() {
  if (_meta) return _meta;
  _meta = await (await fetch(`${INDEX}/meta.json`)).json();
  return _meta;
}

async function loadSlim() {
  if (_slim) return _slim;
  _slim = await (await fetch(`${INDEX}/slim.json`)).json();
  return _slim;
}

// Must match bucket_key() in scripts/build_index.py exactly.
function bucketKey(id) {
  return id.toLowerCase().replace(/[^a-z0-9]/g, '_').slice(0, 2).padEnd(2, '_');
}

async function loadRecord(id) {
  const key = bucketKey(id);
  if (!_recordCache[key]) {
    _recordCache[key] = fetch(`${INDEX}/records/${key}.json`)
      .then(r => (r.ok ? r.json() : {}))
      .catch(() => ({}));
  }
  const bucket = await _recordCache[key];
  return bucket[id] || null;
}

// ── Filter / sort over slim records ──────────────────────────────────────────

function filterModels(models, { q = '', category = '', variant = '', architecture = '' } = {}) {
  return models.filter(m => {
    if (category && m.category !== category) return false;
    if (variant && m.variant !== variant) return false;
    if (architecture && m.architecture !== architecture) return false;
    if (q) {
      const s = q.toLowerCase();
      return (
        (m.name || '').toLowerCase().includes(s) ||
        (m.creator || '').toLowerCase().includes(s) ||
        (m.architecture || '').toLowerCase().includes(s) ||
        (m.variant || '').toLowerCase().includes(s) ||
        (m.parameters || '').toLowerCase().includes(s) ||
        (m.tg || []).some(t => t.toLowerCase().includes(s))
      );
    }
    return true;
  });
}

function sortModels(models, by = 'added', dir = 'desc') {
  return [...models].sort((a, b) => {
    let va = a[by] ?? '';
    let vb = b[by] ?? '';
    if (by === 'added') { va = new Date(va); vb = new Date(vb); }
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return dir === 'asc' ? -1 : 1;
    if (va > vb) return dir === 'asc' ? 1 : -1;
    return 0;
  });
}

// ── Labels / formatting ──────────────────────────────────────────────────────

function badgeClass(cat) {
  return { llm: 'badge-llm', diffusion: 'badge-diffusion', misc: 'badge-misc' }[cat] || '';
}
function catLabel(cat) {
  return { llm: 'LLM', diffusion: 'Diffusion', misc: 'Misc' }[cat] || cat;
}
function variantLabel(v) {
  return { base: 'Base', instruct: 'Instruct', rlhf: 'RLHF/RLAIF',
           finetune: 'Fine-tuned', lora: 'LoRA' }[v] || v || '—';
}
function archLabel(arch) {
  return {
    transformer: 'Transformer', dit: 'DiT', unet: 'UNet',
    'flow-matching': 'Flow Matching', mamba: 'Mamba', rwkv: 'RWKV',
    hybrid: 'Hybrid', moe: 'MoE', other: 'Other'
  }[arch] || arch;
}
function formatDate(d) {
  return d ? new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
}
function modelHref(id) {
  return `model.html#${encodeURIComponent(id)}`;
}

// ── Row rendering (slim record) ──────────────────────────────────────────────

function swarmCell(r) {
  if (!r.m) return '<span class="td-secondary">—</span>';
  const cls = r.s > 0 ? 'swarm-live' : 'swarm-dead';
  return `<span class="swarm ${cls}" title="${r.m} magnet(s)">⚡ ${r.s}<span class="sw-up">▲</span> ${r.p}<span class="sw-dn">▼</span></span>`;
}

function renderRow(r) {
  const hf = safeUrl(r.hfu);
  return `<tr>
    <td class="td-name"><a href="${modelHref(r.id)}">${esc(r.name)}</a></td>
    <td class="td-secondary">${esc(r.creator) || '—'}</td>
    <td><span class="badge ${badgeClass(r.category)}">${esc(catLabel(r.category))}</span></td>
    <td><span class="badge badge-variant">${esc(variantLabel(r.variant))}</span></td>
    <td>${esc(archLabel(r.architecture))}</td>
    <td class="mono">${esc(r.parameters) || '—'}</td>
    <td>${swarmCell(r)}</td>
    <td>${hf ? `<a class="icon-link" href="${esc(hf)}" target="_blank" rel="noopener" title="HuggingFace">HF ↗</a>` : '<span class="td-secondary">—</span>'}</td>
    <td class="td-secondary" style="white-space:nowrap">${formatDate(r.added)}</td>
  </tr>`;
}

// ── URL params ───────────────────────────────────────────────────────────────

function getUrlParam(key) {
  return new URLSearchParams(window.location.search).get(key) || '';
}
function setUrlParam(key, val) {
  const p = new URLSearchParams(window.location.search);
  if (val) p.set(key, val); else p.delete(key);
  history.replaceState(null, '', `${location.pathname}?${p}`);
}
