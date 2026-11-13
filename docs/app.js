import { createClient } from "https://esm.sh/genlayer-js";
import { studionet } from "https://esm.sh/genlayer-js/chains";

const CONTRACT = "0x9405f294C1c12e073b875137D5adb31C4a817d46";
const EXPLORER = "https://explorer-studio.genlayer.com";
const CHAIN_HEX = "0xf22f"; // 61999
const RPC = "https://studio.genlayer.com/api";

const DAY = 86400000;
const today = new Date();
const tenDaysAgo = new Date(today.getTime() - 10 * DAY);

const iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const EXAMPLE = {
  statement: "Adoption reached 40% of surveyed engineering teams in 2025",
  sourceUrl: "https://raw.githubusercontent.com/Juniorenen/claim-freshness/main/README.md",
  publishedOn: iso(tenDaysAgo),
  windowDays: "90",
};

const FIELDS = {
  statement: "statement",
  sourceUrl: "sourceUrl",
  publishedOn: "publishedOn",
  windowDays: "windowDays",
};

const el = (id) => document.getElementById(id);
const $connect = el("connectBtn");
const $run = el("runBtn");
const $reset = el("resetBtn");
const $addNet = el("addNetBtn");
const $menu = el("accountMenu");
const $menuAddr = el("menuAddr");
const $steps = el("steps");
const $result = el("resultPanel");
const $verdict = el("verdict");
const $verdictNote = el("verdictNote");
const $stats = el("stats");
const $raw = el("rawJson");
const $explorer = el("explorerLink");

let client = null;
let account = null;

const CHECK_SVG =
  '<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">' +
  '<path d="M2.5 8.5 L6.2 12.2 L13.5 3.8" fill="none" stroke="currentColor" ' +
  'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const STEPS = [
  { key: "register", title: "Register the claim", note: "writes the statement, its source and its window on chain" },
  { key: "check", title: "Check freshness", note: "compares the transaction date against the window and the source" },
];

const VERDICT_NOTES = {
  FRESH: "Still inside the declared window. Decided without touching the network.",
  AGING: "Past the window but inside the grace band. Still citable, with a caveat.",
  SUPERSEDED: "Past the band, and the source itself carries a newer date. Re-read the claim against the new version.",
  EXPIRED: "Past the band, and the source has not moved. The claim outlived its evidence.",
  UNVERIFIABLE: "Past the band, and the source could not be read. Unknown, not expired.",
};

const VERDICT_CLASS = {
  FRESH: "ok",
  AGING: "warn",
  SUPERSEDED: "warn",
  EXPIRED: "bad",
  UNVERIFIABLE: "warn",
};

function fillExample() {
  for (const [key, id] of Object.entries(FIELDS)) {
    const node = el(id);
    if (node) node.value = EXAMPLE[key];
  }
}

function short(hash) {
  return hash ? `${hash.slice(0, 10)}…${hash.slice(-6)}` : "";
}

function setConnected(addr) {
  $connect.textContent = `${addr.slice(0, 6)}…${addr.slice(-4)}`;
  $connect.title = "Click to disconnect";
  $connect.classList.add("connected");
  $connect.disabled = false;
  $addNet.classList.add("hidden");
  $menuAddr.textContent = addr;
}

function setDisconnected() {
  $connect.textContent = "Connect wallet";
  $connect.title = "";
  $connect.classList.remove("connected");
  $connect.disabled = false;
  $addNet.classList.remove("hidden");
  $menu.classList.add("hidden");
}

function toggleMenu(force) {
  const show = force === undefined ? $menu.classList.contains("hidden") : force;
  $menu.classList.toggle("hidden", !show);
}

