// Solana Smart Money tracker — frontend
let STATE = {
  data: null, tab: "tokens", search: "", tf: "24h", flow: "all", memes: false,
  sortTok: "smart_net_usd", dirTok: -1,
  sortWal: "winrate", dirWal: -1,
  auth: null, lookup: {},
};

function winrateCell(w) {
  if (w.winrate == null) return `<span class="muted" title="not enough closed trades in the recent window">—</span>`;
  const cls = w.winrate >= 60 ? "wr-hi" : w.winrate >= 45 ? "wr-mid" : "wr-lo";
  return `<span class="wr ${cls}">${w.winrate}%</span><span class="wr-n">${w.closed_trades} cl.</span>`;
}

// ---- formatting ----
function usd(n, dp) {
  if (n == null || isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return "$" + n.toFixed(dp ?? 0);
}
function signedUsd(n) {
  if (n == null || isNaN(n)) return "—";
  return (n >= 0 ? "+" : "−") + usd(Math.abs(n));
}
function price(p) {
  if (p == null || isNaN(p) || p === 0) return "—";
  if (p >= 1) return "$" + p.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (p >= 0.01) return "$" + p.toFixed(4);
  return "$" + p.toPrecision(2);
}
function pct(n) {
  if (n == null || isNaN(n)) return "—";
  return (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function tokenLogo(t, cls) {
  const sym = esc((t.symbol || "?").slice(0, 3));
  if (t.logo) return `<img class="${cls}" src="${esc(t.logo)}" onerror="this.outerHTML='<div class=&quot;${cls} ph&quot;>${sym}</div>'">`;
  return `<div class="${cls} ph">${sym}</div>`;
}

// ---- load ----
let _warmupRetry = null;
async function load(force) {
  clearTimeout(_warmupRetry);
  document.getElementById("refresh").textContent = "…";
  const up = document.getElementById("updated");
  up.textContent = "syncing " + STATE.tf + "…";
  try {
    const r = await fetch(`/api/smartmoney?tf=${STATE.tf}&memecoins=${STATE.memes ? 1 : 0}${force ? "&force=1" : ""}`);
    STATE.data = await r.json();
    banner(STATE.data);
    document.getElementById("updated").textContent =
      STATE.data.error === "warming_up" ? "Preparing live data…" :
      STATE.data.error ? "" : `Updated ${STATE.data.updated} · ${STATE.tf}`;
    document.getElementById("cnt-tokens").textContent = STATE.data.token_count ?? 0;
    document.getElementById("cnt-wallets").textContent = STATE.data.wallet_count ?? 0;
    render();
    if (STATE.data.error === "warming_up") _warmupRetry = setTimeout(() => load(false), 3000);
  } catch (e) {
    document.getElementById("tok-body").innerHTML =
      `<tr><td colspan="9" class="loading">Failed to load: ${esc(e)}</td></tr>`;
  }
  document.getElementById("refresh").textContent = "↻";
}

function banner(d) {
  const b = document.getElementById("banner");
  if (d.error === "warming_up") {
    b.className = "banner hidden";
  } else if (d.error === "no_api_key") {
    b.className = "banner";
    b.innerHTML = `<b>No Helius API key.</b> Set <code>HELIUS_API_KEY</code> (or add <code>config.json</code> →
      <code>{"helius_api_key":"…"}</code>) and restart. Free dev key: helius.dev → Dashboard → API Keys.`;
  } else if (d.error && String(d.error).startsWith("auth_")) {
    b.className = "banner";
    b.innerHTML = `<b>Helius rejected the key (${esc(d.error)}).</b> Double-check the key is valid and active.`;
  } else if (d.error) {
    b.className = "banner";
    b.innerHTML = `<b>Data error:</b> ${esc(d.error)}. A trending/swap request failed — retrying on next refresh.`;
  } else {
    b.className = "banner hidden";   // partial scan gaps refill silently on refresh
  }
}

// ---- sorting ----
function sortRows(rows, key, dir) {
  return rows.slice().sort((a, b) => {
    let va = a[key], vb = b[key];
    if (typeof va === "string" || typeof vb === "string")
      return dir * String(va ?? "").localeCompare(String(vb ?? ""));
    return dir * ((va ?? -Infinity) - (vb ?? -Infinity));
  });
}

function tokenRows() {
  if (!STATE.data || !STATE.data.tokens) return [];
  let rs = STATE.data.tokens;
  if (STATE.flow === "acc") rs = rs.filter(t => t.net_label === "Accumulating");
  if (STATE.flow === "dist") rs = rs.filter(t => t.net_label === "Distributing");
  if (STATE.search) {
    const q = STATE.search.toLowerCase();
    rs = rs.filter(t => (t.symbol + " " + t.name + " " + t.address).toLowerCase().includes(q));
  }
  return sortRows(rs, STATE.sortTok, STATE.dirTok);
}
function walletRows() {
  if (!STATE.data || !STATE.data.wallets) return [];
  let rs = STATE.data.wallets;
  if (STATE.search) {
    const q = STATE.search.toLowerCase();
    rs = rs.filter(w => (w.alias + " " + w.wallet + " " + (w.top_tokens || []).join(" ")).toLowerCase().includes(q));
  }
  return sortRows(rs, STATE.sortWal, STATE.dirWal);
}

// ---- render ----
function render() {
  if (!STATE.data) return;
  markSort();
  if (STATE.tab === "tokens") renderTokens(); else renderWallets();
  updateGatebar();
}

// ---- auth / gating UI ----
function updateConnectButton() {
  const btn = document.getElementById("connect");
  const a = STATE.auth;
  if (a && a.authenticated) {
    const pk = a.pubkey ? a.pubkey.slice(0, 4) + "…" + a.pubkey.slice(-4) : "wallet";
    btn.classList.add("connected");
    btn.classList.toggle("premium", !!a.premium);
    btn.innerHTML = a.premium ? `✓ ${pk} · Premium` : `${pk} · Locked`;
    btn.title = "Click to disconnect";
  } else {
    btn.classList.remove("connected", "premium");
    btn.textContent = "Connect Wallet";
    btn.title = "Connect a Solana wallet";
  }
}

function updateGatebar() {
  const bar = document.getElementById("gatebar");
  const g = STATE.data && STATE.data.gating;
  if (!g || !g.active || !g.locked) { bar.className = "gatebar hidden"; return; }
  const a = STATE.auth;
  let cta;
  if (!a || !a.authenticated) {
    cta = `<button class="gate-cta" id="gateConnect">Connect wallet to unlock</button>`;
  } else if (!g.token_set) {
    cta = "";
  } else {
    const bal = a.balance_usd != null ? ` (you hold $${a.balance_usd})` : "";
    cta = `<span class="gate-note">Hold $${g.min_usd} of the token to unlock${bal}.</span>`;
  }
  bar.className = "gatebar";
  bar.innerHTML = `<span class="gate-lead">🔒 ${g.locked} elite wallet${g.locked > 1 ? "s" : ""}
    (win rate ≥ ${g.lock_winrate}%) hidden — win rates shown, identities &amp; coins locked.</span> ${cta}`;
  const gc = document.getElementById("gateConnect");
  if (gc) gc.onclick = () => window.SMwallet && window.SMwallet.open();
}

window.SM = {
  auth: null,
  onAuth(status) {
    STATE.auth = status;
    this.auth = status;
    updateConnectButton();
    try { chatLockState(); } catch (e) {}
    loadTradeStatus().then(() => load(false));
  },
};

function markSort() {
  document.querySelectorAll("#tok-grid th").forEach(th =>
    th.classList.toggle("sorted", th.dataset.sort === STATE.sortTok));
  document.querySelectorAll("#wal-grid th").forEach(th =>
    th.classList.toggle("sorted", th.dataset.sort === STATE.sortWal));
}

function flowCell(t) {
  const tot = t.smart_buy_usd + t.smart_sell_usd || 1;
  const bw = Math.round(t.smart_buy_usd / tot * 100);
  const cls = t.net_label === "Accumulating" ? "acc" : t.net_label === "Distributing" ? "dist" : "mix";
  const nclr = t.smart_net_usd >= 0 ? "up" : "down";
  return `<div class="flowcell">
    <span class="flowval ${nclr}">${signedUsd(t.smart_net_usd)}</span>
    <div class="flowbar"><i class="b" style="width:${bw}%"></i><i class="s" style="width:${100 - bw}%"></i></div>
    <span class="tag ${cls}">${t.net_label}</span>
  </div>`;
}

function renderTokens() {
  let rs = tokenRows();
  const tb = document.getElementById("tok-body");
  const lk = STATE.lookup || {};
  if (!rs.length) {
    if (lk.loading) {
      tb.innerHTML = `<tr><td colspan="9" class="loading">Searching chain for ${esc(lk.query.slice(0, 12))}…</td></tr>`;
      return;
    }
    if (lk.token) { rs = [lk.token]; }            // off-board token resolved by live lookup
    else if (lk.error) {
      tb.innerHTML = `<tr><td colspan="9" class="loading">${lk.error === "not_found"
        ? "Token not found on any Solana DEX." : lk.error === "not_an_address"
        ? "No tokens match." : "Lookup failed — try again."}</td></tr>`;
      return;
    } else {
      tb.innerHTML = `<tr><td colspan="9" class="loading">${STATE.data.error ? "No data — see notice above." : "No tokens match."}</td></tr>`;
      return;
    }
  }
  tb.innerHTML = rs.map((t, i) => `
    <tr data-i="${i}">
      <td class="lft"><div class="tok">${tokenLogo(t, "")}
        <div><div class="nm">${esc(t.symbol)}</div><div class="sb">${esc((t.name || "").slice(0, 22))}</div></div></div></td>
      <td>${price(t.price)}</td>
      <td class="${t.price_change_24h >= 0 ? "up" : "down"}">${pct(t.price_change_24h)}</td>
      <td>${flowCell(t)}</td>
      <td class="up">${usd(t.smart_buy_usd)}</td>
      <td class="down">${usd(t.smart_sell_usd)}</td>
      <td><span class="wcount"><span class="dot"></span>${t.smart_wallets}</span></td>
      <td class="muted">${usd(t.volume_24h)}</td>
      <td class="muted">${usd(t.mcap)}</td>
    </tr>`).join("");
  tb.querySelectorAll("tr[data-i]").forEach(tr =>
    tr.onclick = () => openTokenDrawer(rs[+tr.dataset.i]));
}

function renderWallets() {
  const rs = walletRows();
  const tb = document.getElementById("wal-body");
  if (!rs.length) {
    tb.innerHTML = `<tr><td colspan="9" class="loading">${STATE.data.error ? "No data — see notice above." : "No wallets match."}</td></tr>`;
    return;
  }
  const minUsd = STATE.data.gating ? STATE.data.gating.min_usd : 5;
  tb.innerHTML = rs.map((w, i) => {
    const ini = (w.alias || "?").split(" ").map(s => s[0]).join("").slice(0, 2);
    const idCell = w.gated
      ? `<div class="walletcell"><span class="av lock-av">🔒</span>
           <div><div class="al blurred">${esc(w.alias || "Smart Wallet")}</div>
             <div class="addr lock-msg">Hold $${minUsd} to reveal · copy locked</div></div></div>`
      : `<div class="walletcell"><span class="av">${esc(ini)}</span>
           <div><div class="al">${esc(w.alias)}</div><div class="addr">${esc(w.short)}</div></div></div>`;
    const tokCell = w.gated
      ? `<span class="muted">🔒</span>`
      : (w.top_tokens || []).slice(0, 4).map(s => `<span class="pill">${esc(s)}</span>`).join("");
    return `<tr data-i="${i}" class="${w.gated ? "row-locked" : ""}">
      <td class="lft">${idCell}</td>
      <td>${winrateCell(w)}</td>
      <td class="${w.net_usd >= 0 ? "up" : "down"}">${signedUsd(w.net_usd)}</td>
      <td class="up">${usd(w.buy_usd)}</td>
      <td class="down">${usd(w.sell_usd)}</td>
      <td>${usd(w.volume_usd)}</td>
      <td>${w.tokens_traded}</td>
      <td class="muted">${w.trades}</td>
      <td class="lft">${tokCell}</td>
    </tr>`;
  }).join("");
  tb.querySelectorAll("tr[data-i]").forEach(tr =>
    tr.onclick = () => {
      const w = rs[+tr.dataset.i];
      if (w.gated) { window.SMwallet && window.SMwallet.open(); }
      else openWalletDrawer(w);
    });
}

// ---- drawers ----
function sparkline(arr) {
  if (!arr || arr.length < 2) return "";
  const w = 524, h = 64, min = Math.min(...arr), max = Math.max(...arr), rng = max - min || 1;
  const pts = arr.map((v, i) => `${(i / (arr.length - 1) * w).toFixed(1)},${(h - (v - min) / rng * h).toFixed(1)}`).join(" ");
  const col = arr[arr.length - 1] >= arr[0] ? "var(--buy)" : "var(--sell)";
  return `<div class="spark"><svg width="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2"/></svg></div>`;
}

function showOverlay(html) {
  document.getElementById("drawerBody").innerHTML = html;
  document.getElementById("overlay").classList.remove("hidden");
}

function traderRow(t) {
  return `<div class="trow">
    <span class="av">${esc((t.alias || "?").split(" ").map(s => s[0]).join("").slice(0, 2))}</span>
    <div><div class="al">${esc(t.alias)}</div><div class="ad">${esc(t.short)}</div></div>
    <span class="nu ${t.net_usd >= 0 ? "up" : "down"}">${signedUsd(t.net_usd)}</span>
  </div>`;
}

async function openTokenDrawer(t) {
  showOverlay(`<div class="d-head">${tokenLogo(t, "")}
      <div><div class="d-title">${esc(t.symbol)}</div><div class="d-sub">${esc(t.name)}</div></div></div>
    <div class="d-sub">${esc(t.address)} <button class="copy" data-copy="${esc(t.address)}">copy</button>
      <a class="ext" href="https://dexscreener.com/solana/${esc(t.address)}" target="_blank">DexScreener ↗</a>
      <a class="ext" href="https://www.geckoterminal.com/solana/tokens/${esc(t.address)}" target="_blank">GeckoTerminal ↗</a>
      <a class="ext" href="https://solscan.io/token/${esc(t.address)}" target="_blank">Solscan ↗</a>
      ${tradeBtn(t.address, t.symbol)}</div>
    <div class="d-meta">
      <div><span>Price</span><b>${price(t.price)}</b></div>
      <div><span>24h</span><b class="${t.price_change_24h >= 0 ? "up" : "down"}">${pct(t.price_change_24h)}</b></div>
      <div><span>Smart Net</span><b class="${t.smart_net_usd >= 0 ? "up" : "down"}">${signedUsd(t.smart_net_usd)}</b></div>
      <div><span>Mkt Cap</span><b>${usd(t.mcap)}</b></div>
      <div><span>Liquidity</span><b>${usd(t.liquidity)}</b></div>
    </div>
    <div id="d-spark" class="spark muted" style="font-size:11px">loading price…</div>
    <div class="block"><h3><span>Smart traders (${STATE.tf})</span><span>buy/sell net</span></h3>
      ${(t.top_traders || []).map(traderRow).join("") || '<div class="trow muted">No trader data.</div>'}</div>`);
  hookCopy();
  // lazy-load richer detail (sparkline + freshest traders)
  try {
    const r = await fetch(`/api/token?address=${t.address}&tf=${STATE.tf}`);
    const d = await r.json();
    const el = document.getElementById("d-spark");
    if (el) el.outerHTML = d.spark && d.spark.length ? sparkline(d.spark) : "";
  } catch (e) { /* keep static view */ }
}

async function openWalletDrawer(w) {
  const ini = (w.alias || "?").split(" ").map(s => s[0]).join("").slice(0, 2);
  showOverlay(`<div class="d-head"><span class="av" style="width:46px;height:46px;font-size:16px">${esc(ini)}</span>
      <div><div class="d-title">${esc(w.alias)}</div><div class="d-sub">${esc(w.short)}</div></div></div>
    <div class="d-sub">${esc(w.wallet)} <button class="copy" data-copy="${esc(w.wallet)}">copy</button>
      <a class="ext" href="https://gmgn.ai/sol/address/${esc(w.wallet)}" target="_blank">GMGN ↗</a>
      <a class="ext" href="https://solscan.io/account/${esc(w.wallet)}" target="_blank">Solscan ↗</a></div>
    <div class="d-meta">
      <div><span>Win rate</span><b class="${w.winrate >= 60 ? "up" : w.winrate == null ? "muted" : ""}">${w.winrate == null ? "—" : w.winrate + "%"}</b></div>
      <div><span>Closed trades</span><b>${w.closed_trades || 0}</b></div>
      ${w.realized_pnl != null ? `<div><span>Realized PnL</span><b class="${w.realized_pnl >= 0 ? "up" : "down"}">${signedUsd(w.realized_pnl)}</b></div>` : ""}
      <div><span>Net Flow</span><b class="${w.net_usd >= 0 ? "up" : "down"}">${signedUsd(w.net_usd)}</b></div>
      <div><span>Volume</span><b>${usd(w.volume_usd)}</b></div>
      <div><span>Tokens</span><b>${w.tokens_traded}</b></div>
    </div>
    <div class="block"><h3><span>Memecoin positions (${STATE.tf})</span><span>net flow</span></h3>
      ${(w.tokens || []).map(tk => `<div class="trow"><div class="al">${esc(tk.symbol)}</div>
        <span class="ad">${esc((tk.address || "").slice(0, 6))}…</span>
        <span class="nu ${tk.net_usd >= 0 ? "up" : "down"}">${signedUsd(tk.net_usd)}</span></div>`).join("")}</div>`);
  hookCopy();
}

function hookCopy() {
  document.querySelectorAll(".copy").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    navigator.clipboard?.writeText(b.dataset.copy);
    b.textContent = "copied";
    setTimeout(() => (b.textContent = "copy"), 1200);
  });
}

// ---- pump.fun radar ----
STATE.pf = { smart: false, view: "new", data: null, loaded: false };

function bondCell(c) {
  if (c.bonded) return `<div class="bondcell"><span class="tag acc">Bonded</span></div>`;
  const pct = c.bond_pct ?? 0;
  const sol = c.sol_raised != null ? `${c.sol_raised} / 85 SOL` : "";
  const vel = c.vel > 0 ? `<span class="vel">🔥 +${c.vel} SOL/min</span>` : "";
  return `<div class="bondcell">
    <div class="bondbar"><i style="width:${pct}%"></i></div>
    <span class="bond-lbl">${pct}% <span class="muted">${sol}</span></span>${vel}
  </div>`;
}

function fmtAge(s) {
  if (s == null) return "—";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + (s % 60) + "s";
  return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
}

async function loadPumpfun() {
  try {
    const r = await fetch(`/api/pumpfun?smart=${STATE.pf.smart ? 1 : 0}&view=${STATE.pf.view}`);
    STATE.pf.data = await r.json();
    STATE.pf.loaded = true;
    document.getElementById("cnt-pf").textContent = STATE.pf.data.locked ? "🔒" : (STATE.pf.data.count ?? 0);
    renderPumpfun();
  } catch (e) {
    document.getElementById("pf-body").innerHTML =
      `<tr><td colspan="7" class="loading">Failed to load: ${esc(e)}</td></tr>`;
  }
}

function renderPumpfun() {
  const d = STATE.pf.data;
  if (!d) return;
  const lock = document.getElementById("pf-lock");
  const wrap = document.getElementById("pf-wrap");
  if (d.locked) {
    const m = d.milestone || {};
    wrap.classList.add("hidden");
    lock.classList.remove("hidden");
    lock.innerHTML = `
      <div class="pf-lock-card">
        <div class="pf-lock-icon">🔒</div>
        <h3>Pump.fun Launch Radar unlocks at $${(m.target || 100000).toLocaleString()} market cap</h3>
        <p>This feature auto-releases for <b>everyone</b> the moment our token reaches the milestone.
           ${m.token_set ? "" : "Token not launched yet — stay tuned."}</p>
        <div class="pf-progress"><i style="width:${m.progress || 0}%"></i></div>
        <div class="pf-progress-lbl">$${(m.mcap || 0).toLocaleString()} / $${(m.target || 100000).toLocaleString()} (${m.progress || 0}%)</div>
      </div>`;
    return;
  }
  lock.classList.add("hidden");
  wrap.classList.remove("hidden");
  document.getElementById("pf-updated").textContent = d.updated ? `Updated ${d.updated}` : "";
  document.getElementById("pf-note").textContent = STATE.pf.smart
    ? "Only launches already bought by a tracked smart-money wallet."
    : "Live feed of every new pump.fun launch — including un-bonded tokens seconds old.";
  const tb = document.getElementById("pf-body");
  let rows = d.coins || [];
  if (STATE.search) {
    const q = STATE.search.toLowerCase();
    rows = rows.filter(c => (c.symbol + " " + c.name + " " + c.mint).toLowerCase().includes(q));
  }
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="7" class="loading">${STATE.pf.smart
      ? "No smart-money buys detected in the newest launches yet — they refresh every few seconds."
      : "No launches returned — pump.fun API may be busy, retrying…"}</td></tr>`;
    return;
  }
  const now = Date.now();
  tb.innerHTML = rows.map(c => {
    const age = c.created_ms ? Math.max(0, Math.floor((now - c.created_ms) / 1000)) : c.age_s;
    const smart = c.smart_buyers
      ? `<span class="wcount"><span class="dot"></span>${c.smart_buyers}</span>
         <div class="pf-aliases">${(c.smart_aliases || []).map(a => `<span class="pill">${esc(a)}</span>`).join("")}</div>`
      : `<span class="muted">—</span>`;
    const trade = (c.bonded ? tradeBtn(c.mint, c.symbol) : "") +
      (c.bonded
        ? `<a class="ext" href="https://jup.ag/swap/SOL-${esc(c.mint)}" target="_blank" onclick="event.stopPropagation()">Jupiter ↗</a><br>
           <a class="ext" href="https://pump.fun/coin/${esc(c.mint)}" target="_blank" onclick="event.stopPropagation()">pump.fun ↗</a>`
        : `<a class="ext" href="https://pump.fun/coin/${esc(c.mint)}" target="_blank" onclick="event.stopPropagation()">pump.fun ↗</a>`);
    return `<tr>
      <td class="lft"><a class="tok tok-link" href="https://pump.fun/coin/${esc(c.mint)}" target="_blank" rel="noopener">${tokenLogo({ symbol: c.symbol, logo: c.image }, "")}
        <div><div class="nm">${esc(c.symbol)} <span class="ext-mini">↗</span></div><div class="sb">${esc((c.name || "").slice(0, 24))}</div></div></a></td>
      <td class="pf-age ${age <= 60 ? "up" : "muted"}" data-ts="${c.created_ms || ""}">${fmtAge(age)}</td>
      <td>${usd(c.usd_mcap)}</td>
      <td>${bondCell(c)}</td>
      <td class="muted">${c.replies ?? 0}</td>
      <td>${smart}</td>
      <td>${trade}</td>
    </tr>`;
  }).join("");
}

// tick ages every second; refresh feed every 6s while the tab is open
setInterval(() => {
  if (STATE.tab !== "pumpfun") return;
  const now = Date.now();
  document.querySelectorAll(".pf-age[data-ts]").forEach(td => {
    const ts = +td.dataset.ts;
    if (ts) td.textContent = fmtAge(Math.max(0, Math.floor((now - ts) / 1000)));
  });
}, 1000);
setInterval(() => { if (STATE.tab === "pumpfun") loadPumpfun(); }, 6000);

// ---- in-app trading (Jupiter; locked until token bonds, admins preview) ----
STATE.trade = { unlocked: false, reason: "", t: null };

async function loadTradeStatus() {
  try {
    const d = await (await fetch("/api/trade/status")).json();
    STATE.trade.unlocked = !!d.unlocked;
    STATE.trade.reason = d.reason || "";
  } catch (e) { STATE.trade.unlocked = false; }
}

function tradeBtn(mint, symbol) {
  if (!STATE.trade.unlocked) return "";
  return `<button class="t-open" data-mint="${esc(mint)}" data-sym="${esc(symbol)}"
    onclick="event.stopPropagation();openTrade(this.dataset.mint,this.dataset.sym)">⚡ TRADE</button>`;
}

const T = { mint: null, sym: "", side: "buy", amount: 0.1, pct: 50, ok: false };
let _quoteTimer = null, _quoteInterval = null, _quoteSeq = 0;

function tmodalOpen() { return !document.getElementById("tmodal").classList.contains("hidden"); }

function openTrade(mint, sym) {
  if (!window.SMwallet || !window.SMwallet.getAccount()) {
    if (window.SMwallet) window.SMwallet.open();
    return;
  }
  T.mint = mint; T.sym = sym; T.side = "buy"; T.amount = 0.1; T.pct = 50; T.ok = false;
  document.getElementById("tmodalTitle").textContent = `Trade ${sym}`;
  document.getElementById("t-preview").innerHTML = "";
  document.getElementById("t-msg").textContent = ""; document.getElementById("t-msg").className = "t-msg";
  document.getElementById("t-exec").disabled = true;
  document.getElementById("tmodal").classList.remove("hidden");
  renderTradeSide();                       // triggers the first auto-quote
  clearInterval(_quoteInterval);
  _quoteInterval = setInterval(() => { if (tmodalOpen()) tradeQuote(); }, 10000);  // keep it fresh
}

function closeTrade() {
  document.getElementById("tmodal").classList.add("hidden");
  clearInterval(_quoteInterval); clearTimeout(_quoteTimer);
}

function scheduleQuote(delay = 250) {
  clearTimeout(_quoteTimer);
  const live = document.getElementById("t-live");
  if (live) { live.textContent = "quoting…"; live.className = "t-live on"; }
  _quoteTimer = setTimeout(tradeQuote, delay);
}

function renderTradeSide() {
  document.getElementById("t-buy").classList.toggle("active", T.side === "buy");
  document.getElementById("t-sell").classList.toggle("active", T.side === "sell");
  const el = document.getElementById("t-amounts");
  if (T.side === "buy") {
    el.innerHTML = `<span class="t-lbl">Spend</span>` +
      [0.05, 0.1, 0.5, 1].map(v =>
        `<button class="t-amt ${T.amount === v ? "active" : ""}" data-v="${v}">${v} SOL</button>`).join("") +
      `<input id="t-custom" type="number" min="0.001" step="0.01" placeholder="custom" value="${[0.05,0.1,0.5,1].includes(T.amount) ? "" : T.amount}">`;
  } else {
    el.innerHTML = `<span class="t-lbl">Sell</span>` +
      [25, 50, 100].map(v =>
        `<button class="t-amt ${T.pct === v ? "active" : ""}" data-v="${v}">${v}%</button>`).join("") +
      `<span class="t-lbl" style="margin-left:4px">of balance</span>`;
  }
  el.querySelectorAll(".t-amt").forEach(b => b.onclick = () => {
    if (T.side === "buy") T.amount = parseFloat(b.dataset.v); else T.pct = parseFloat(b.dataset.v);
    renderTradeSide();
  });
  const c = document.getElementById("t-custom");
  if (c) c.oninput = () => { const v = parseFloat(c.value); if (v > 0) { T.amount = v; scheduleQuote(500); } };
  scheduleQuote(250);                      // auto-quote on every input change
}

function tradeBody() {
  const body = { side: T.side, mint: T.mint,
    slippage_bps: +document.getElementById("t-slippage").value };
  if (T.side === "buy") body.sol_amount = T.amount; else body.pct = T.pct;
  return body;
}

async function tradeQuote() {
  if (!tmodalOpen() || !T.mint) return;
  const msg = document.getElementById("t-msg"), pv = document.getElementById("t-preview");
  const live = document.getElementById("t-live");
  const seq = ++_quoteSeq;
  try {
    const d = await (await fetch("/api/trade/quote", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(tradeBody()) })).json();
    if (seq !== _quoteSeq) return;         // a newer quote superseded this one
    if (d.error) {
      msg.textContent = d.reason || d.error; msg.className = "t-msg err";
      pv.innerHTML = ""; T.ok = false;
      document.getElementById("t-exec").disabled = true;
      if (live) { live.textContent = "no route"; live.className = "t-live"; }
      return;
    }
    const s = d.summary;
    pv.innerHTML = T.side === "buy"
      ? `<div>› You pay <b>${s.in}</b></div>
         <div>› You receive ≈ <b class="up">${s.out_ui != null ? s.out_ui.toLocaleString(undefined,{maximumFractionDigits:0}) : "?"} ${esc(T.sym)}</b></div>
         <div class="muted">price impact ${s.price_impact_pct ?? "?"}% · slippage ${(s.slippage_bps/100)}%</div>`
      : `<div>› You sell <b>${s.in_ui != null ? s.in_ui.toLocaleString(undefined,{maximumFractionDigits:0}) : "?"} ${esc(T.sym)}</b> (${s.pct}%)</div>
         <div>› You receive ≈ <b class="up">${(s.out_sol ?? 0).toFixed(4)} SOL</b></div>
         <div class="muted">price impact ${s.price_impact_pct ?? "?"}% · slippage ${(s.slippage_bps/100)}%</div>`;
    msg.textContent = ""; msg.className = "t-msg"; T.ok = true;
    document.getElementById("t-exec").disabled = false;
    if (live) { live.textContent = "● live · auto-updates"; live.className = "t-live ok"; }
  } catch (e) {
    if (seq !== _quoteSeq) return;
    if (live) { live.textContent = "retry…"; live.className = "t-live"; }
  }
}

async function tradeExec() {
  const msg = document.getElementById("t-msg");
  const btn = document.getElementById("t-exec");
  btn.disabled = true;
  msg.className = "t-msg";
  try {
    msg.textContent = "building transaction…";
    const d = await (await fetch("/api/trade/swap", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(tradeBody(true)) })).json();
    if (d.error || !d.tx) { msg.textContent = d.reason || d.error || "build failed"; msg.className = "t-msg err"; btn.disabled = false; return; }
    msg.textContent = "approve in your wallet…";
    const sig = await window.SMwallet.signAndSend(d.tx);
    msg.className = "t-msg ok";
    msg.innerHTML = `✓ sent — <a class="ext" href="https://solscan.io/tx/${sig}" target="_blank">view on Solscan ↗</a>`;
  } catch (e) {
    msg.textContent = "cancelled or failed: " + (e.message || e);
    msg.className = "t-msg err";
    btn.disabled = false;
  }
}

// ---- community chat ----
let _chatLastId = 0, _chatOpen = false;
function openChat() {
  _chatOpen = true;
  document.getElementById("chatPanel").classList.remove("hidden");
  document.getElementById("chatToggle").classList.add("on");
  loadChat(true);
}
function closeChat() {
  _chatOpen = false;
  document.getElementById("chatPanel").classList.add("hidden");
  document.getElementById("chatToggle").classList.remove("on");
}
function chatLockState() {
  const logged = !!(STATE.auth && STATE.auth.authenticated);
  const lock = document.getElementById("chat-locked");
  const row = document.getElementById("chat-input-row");
  if (lock) lock.classList.toggle("hidden", logged);
  if (row) row.classList.toggle("hidden", !logged);
}
function chatMsgHtml(m) {
  const t = new Date(m.ts * 1000).toISOString().slice(11, 16);
  const sh = m.pubkey ? m.pubkey.slice(0, 4) + "…" + m.pubkey.slice(-4) : "";
  return `<div class="cmsg"><div class="cmsg-h"><b>${esc(m.alias || "anon")}</b>
    <span class="cmsg-a">${esc(sh)}</span><span class="cmsg-t">${t}</span></div>
    <div class="cmsg-b">${esc(m.text)}</div></div>`;
}
async function loadChat(forceScroll) {
  try {
    const r = await fetch(`/api/chat?after=${_chatLastId}`);
    const d = await r.json();
    const ai = document.getElementById("chat-ai");
    if (ai) ai.textContent = d.ai ? "AI-moderated" : "moderated";
    const box = document.getElementById("chat-msgs");
    if (d.messages && d.messages.length && box) {
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
      d.messages.forEach(m => { _chatLastId = Math.max(_chatLastId, m.id); box.insertAdjacentHTML("beforeend", chatMsgHtml(m)); });
      if (forceScroll || atBottom) box.scrollTop = box.scrollHeight;
    } else if (box && !box.children.length) {
      box.innerHTML = `<div class="chat-empty">No messages yet. Be the first — keep it civil.</div>`;
    }
    chatLockState();
  } catch (e) { /* keep panel */ }
}
async function sendChat() {
  const inp = document.getElementById("chat-input");
  const note = document.getElementById("chat-note");
  const text = inp.value.trim();
  if (!text) return;
  note.textContent = "";
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    const d = await r.json();
    if (d.error) { note.textContent = d.reason || "Message blocked."; note.className = "chat-note err"; return; }
    inp.value = "";
    note.textContent = ""; note.className = "chat-note";
    loadChat(true);
  } catch (e) { note.textContent = "Failed to send."; note.className = "chat-note err"; }
}

// ---- events ----
function switchTab(name) {
  STATE.tab = name;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.getElementById("view-tokens").classList.toggle("hidden", name !== "tokens");
  document.getElementById("view-wallets").classList.toggle("hidden", name !== "wallets");
  document.getElementById("view-pumpfun").classList.toggle("hidden", name !== "pumpfun");
  if (name === "pumpfun") loadPumpfun();
  else render();
}
document.getElementById("pf-smart").onchange = e => {
  STATE.pf.smart = e.target.checked;
  // smart cross-reference only runs on the fresh-launch view — switch to it if needed
  if (STATE.pf.smart && STATE.pf.view !== "new") {
    STATE.pf.view = "new";
    document.getElementById("pf-status").value = "new";
  }
  loadPumpfun();
};
document.getElementById("pf-status").onchange = e => {
  STATE.pf.view = e.target.value;
  if (STATE.pf.view !== "new" && STATE.pf.smart) {     // smart filter is new-view only
    STATE.pf.smart = false;
    document.getElementById("pf-smart").checked = false;
  }
  loadPumpfun();
};
document.getElementById("tmodalClose").onclick = closeTrade;
document.getElementById("tmodal").onclick = e => { if (e.target.id === "tmodal") closeTrade(); };
document.getElementById("t-buy").onclick = () => { T.side = "buy"; renderTradeSide(); };
document.getElementById("t-sell").onclick = () => { T.side = "sell"; renderTradeSide(); };
document.getElementById("t-slippage").onchange = () => scheduleQuote(0);
document.getElementById("t-exec").onclick = tradeExec;
document.querySelectorAll(".tab").forEach(t => t.onclick = () => switchTab(t.dataset.tab));
document.querySelectorAll("#tok-grid th[data-sort]").forEach(th => th.onclick = () => {
  if (STATE.sortTok === th.dataset.sort) STATE.dirTok *= -1;
  else { STATE.sortTok = th.dataset.sort; STATE.dirTok = th.dataset.sort === "name" ? 1 : -1; }
  render();
});
document.querySelectorAll("#wal-grid th[data-sort]").forEach(th => th.onclick = () => {
  if (STATE.sortWal === th.dataset.sort) STATE.dirWal *= -1;
  else { STATE.sortWal = th.dataset.sort; STATE.dirWal = th.dataset.sort === "alias" ? 1 : -1; }
  render();
});
document.getElementById("closeDrawer").onclick = () => document.getElementById("overlay").classList.add("hidden");
document.getElementById("overlay").onclick = e => { if (e.target.id === "overlay") e.currentTarget.classList.add("hidden"); };
document.getElementById("refresh").onclick = () => load(true);
let _lookupTimer = null;
function onSearch(val) {
  STATE.search = val.trim();
  if (STATE.tab === "pumpfun") { renderPumpfun(); return; }
  render();
  if (STATE.tab !== "tokens") return;
  const q = STATE.search;
  const isAddr = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(q);
  clearTimeout(_lookupTimer);
  if (!isAddr || tokenRows().length) { STATE.lookup = {}; renderTokens(); return; }
  STATE.lookup = { query: q, loading: true };
  renderTokens();
  _lookupTimer = setTimeout(() => doLookup(q), 350);
}
async function doLookup(addr) {
  try {
    const r = await fetch(`/api/lookup?address=${encodeURIComponent(addr)}&tf=${STATE.tf}`);
    const d = await r.json();
    if (STATE.search !== addr) return;             // user kept typing — stale
    STATE.lookup = { query: addr, token: d.token || null, error: d.error || null, loading: false };
  } catch (e) { STATE.lookup = { query: addr, error: "failed", loading: false }; }
  renderTokens();
}
document.getElementById("search").oninput = e => onSearch(e.target.value);
document.getElementById("chatToggle").onclick = () => _chatOpen ? closeChat() : openChat();
document.getElementById("chatClose").onclick = closeChat;
document.getElementById("chat-send").onclick = sendChat;
document.getElementById("chat-locked").onclick = () => window.SMwallet && window.SMwallet.open();
document.getElementById("chat-input").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); sendChat(); }
});
setInterval(() => { if (_chatOpen) loadChat(false); }, 4000);
document.getElementById("tf").onchange = e => { STATE.tf = e.target.value; load(false); };
document.getElementById("flow").onchange = e => { STATE.flow = e.target.value; render(); };
document.getElementById("memes").onchange = e => { STATE.memes = e.target.checked; load(false); };
document.addEventListener("keydown", e => { if (e.key === "Escape") document.getElementById("overlay").classList.add("hidden"); });

(async () => {
  await loadTradeStatus();
  await load(false);
})();
setInterval(() => load(false), 300000);
