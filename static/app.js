const DATA_URL = './data/models.json';
let _cache = null;

// Escape untrusted strings before they go into innerHTML. Submissions are
// community-supplied, so every model field is treated as hostile.
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// Only allow http(s) and magnet URLs into href attributes — blocks javascript:.
function safeUrl(u) {
  if (!u) return '';
  try {
    const proto = new URL(u, location.href).protocol;
    return ['http:', 'https:', 'magnet:'].includes(proto) ? u : '';
  } catch {
    return '';
  }
}

async function loadModels() {
  if (_cache) return _cache;
  const r = await fetch(DATA_URL);
  _cache = await r.json();
  return _cache;
}

function filterModels(models, { q = '', category = '', variant = '', architecture = '' } = {}) {
  return models.filter(m => {
    if (category && m.category !== category) return false;
    if (variant && m.variant !== variant) return false;
    if (architecture && m.architecture !== architecture) return false;
    if (q) {
      const s = q.toLowerCase();
      return (
        m.name.toLowerCase().includes(s) ||
        (m.creator || '').toLowerCase().includes(s) ||
        (m.architecture || '').toLowerCase().includes(s) ||
        (m.variant || '').toLowerCase().includes(s) ||
        (m.parameters || '').toLowerCase().includes(s) ||
        (m.tags || []).some(t => t.toLowerCase().includes(s))
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
  return new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function modelHref(id) {
  return `model.html#${encodeURIComponent(id)}`;
}

// Aggregate seeder/peer counts across a model's magnets for the list view.
function swarmTotals(m) {
  const mags = m.magnets || [];
  let seeders = 0, peers = 0, hasData = false;
  for (const mg of mags) {
    if (mg.swarm && (mg.swarm.checked || mg.swarm.seeders || mg.swarm.peers)) {
      hasData = true;
      seeders += mg.swarm.seeders || 0;
      peers   += mg.swarm.peers   || 0;
    }
  }
  return { count: mags.length, seeders, peers, hasData };
}

function swarmCell(m) {
  const t = swarmTotals(m);
  if (!t.count) return '<span class="td-secondary">—</span>';
  if (!t.hasData) return '<span class="swarm swarm-pending">⚡ pending</span>';
  const cls = t.seeders > 0 ? 'swarm-live' : 'swarm-dead';
  return `<span class="swarm ${cls}" title="${t.count} magnet(s)">⚡ ${t.seeders}<span class="sw-up">▲</span> ${t.peers}<span class="sw-dn">▼</span></span>`;
}

function renderRow(m) {
  const hf = safeUrl(m.huggingface);
  return `<tr>
    <td class="td-name"><a href="${modelHref(m.id)}">${esc(m.name)}</a></td>
    <td class="td-secondary">${esc(m.creator) || '—'}</td>
    <td><span class="badge ${badgeClass(m.category)}">${esc(catLabel(m.category))}</span></td>
    <td><span class="badge badge-variant">${esc(variantLabel(m.variant))}</span></td>
    <td>${esc(archLabel(m.architecture))}</td>
    <td class="mono">${esc(m.parameters) || '—'}</td>
    <td>${swarmCell(m)}</td>
    <td>${hf ? `<a class="icon-link" href="${esc(hf)}" target="_blank" rel="noopener" title="HuggingFace">HF ↗</a>` : '<span class="td-secondary">—</span>'}</td>
    <td class="td-secondary" style="white-space:nowrap">${formatDate(m.added)}</td>
  </tr>`;
}

function getUrlParam(key) {
  return new URLSearchParams(window.location.search).get(key) || '';
}

function setUrlParam(key, val) {
  const p = new URLSearchParams(window.location.search);
  if (val) p.set(key, val); else p.delete(key);
  history.replaceState(null, '', `${location.pathname}?${p}`);
}