function renderSteps() {
  $steps.innerHTML = "";
  for (const s of STEPS) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="mark"></span>
      <div>
        <div class="title">${s.title}</div>
        <div class="sub">${s.note}</div>
      </div>
      <span class="link"></span>`;
    $steps.appendChild(li);
  }
}

function stepNode(index) {
  return $steps.children[index];
}

function stepSet(index, state, message, link) {
  const li = stepNode(index);
  if (!li) return;
  li.className = state;
  const mark = li.querySelector(".mark");
  if (state === "done") mark.innerHTML = CHECK_SVG;
  else mark.textContent = state === "fail" ? "!" : "";
  if (message) li.querySelector(".sub").textContent = message;
  if (link !== undefined) li.querySelector(".link").innerHTML = link;
}

let toastTimer = null;

function toastNode() {
  let node = el("toast");
  if (!node) {
    node = document.createElement("div");
    node.id = "toast";
    node.className = "toast";
    document.body.appendChild(node);
  }
  return node;
}

function log(msg, bad) {
  const node = toastNode();
  node.textContent = msg;
  node.className = `toast show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.className = `toast${bad ? " bad" : ""}`;
  }, bad ? 6000 : 3000);
  console.log("[freshness]", msg);
}

function clearLog() {
  const node = el("toast");
  if (node) node.className = "toast" + (node.classList.contains("bad") ? " bad" : "");
  clearTimeout(toastTimer);
}

const CHAIN_PARAMS = {
  chainId: CHAIN_HEX,
  chainName: "GenLayer Studio",
  rpcUrls: [RPC],
  nativeCurrency: { name: "GEN", symbol: "GEN", decimals: 18 },
  blockExplorerUrls: [EXPLORER],
};

// ---------------------------------------------------------------- providers
// Several wallets can be installed at once and every one of them injects
// window.ethereum. OKX even reports isMetaMask === true, so feature-sniffing
// picks the wrong one. EIP-6963 lets each wallet announce itself with an
// identity, and the user picks.
let eth = null;
let walletName = "";
const announced = new Map();

window.addEventListener("eip6963:announceProvider", (event) => {
  const detail = event.detail;
  if (detail && detail.info && detail.provider) {
    announced.set(detail.info.uuid, detail);
  }
});
window.dispatchEvent(new Event("eip6963:requestProvider"));

function walletList() {
  return [...announced.values()].sort((a, b) =>
    String(a.info.name).localeCompare(String(b.info.name))
  );
}

function chooseWallet(items) {
  return new Promise((resolve, reject) => {
    const box = el("walletChooser");
    const list = el("chooserList");
    list.innerHTML = "";
    for (const item of items) {
      const btn = document.createElement("button");
      btn.className = "chooserItem";
      btn.innerHTML = `<img src="${item.info.icon}" alt="" /><span>${item.info.name}</span>`;
      btn.addEventListener("click", () => {
        box.classList.add("hidden");
        resolve(item.provider);
      });
      list.appendChild(btn);
    }
    el("chooserCancel").onclick = () => {
      box.classList.add("hidden");
      reject(new Error("wallet selection cancelled"));
    };
    box.classList.remove("hidden");
  });
}

// MetaMask only. EIP-6963 rdns is the one marker other wallets do not spoof —
// OKX reports isMetaMask: true, which is how it used to hijack this flow.
function findMetaMask() {
  const items = [...announced.values()];
  const byRdns = items.find(
    (i) => String(i.info && i.info.rdns ? i.info.rdns : "").toLowerCase() === "io.metamask"
  );
  if (byRdns) return byRdns.provider;
  const legacy = window.ethereum;
  if (
    legacy &&
    legacy.isMetaMask &&
    !legacy.isOkxWallet &&
    !legacy.isOKExWallet &&
    !legacy.isTokenPocket &&
    !legacy.isRabby &&
    !legacy.isTrust
  ) {
    return legacy;
  }
  return null;
}

