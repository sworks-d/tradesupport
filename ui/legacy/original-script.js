const backdrop = document.getElementById('detail-backdrop');

function openPanel(panelId) {
  document.querySelectorAll('.detail-panel.show').forEach(p => p.classList.remove('show'));
  const panel = document.querySelector(`[data-panel="${panelId}"]`);
  if (panel) {
    panel.classList.add('show');
    backdrop.classList.add('show');
  }
}

function closePanels() {
  document.querySelectorAll('.detail-panel.show').forEach(p => p.classList.remove('show'));
  backdrop.classList.remove('show');
}

document.querySelectorAll('[data-detail]').forEach(item => {
  item.addEventListener('click', () => openPanel(item.dataset.detail));
});

document.querySelectorAll('.detail-close-btn').forEach(btn => {
  btn.addEventListener('click', closePanels);
});

backdrop.addEventListener('click', closePanels);

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closePanels();
});

// === Refresh button (price update) ===
let lastUpdateTime = Date.now();

function formatRelative(ms) {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}秒前`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}分前`;
  const hour = Math.floor(min / 60);
  return `${hour}時間前`;
}

function updateRelativeTime() {
  const el = document.getElementById('updated-time');
  if (!el) return;
  const elapsed = Date.now() - lastUpdateTime;
  el.textContent = elapsed < 5000 ? 'たった今' : formatRelative(elapsed);

  // Update sidebar relative time too
  const sbTime = document.querySelector('.sb-last-updated span:first-child');
  if (sbTime) sbTime.textContent = (elapsed < 5000 ? 'たった今' : formatRelative(elapsed)) + ' 取得';
}

setInterval(updateRelativeTime, 10000);

function simulatePriceUpdate() {
  // Update lastUpdateTime
  lastUpdateTime = Date.now();
  updateRelativeTime();

  // Simulate slight price changes on holdings (visual only, demo)
  const priceCells = document.querySelectorAll('.hold-price, .hold-pnl');
  priceCells.forEach(cell => {
    const isUp = Math.random() > 0.5;
    cell.classList.remove('flash-up', 'flash-down');
    void cell.offsetWidth; // force reflow
    cell.classList.add(isUp ? 'flash-up' : 'flash-down');
  });

  // Flash sidebar equity too
  const eq = document.querySelector('.sb-equity-value');
  if (eq) {
    eq.classList.remove('flash-up', 'flash-down');
    void eq.offsetWidth;
    eq.classList.add('flash-up');
  }
}

function doRefresh(btn) {
  if (btn.classList.contains('spinning')) return;
  btn.classList.add('spinning');

  // Simulate API delay (in real app: fetch prices from moomoo)
  setTimeout(() => {
    simulatePriceUpdate();
    btn.classList.remove('spinning');
  }, 700);
}

const refreshBtn = document.getElementById('refresh-btn');
if (refreshBtn) {
  refreshBtn.addEventListener('click', () => doRefresh(refreshBtn));
}

const sbRefreshBtn = document.getElementById('sb-refresh-btn');
if (sbRefreshBtn) {
  sbRefreshBtn.addEventListener('click', () => doRefresh(sbRefreshBtn));
}

// === Topics tabs (filter by category) ===
document.querySelectorAll('.topics-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const cat = tab.dataset.topicTab;
    document.querySelectorAll('.topics-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.topic').forEach(topic => {
      if (cat === 'all') {
        topic.style.display = '';
      } else {
        topic.style.display = topic.dataset.cat === cat ? '' : 'none';
      }
    });
  });
});

// === Collapsible "more" sections ===
document.querySelectorAll('.more-toggle').forEach(toggle => {
  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const id = toggle.dataset.toggle;
    const list = document.querySelector(`[data-list="${id}"]`);
    toggle.classList.toggle('open');
    if (list) list.classList.toggle('open');
  });
});

// === Hover tooltip for holding graphs ===
const tooltip = document.createElement('div');
tooltip.className = 'tooltip';
document.body.appendChild(tooltip);

document.querySelectorAll('.hover-point').forEach(point => {
  point.addEventListener('mouseenter', (e) => {
    const date = point.dataset.date;
    const actual = point.dataset.actual;
    const pred = point.dataset.pred;
    const graph = point.closest('.hold-graph');
    const ticker = graph ? graph.dataset.graph : '';

    const actualClass = actual.startsWith('-') ? 'down' : actual.startsWith('+') && actual !== '+0%' ? 'up' : '';

    tooltip.innerHTML = `
      <div class="tooltip-row"><span class="lbl">${ticker} · ${date}</span></div>
      <div class="tooltip-row"><span class="lbl">実績</span><span class="val ${actualClass}">${actual}</span></div>
      <div class="tooltip-row"><span class="lbl">予測</span><span class="val key">${pred}</span></div>
    `;
    tooltip.classList.add('show');
  });

  point.addEventListener('mousemove', (e) => {
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    let x = e.clientX + 12;
    let y = e.clientY - th - 8;
    if (x + tw > window.innerWidth) x = e.clientX - tw - 12;
    if (y < 0) y = e.clientY + 16;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  });

  point.addEventListener('mouseleave', () => {
    tooltip.classList.remove('show');
  });
});

// === Track record points hover ===
document.querySelectorAll('.track-point').forEach(point => {
  point.addEventListener('mouseenter', () => {
    const date = point.dataset.date;
    const ticker = point.dataset.ticker;
    const action = point.dataset.action;
    const result = point.dataset.result;
    const hit = point.dataset.hit;

    let resultClass = '';
    if (hit === 'hit') resultClass = 'up';
    else if (hit === 'miss') resultClass = 'down';
    else if (hit === 'now') resultClass = 'key';

    tooltip.innerHTML = `
      <div class="tooltip-row"><span class="lbl">${date}</span><span class="val">${ticker}</span></div>
      <div class="tooltip-row"><span class="lbl">アクション</span><span class="val">${action}</span></div>
      <div class="tooltip-row"><span class="lbl">結果</span><span class="val ${resultClass}">${result}</span></div>
    `;
    tooltip.classList.add('show');
  });

  point.addEventListener('mousemove', (e) => {
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    let x = e.clientX + 12;
    let y = e.clientY - th - 8;
    if (x + tw > window.innerWidth) x = e.clientX - tw - 12;
    if (y < 0) y = e.clientY + 16;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  });

  point.addEventListener('mouseleave', () => {
    tooltip.classList.remove('show');
  });
});
