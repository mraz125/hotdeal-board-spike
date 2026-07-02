const state = {
  data: null,
  category: 'all',
  query: '',
  sort: 'default',
};

const sourceOrder = ['kakao', 'coupang', '11st', 'naver', 'ssg', 'lotteon'];

const $ = (selector) => document.querySelector(selector);
const lanesEl = $('#lanes');
const filtersEl = $('#categoryFilters');
const snapshotEl = $('#snapshot');
const template = $('#dealCardTemplate');
const DATA_REFRESH_INTERVAL_MS = 60 * 60 * 1000;
const UPDATE_CADENCE_LABEL = '1시간마다 자동 갱신';
let controlsWired = false;

const imageObserver = 'IntersectionObserver' in window
  ? new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const img = entry.target;
        if (img.dataset.src) {
          img.src = img.dataset.src;
          delete img.dataset.src;
        }
        imageObserver.unobserve(img);
      }
    }, { rootMargin: '600px 360px' })
  : null;

function formatWon(value) {
  if (!value && value !== 0) return '가격 확인';
  return `${Number(value).toLocaleString('ko-KR')}원`;
}

function textEl(tagName, className, text) {
  const el = document.createElement(tagName);
  if (className) el.className = className;
  el.textContent = text;
  return el;
}

function formatGeneratedAt(iso) {
  if (!iso) return '확인 전';
  const dt = new Date(iso);
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'
  }).format(dt);
}