async function pickMetaMask() {
  await sleep(300);
  return findMetaMask();
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Some wallets show the connection prompt, the user approves, and the
// eth_requestAccounts promise still never settles. Polling eth_accounts is the
// only reliable way to notice that access was granted.
async function requestAccounts() {
  let fromEvent = null;
  const onAccounts = (a) => {
    if (a && a.length) {
      fromEvent = a;
      log("accountsChanged event received");
    }
  };
  eth.on?.("accountsChanged", onAccounts);

  log("prompt sent · confirm in MetaMask");
  const prompt = eth.request({ method: "eth_requestAccounts" });
  let promptError = null;
  prompt.catch((e) => {
    promptError = e;
    log(`prompt rejected: ${friendly(e)}`, true);
  });

  const deadline = Date.now() + 90000;
  try {
    while (Date.now() < deadline) {
      if (fromEvent) return fromEvent;

      const raced = await Promise.race([
        prompt.then((a) => ({ ok: a })).catch((e) => ({ err: e })),
        sleep(600).then(() => null),
      ]);
      if (raced && raced.ok && raced.ok.length) return raced.ok;
      if (raced && raced.err) throw raced.err;
      if (promptError) throw promptError;
      if (fromEvent) return fromEvent;

      // Never await the probe directly: while a prompt is open the wallet
      // queues further requests, so an un-timed await here deadlocks the loop.
      const probe = await Promise.race([
        eth.request({ method: "eth_accounts" }).catch(() => null),
        sleep(400).then(() => null),
      ]);
      if (probe && probe.length) {
        log("eth_accounts reports access granted");
        return probe;
      }
      if (fromEvent) return fromEvent;
    }
    throw new Error("the wallet did not return an account");
  } finally {
    eth.removeListener?.("accountsChanged", onAccounts);
  }
}

async function chainIdNow() {
  // timed, because an open wallet prompt queues replies
  const value = await Promise.race([
    eth.request({ method: "eth_chainId" }).catch(() => null),
    sleep(2000).then(() => null),
  ]);
  return String(value || "").toLowerCase();
}

// Same failure mode as eth_requestAccounts: the prompt may never settle even
// after the user approved. Confirm by reading eth_chainId instead of waiting.
async function switchChain() {
  const prompt = eth.request({
    method: "wallet_switchEthereumChain",
    params: [{ chainId: CHAIN_HEX }],
  });
  prompt.catch(() => {});

  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const raced = await Promise.race([
      prompt.then(() => ({ ok: true })).catch((e) => ({ err: e })),
      sleep(600).then(() => null),
    ]);
    if (raced && raced.ok) return;
    if (raced && raced.err) throw raced.err;

    const current = await chainIdNow();
    if (current === CHAIN_HEX) return;
  }
  throw new Error("the wallet did not switch to chain 61999");
}

async function addChain() {
  await eth.request({
    method: "wallet_addEthereumChain",
    params: [CHAIN_PARAMS],
  });
}

function chainMissing(err) {
  return !!(
    err &&
    (err.code === 4902 ||
      /unrecognized|not added|unknown chain|not found|does not exist/i.test(err.message || ""))
  );
}

// "Add network" button: add only when the wallet does not have the chain.
async function addNetwork() {
  clearLog();
  if (!eth) {
    eth = await pickMetaMask();
    if (!eth) {
      showInstallHint(true);
      log("MetaMask not found in this browser", true);
      return;
    }
    walletName = "MetaMask";
    showInstallHint(false);
    watchProvider(eth);
  }
  $addNet.disabled = true;
  try {
    if ((await chainIdNow()) === CHAIN_HEX) {
      log("This wallet already has GenLayer Studio (61999). Press Connect wallet.");
      return;
    }
    try {
      await switchChain();
      log("This wallet already had GenLayer Studio — switched to it.");
    } catch (err) {
      if (!chainMissing(err)) throw err;
      await addChain();
      log("GenLayer Studio added to the wallet. Press Connect wallet.");
    }
  } catch (err) {
    log(`add network failed: ${friendly(err)}`, true);
  } finally {
    $addNet.disabled = false;
  }
}

// Make sure the wallet can talk to chain 61999. Prompts add + switch if needed.
async function ensureChain() {
  if ((await chainIdNow()) === CHAIN_HEX) return "already on 61999";
  try {
    await switchChain();
    return "switched to 61999";
  } catch (err) {
    if (!chainMissing(err)) throw err;
    await addChain();
    await switchChain();
    return "added and switched to 61999";
  }
}

