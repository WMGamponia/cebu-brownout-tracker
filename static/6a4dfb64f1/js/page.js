// What both pages share in snapshot mode (Phase 6 spec 4.3): picking the data source from
// <html data-mode>, and the "Last updated …" banner (old data only) and credits. Text via textContent
// only; links via setAttribute, and only https:// or relative ones.
import { makeSource, shouldReload, snapshotCopy } from "./source.js";

export const isSnapshotPage = () => document.documentElement.dataset.mode === "snapshot";

async function fetchSnap() {
  const r = await fetch("snapshot.json", { cache: "no-store" });
  if (!r.ok) throw new Error(`snapshot.json: HTTP ${r.status}`);
  return r.json();
}

/** The page's data source; in snapshot mode after loading snapshot.json (throws on failure). */
export async function pageSource() {
  if (!isSnapshotPage()) return makeSource("live", null);
  return makeSource("snapshot", await fetchSnap());
}

const RECHECK_MS = 10 * 60 * 1000;

/** Keep an open snapshot tab honest: when it comes back into view, and every RECHECK_MS while
 *  visible, reload if newer data is published or the data it shows has gone old. */
export function watchSnapshot(src) {
  let busy = false;
  async function recheck() {
    if (busy || document.visibilityState !== "visible") return;
    busy = true;
    let latest = null;
    try {
      latest = await fetchSnap();
    } catch {
      // offline or a failed fetch: decide on the clock alone
    }
    busy = false;
    if (shouldReload(src.snap, src.fresh, src.now(), latest)) location.reload();
  }
  document.addEventListener("visibilitychange", recheck);
  window.addEventListener("pageshow", (e) => e.persisted && recheck()); // restored from bfcache
  setInterval(recheck, RECHECK_MS);
}

function link(text, href, external) {
  const a = document.createElement("a");
  a.textContent = text;
  const ok = typeof href === "string" && (href.startsWith("https://") || !href.includes(":"));
  if (ok) a.setAttribute("href", href);
  if (external) {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  }
  return a;
}

/** Show the footer credits, and the banner when the data is old (live-site spec 5.5). */
export function showSnapshotChrome(snap, fresh) {
  const copy = snapshotCopy(snap, fresh);
  const veco = () => link("VECO's advisories", snap.veco_url, true);
  const banner = document.getElementById("snapshot-banner");
  if (copy.banner) {
    banner.replaceChildren(copy.banner, veco(), ".");
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
  const credits = document.getElementById("snapshot-credits");
  credits.replaceChildren(
    copy.credits,
    ...(fresh ? [veco(), "; "] : []),
    "code: ",
    link(snap.code_url, snap.code_url, true),
    "; licences: ",
    link("LICENSES.md", "LICENSES.md", false),
  );
  credits.hidden = false;
}
