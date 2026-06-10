// Wallet login via the Solana Wallet Standard — auto-detects EVERY installed wallet
// (Phantom, Solflare, Backpack, OKX, Coinbase, Glow, Trust, …) with no hardcoding.
// The standard library is loaded lazily so the button + session restore work regardless.

const $ = (id) => document.getElementById(id);
const enc = new TextEncoder();
const b64 = (bytes) => { let s = ""; bytes.forEach((b) => (s += String.fromCharCode(b))); return btoa(s); };

let API = null;
let WALLETS = [];

async function ensureLib() {
  if (API) return API;
  const mod = await import("https://esm.sh/@wallet-standard/app@1.1.0");
  API = mod.getWallets();
  API.on("register", refresh);
  API.on("unregister", refresh);
  return API;
}

function solanaWallets() {
  try {
    return API.get().filter((w) =>
      (w.chains || []).some((c) => c.startsWith("solana:")) &&
      w.features["standard:connect"] && w.features["solana:signMessage"]);
  } catch (e) { return []; }
}

function refresh() { WALLETS = solanaWallets(); renderWalletList(); }

function renderWalletList() {
  const el = $("walletList");
  if (!el) return;
  if (!WALLETS.length) {
    el.innerHTML = `<div class="wallet-empty">No Solana wallet detected. Install
      <a href="https://phantom.app" target="_blank">Phantom</a>,
      <a href="https://solflare.com" target="_blank">Solflare</a> or
      <a href="https://backpack.app" target="_blank">Backpack</a>, then reopen this dialog.</div>`;
    return;
  }
  el.innerHTML = WALLETS.map((w, i) =>
    `<button class="wallet-btn" data-i="${i}">
       <img src="${w.icon}" alt="" onerror="this.style.visibility='hidden'">
       <span class="wallet-name">${w.name}</span>
       <span class="wallet-go">Sign in</span>
     </button>`).join("");
  el.querySelectorAll(".wallet-btn").forEach((b) =>
    (b.onclick = () => login(WALLETS[+b.dataset.i])));
}

async function login(wallet) {
  const msg = $("wmodalMsg");
  try {
    msg.textContent = `Opening ${wallet.name}…`;
    const { accounts } = await wallet.features["standard:connect"].connect();
    const account = accounts[0];
    if (!account) throw new Error("no account returned");

    msg.textContent = "Fetching sign-in challenge…";
    const ch = await (await fetch("/api/auth/nonce")).json();

    msg.textContent = "Approve the signature in your wallet…";
    const out = await wallet.features["solana:signMessage"].signMessage({
      account, message: enc.encode(ch.message),
    });
    const signature = b64(out[0].signature);

    msg.textContent = "Verifying…";
    const vr = await (await fetch("/api/auth/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pubkey: account.address, signature, nonce: ch.nonce }),
    })).json();

    if (vr.error) { msg.textContent = vr.error; return; }
    close();
    const status = await (await fetch("/api/auth/status")).json();
    window.SM && window.SM.onAuth && window.SM.onAuth(status);
  } catch (e) {
    msg.textContent = "Sign-in cancelled or failed: " + (e.message || e);
  }
}

async function open() {
  $("wmodal").classList.remove("hidden");
  $("wmodalMsg").textContent = "";
  $("walletList").innerHTML = "Detecting wallets…";
  try { await ensureLib(); refresh(); }
  catch (e) {
    $("walletList").innerHTML =
      `<div class="wallet-empty">Couldn't load the wallet connector (network issue?). Reload and try again.</div>`;
  }
}
function close() { $("wmodal").classList.add("hidden"); }

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  const status = await (await fetch("/api/auth/status")).json();
  window.SM && window.SM.onAuth && window.SM.onAuth(status);
}

$("connect").onclick = () =>
  (window.SM && window.SM.auth && window.SM.auth.authenticated) ? logout() : open();
$("wmodalClose").onclick = close;
$("wmodal").onclick = (e) => { if (e.target.id === "wmodal") close(); };

// let app.js open the modal from a gated row click
window.SMwallet = { open, logout };

// restore an existing session on load (no library needed)
(async () => {
  try {
    const status = await (await fetch("/api/auth/status")).json();
    window.SM && window.SM.onAuth && window.SM.onAuth(status);
  } catch (e) {}
})();