async function connect() {
  clearLog();
  log("click · looking for MetaMask");
  if (!eth) {
    eth = await pickMetaMask();
    if (!eth) {
      showInstallHint(true);
      log("MetaMask not found in this browser", true);
      throw new Error("MetaMask is not installed");
    }
    walletName = "MetaMask";
    showInstallHint(false);
    watchProvider(eth);
  }
  log("MetaMask found · requesting accounts");

  $connect.disabled = true;
  $connect.textContent = "Connecting…";

  // 1. account access first. Wallets commonly refuse wallet_switchEthereumChain
  //    until an account is authorised, so asking for the chain first deadlocks
  //    the whole flow. The interface flips to the address as soon as this
  //    resolves, before any chain work happens.
  log("asking the wallet for account access…");
  const accounts = await requestAccounts();
  if (!accounts || !accounts.length) throw new Error("wallet returned no account");
  account = accounts[0];
  log(`account granted · ${account.slice(0, 6)}…${account.slice(-4)}`);
  {
    const tag = document.querySelector(".tag");
    if (tag) tag.textContent = "chain 61999 · MetaMask";
  }
  setConnected(account);
  $run.disabled = false;
  $run.classList.add("pulse");

  // 2. chain: add it if the wallet is missing it, then select it.
  try {
    const net = await ensureChain();
    log(`connected · ${net}`);
  } catch (err) {
    log(`connected, but the chain is not ready: ${friendly(err)}`, true);
  }

  // 3. client
  try {
    client = createClient({ chain: studionet, account, provider: eth });
  } catch (err) {
    log(`client init failed: ${friendly(err)}`, true);
  }

  return account;
}

function showInstallHint(on) {
  const node = el("installHint");
  if (!node) return;
  node.classList.toggle("hidden", !on);
}

function friendly(err) {
  const m = (err && (err.shortMessage || err.message)) || String(err);
  if (/user rejected|denied/i.test(m)) return "rejected in wallet";
  return m.slice(0, 160);
}

// GenLayer moves a transaction ACCEPTED -> FINALIZED only after the appeal
// window, which is far longer than a user will wait. ACCEPTED already means the
// execution result is decided, so stop there and read the state.
async function waitSettled(hash, timeoutMs) {
  const terminal = ["ACCEPTED", "FINALIZED", "UNDETERMINED", "CANCELED"];
  const deadline = Date.now() + (timeoutMs || 300000);
  let last = "";
  while (Date.now() < deadline) {
    let tx = null;
    try {
      tx = await client.getTransaction({ hash });
    } catch (err) {
      tx = null;
    }
    const st = String((tx && tx.statusName) || "").toUpperCase();
    if (st) last = st;
    if (terminal.includes(st)) return tx;
    await sleep(2500);
  }
  throw new Error(`transaction did not settle (last status: ${last || "unknown"})`);
}

async function send(index, functionName, args) {
  stepSet(index, "running", "waiting for the wallet…", "");
  let hash;
  try {
    hash = await client.writeContract({ address: CONTRACT, functionName, args, value: 0n });
  } catch (err) {
    stepSet(index, "fail", friendly(err), "");
    throw err;
  }
  const link = `<a href="${EXPLORER}/tx/${hash}" target="_blank" rel="noreferrer">tx ${short(hash)} ↗</a>`;
  stepSet(index, "running", "submitted · waiting for the decision…", link);
  const tx = await waitSettled(hash);
  const status = String(tx.statusName || "").toUpperCase();
  const exec = String(tx.txExecutionResultName || "").toUpperCase();
  const okStatus = status === "ACCEPTED" || status === "FINALIZED";
  const okExec = !exec || exec === "FINISHED_WITH_RETURN";
  if (!okStatus || !okExec) {
    const detail = `${tx.statusName || "?"}${tx.txExecutionResultName ? " / " + tx.txExecutionResultName : ""}`;
    stepSet(index, "fail", `not successful · ${detail}`, link);
    throw new Error(`step ${functionName} failed: ${detail}`);
  }
  stepSet(index, "done", "accepted", link);
  return { hash, tx };
}

function packedDate(isoDate) {
  // "2026-11-14" -> 20261114
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((isoDate || "").trim());
  if (!m) throw new Error("published date must be YYYY-MM-DD");
  return Number(m[1] + m[2] + m[3]);
}