function relativeFromNow(iso) {
  if (!iso) return '종료 시간 미표시';
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return '종료 시간 확인 필요';
  const diff = target - Date.now();
  if (diff <= 0) return '종료됨 또는 확인 필요';
  const minutes = Math.floor(diff / 60000);
  if (minutes < 60) return `${minutes}분 남음`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 남음`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return `${days}일 ${remHours}시간 남음`;
}

function checkedAgo(iso) {
  if (!iso) return '확인 전';
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.max(0, Math.floor(diff / 60000));
  if (minutes < 1) return '방금 확인';
  if (minutes < 60) return `${minutes}분 전 확인`;
  const hours = Math.floor(minutes / 60);
  return `${hours}시간 전 확인`;
}

function sourceCategoryText(deal) {
  const path = deal.source_category?.path || [];
  if (path.length > 1) return path.slice(-2).join(' > ');
  return deal.source_category?.label || deal.deal_type;
}

function getFilteredDeals() {
  const q = state.query.trim().toLowerCase();
  return state.data.deals.filter((deal) => {
    if (state.category !== 'all' && deal.canonical_category?.id !== state.category) return false;
    if (!q) return true;
    const haystack = [
      deal.title,
      deal.source_name,
      deal.source_category?.label,
      ...(deal.source_category?.path || []),
      deal.canonical_category?.label,
    ].join(' ').toLowerCase();
    return haystack.includes(q);
  });
}

function sortDeals(deals) {
  const copy = [...deals];
  if (state.sort === 'ending') {
    return copy.sort((a, b) => {
      const at = a.ends_at ? new Date(a.ends_at).getTime() : Infinity;
      const bt = b.ends_at ? new Date(b.ends_at).getTime() : Infinity;
      return at - bt;
    });
  }
  if (state.sort === 'discount') {
    return copy.sort((a, b) => (b.discount_rate || -1) - (a.discount_rate || -1));
  }
  if (state.sort === 'priceLow') {
    return copy.sort((a, b) => (a.deal_price || Infinity) - (b.deal_price || Infinity));
  }
  return copy;
}

function renderFilters() {
  const counts = new Map();
  for (const deal of state.data.deals) {
    const id = deal.canonical_category?.id || 'other';
    counts.set(id, (counts.get(id) || 0) + 1);
  }
  const categories = [{ id: 'all', label: '전체', count: state.data.deals.length }];
  for (const [id, label] of Object.entries(state.data.canonical_categories)) {
    if (counts.has(id)) categories.push({ id, label, count: counts.get(id) });
  }
  filtersEl.innerHTML = '';
  for (const cat of categories) {
    const btn = document.createElement('button');
    btn.className = `category-btn ${state.category === cat.id ? 'active' : ''}`;
    btn.type = 'button';
    btn.append(textEl('span', '', cat.label), textEl('strong', '', String(cat.count)));
    btn.addEventListener('click', () => {
      state.category = cat.id;
      render();
    });
    filtersEl.appendChild(btn);
  }
}

function renderSnapshot(filteredDeals) {
  const sourceCount = new Set(filteredDeals.map((d) => d.source_id)).size;
  const avgDiscounts = filteredDeals.map((d) => d.discount_rate).filter((v) => Number.isFinite(v));
  const avg = avgDiscounts.length ? Math.round(avgDiscounts.reduce((a, b) => a + b, 0) / avgDiscounts.length) : null;
  const endingSoon = filteredDeals.filter((d) => {
    if (!d.ends_at) return false;
    const left = new Date(d.ends_at).getTime() - Date.now();
    return left > 0 && left < 12 * 60 * 60 * 1000;
  }).length;
  const categoryCount = new Set(filteredDeals.map((d) => d.canonical_category?.id)).size;
  const cards = [
    ['수집 딜', `${filteredDeals.length.toLocaleString('ko-KR')}개`],
    ['쇼핑몰 소스', `${sourceCount}개`],
    ['분류 카테고리', `${categoryCount}개`],
    ['평균 할인율', avg ? `${avg}%` : '확인 중'],
  ];
  if (endingSoon) cards[3] = ['마감 임박', `${endingSoon}개`];
  snapshotEl.replaceChildren(...cards.map(([label, value]) => {
    const card = document.createElement('article');
    card.className = 'snapshot-card';
    card.append(textEl('span', '', label), textEl('strong', '', value));
    return card;
  }));
}

function renderCard(deal) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.href = deal.url || deal.source_home_url || '#';
  node.style.setProperty('--source-accent', deal.accent || '#121417');
  node.querySelector('.source-pill').textContent = deal.deal_type || deal.source_name;
  const status = node.querySelector('.status-pill');
  status.textContent = deal.status || '판매중';
  if (deal.status !== '판매중') status.classList.add('soldout');
  node.querySelector('.source-category').textContent = sourceCategoryText(deal);
  node.querySelector('.canonical-category').textContent = deal.canonical_category?.label || '기타';
  const img = node.querySelector('img');
  if (deal.image_url) {
    img.dataset.src = deal.image_url;
    img.alt = deal.title;
    if (imageObserver) {
      imageObserver.observe(img);
    } else {
      img.src = deal.image_url;
    }
  } else {
    img.remove();
    node.querySelector('.thumb-wrap').textContent = deal.source_name;
  }
  node.querySelector('h3').textContent = deal.title;
  const noteParts = [];
  if (deal.raw_excerpt?.storeName) noteParts.push(deal.raw_excerpt.storeName);
  if (deal.raw_excerpt?.selQty) noteParts.push(`판매 ${deal.raw_excerpt.selQty}`);
  if (deal.raw_excerpt?.reviews) noteParts.push(`리뷰 ${deal.raw_excerpt.reviews}`);
  node.querySelector('.deal-note').textContent = noteParts.join(' · ') || '원본 페이지에서 상세 조건 확인';
  node.querySelector('.price-label').textContent = deal.price_label || '특가';
  node.querySelector('.deal-price').textContent = formatWon(deal.deal_price);
  const discount = node.querySelector('.discount-badge');
  if (deal.discount_rate || deal.original_price) {
    discount.textContent = deal.discount_rate ? `${deal.discount_rate}%` : formatWon(deal.original_price);
  } else {
    discount.remove();
  }
  node.querySelector('.time-left').textContent = relativeFromNow(deal.ends_at);
  node.querySelector('.checked-at').textContent = checkedAgo(deal.checked_at);
  const badgeRow = node.querySelector('.badge-row');
  for (const badge of deal.badges || []) {
    const span = document.createElement('span');
    span.textContent = badge;
    badgeRow.appendChild(span);
  }
  return node;
}

function renderLane(source, deals) {
  const lane = document.createElement('article');
  lane.className = 'lane';
  lane.style.setProperty('--source-accent', source.accent || '#121417');
  const cats = new Map();
  for (const deal of deals) {
    const label = deal.canonical_category?.label || '기타';
    cats.set(label, (cats.get(label) || 0) + 1);
  }
  const topCats = [...cats.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
  const avgDiscounts = deals.map((d) => d.discount_rate).filter((v) => Number.isFinite(v));
  const avg = avgDiscounts.length ? Math.round(avgDiscounts.reduce((a, b) => a + b, 0) / avgDiscounts.length) : null;
  const header = document.createElement('header');
  header.className = 'lane-header';
  header.append(
    textEl('h2', '', source.name),
    textEl('p', '', `${deals.length}개 수집 · ${topCats.map(([label]) => label).join(' · ') || '카테고리 확인 중'}`)
  );
  const stats = document.createElement('div');
  stats.className = 'lane-stats';
  stats.append(
    textEl('span', '', source.deal_type),
    textEl('span', '', `평균 ${avg ? `${avg}%` : '확인 중'}`),
    textEl('span', '', `${source.count || 0}개 수집`)
  );
  header.appendChild(stats);
  const stack = document.createElement('div');
  stack.className = 'card-stack';
  lane.append(header, stack);
  if (!deals.length) {
    stack.appendChild(textEl('div', 'empty-lane', '선택한 조건에 맞는 딜이 없습니다.'));
    return lane;
  }
  for (const deal of sortDeals(deals)) stack.appendChild(renderCard(deal));
  return lane;
}

function renderLanes(filteredDeals) {
  lanesEl.innerHTML = '';
  const bySource = new Map();
  for (const deal of filteredDeals) {
    if (!bySource.has(deal.source_id)) bySource.set(deal.source_id, []);
    bySource.get(deal.source_id).push(deal);
  }
  for (const sourceId of sourceOrder) {
    const rawSource = state.data.sources[sourceId];
    if (!rawSource) continue;
    const summary = state.data.source_summary[sourceId] || {};
    const source = { ...rawSource, ...summary };
    lanesEl.appendChild(renderLane(source, bySource.get(sourceId) || []));
  }
}

function render() {
  renderFilters();
  const filtered = getFilteredDeals();
  renderSnapshot(filtered);
  renderLanes(filtered);
}

async function loadData() {
  const candidates = ['./data/deals.json'];
  let lastError;
  for (const url of candidates) {
    try {
      const res = await fetch(`${url}?t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return await res.json();
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

function wireControls() {
  $('#searchInput').addEventListener('input', (event) => {
    state.query = event.target.value;
    render();
  });
  $('#sortSelect').addEventListener('change', (event) => {
    state.sort = event.target.value;
    render();
  });
  $('#scrollLeft').addEventListener('click', () => lanesEl.scrollBy({ left: -420, behavior: 'smooth' }));
  $('#scrollRight').addEventListener('click', () => lanesEl.scrollBy({ left: 420, behavior: 'smooth' }));
}

async function refreshData() {
  try {
    state.data = await loadData();
    $('#generatedAt').textContent = formatGeneratedAt(state.data.generated_at);
    const manifestRows = Object.values(state.data.manifest || {});
    const ok = manifestRows.filter((m) => m.status === 'ok').length;
    const stale = manifestRows.filter((m) => m.status === 'stale').length;
    const total = manifestRows.length;
    $('#coverageText').textContent = stale
      ? `${ok}/${total} 신규 수집 성공 · ${stale}개 이전 데이터 유지 · ${UPDATE_CADENCE_LABEL}`
      : `${ok}/${total} 소스 수집 성공 · ${UPDATE_CADENCE_LABEL}`;
    if (!controlsWired) {
      wireControls();
      controlsWired = true;
    }
    render();
  } catch (error) {
    if (!state.data) {
      lanesEl.replaceChildren(textEl('div', 'empty-lane', `데이터를 불러오지 못했습니다. ${error.message}`));
      $('#generatedAt').textContent = '데이터 없음';
      $('#coverageText').textContent = 'scripts/hourly_collect.py 실행 필요';
      return;
    }
    $('#coverageText').textContent = `자동 갱신 실패 · 기존 데이터 유지 · ${UPDATE_CADENCE_LABEL}`;
  }
}

async function init() {
  await refreshData();
  window.setInterval(refreshData, DATA_REFRESH_INTERVAL_MS);
}

init();