function readClaimInput() {
  const v = (id) => (el(id).value || "").trim();
  const statement = v("statement");
  const sourceUrl = v("sourceUrl");
  const publishedOn = packedDate(v("publishedOn"));
  const windowDays = Number(v("windowDays"));

  if (!statement) throw new Error("statement is required");
  if (!/^https?:\/\//.test(sourceUrl)) throw new Error("source URL must be http(s)");
  if (!Number.isFinite(windowDays) || windowDays < 1) throw new Error("window must be at least 1 day");

  return { statement, sourceUrl, publishedOn, windowDays };
}

async function run() {
  $run.disabled = true;
  $run.classList.remove("pulse");
  $result.classList.add("hidden");
  renderSteps();
  try {
    if (!client) {
      if (!account) {
        await connect();
      } else {
        client = createClient({ chain: studionet, account, provider: eth });
      }
    }
    if (!client) throw new Error("wallet not connected");
    const c = readClaimInput();

    const next = Number(
      await client.readContract({ address: CONTRACT, functionName: "count_records", args: [] })
    ) + 1;

    await send(0, "register", [
      next, c.statement, c.sourceUrl, c.publishedOn, c.windowDays,
    ]);
    await send(1, "check", [next]);

    const raw = await client.readContract({
      address: CONTRACT, functionName: "get_record", args: [next],
    });
    showResult(JSON.parse(raw));
  } catch (err) {
    console.error(err);
    $verdict.textContent = "Stopped";
    $verdict.className = "verdict bad";
    $verdictNote.textContent = friendly(err);
    $stats.innerHTML = "";
    $raw.textContent = "";
    $result.classList.remove("hidden");
  } finally {
    $run.disabled = !client;
  }
}

function showResult(record) {
  const verdict = record.verdict || "—";
  $verdict.textContent = verdict;
  $verdict.className = `verdict ${VERDICT_CLASS[verdict] || ""}`;
  $verdictNote.textContent = VERDICT_NOTES[verdict] || `state: ${record.state}`;

  const srcDate = record.source_date ? String(record.source_date) : "not read";
  const grace = record.window_days + Math.floor((record.window_days * 5) / 4);
  const cells = [
    ["age", `${record.age_days} days`],
    ["window", `${record.window_days} days`],
    ["grace band", `${grace} days`],
    ["published", String(record.published_on)],
    ["source date", srcDate],
    ["checks run", String((record.history || []).length)],
    ["state", record.state || "—"],
  ];
  $stats.innerHTML = cells
    .map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join("");

  $raw.textContent = JSON.stringify(record, null, 2);
  $explorer.href = `${EXPLORER}/address/${CONTRACT}`;
  $result.classList.remove("hidden");
  $result.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$connect.addEventListener("click", () => {
  if (account) {
    toggleMenu();
    return;
  }
  connect().catch((err) => {
    setDisconnected();
    log(`connect failed: ${friendly(err)}`, true);
  });
});

el("addNetBtn")?.addEventListener("click", addNetwork);

el("disconnectBtn")?.addEventListener("click", () => {
  client = null;
  account = null;
  setDisconnected();
  $run.disabled = true;
  $run.classList.remove("pulse");
  $result.classList.add("hidden");
  log("Wallet disconnected.");
  eth
    ?.request?.({ method: "wallet_revokePermissions", params: [{ eth_accounts: {} }] })
    .catch(() => {});
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".wallet")) toggleMenu(false);
});

$run.addEventListener("click", run);
$reset.addEventListener("click", () => {
  fillExample();
  $result.classList.add("hidden");
  renderSteps();
});

function watchProvider(p) {
  // Never reload here. Authorising the connection fires accountsChanged, and a
  // reload would wipe the state that was just established — which is exactly
  // the "connected but still shows Connect wallet" symptom.
  p?.on?.("accountsChanged", (list) => {
    if (list && list.length) {
      account = list[0];
      eth = p;
      client = createClient({ chain: studionet, account, provider: eth });
      setConnected(account);
      $run.disabled = false;
      log(`wallet account · ${account.slice(0, 6)}…${account.slice(-4)}`);
    } else {
      account = null;
      client = null;
      setDisconnected();
      $run.disabled = true;
      $run.classList.remove("pulse");
      log("wallet disconnected from its side");
    }
  });

  p?.on?.("chainChanged", () => {
    if (account) client = createClient({ chain: studionet, account, provider: eth });
  });
}

el("contractAddr").textContent = `${CONTRACT.slice(0, 6)}…${CONTRACT.slice(-4)}`;
{
  const tag = document.querySelector(".tag");
  if (tag) tag.textContent = "chain 61999 · MetaMask";
}
$explorer.href = `${EXPLORER}/address/${CONTRACT}`;
fillExample();
renderSteps();
