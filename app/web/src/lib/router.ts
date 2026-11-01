// Tiny history router: path + query are the state; every view is URL-addressable.
// URLs are /<person>/<path>: `Loc.path` is the person-less path (what ROUTES match) and `Loc.person` the prefix.
// App code keeps writing person-less paths ("/activities/123"); navigate(), <a href> (via a preact vnode hook in
// main.tsx) and API calls (lib/api.ts) add the current person.
import { useEffect, useState } from "preact/hooks";

type Loc = { path: string; query: URLSearchParams; person: string | null };
let PEOPLE: string[] = [];
let DEFAULT = "";
export function setPeople(people: string[], def: string) { PEOPLE = people; DEFAULT = def; }
export const people = () => PEOPLE;
export const defaultPerson = () => DEFAULT;

/** split "/patrik/activities/1" into ["patrik", "/activities/1"]; person is null if the first segment isn't one */
export function splitPerson(pathname: string): [string | null, string] {
  const m = /^\/([^/]+)(\/.*)?$/.exec(pathname);
  if (m && PEOPLE.includes(decodeURIComponent(m[1]))) return [decodeURIComponent(m[1]), m[2] || "/"];
  return [null, pathname];
}
/** person in the current URL (or the default while redirecting an unprefixed URL) */
export const currentPerson = () => splitPerson(location.pathname)[0] ?? DEFAULT;

/** "/activities?x" -> "/<person>/activities?x" (unchanged if already prefixed, not app-absolute, or /api) */
export function withPerson(url: string, person = currentPerson()): string {
  if (!url.startsWith("/") || url.startsWith("//") || url === "/api" || url.startsWith("/api/") || url.startsWith("/assets/")) return url;
  const i = url.search(/[?#]/), path = i < 0 ? url : url.slice(0, i), rest = i < 0 ? "" : url.slice(i);
  if (splitPerson(path)[0]) return url;
  return "/" + encodeURIComponent(person) + (path === "/" ? "" : path) + rest;
}

const read = (): Loc => {
  const [person, path] = splitPerson(location.pathname);
  return { path: path.replace(/\/+$/, "") || "/", query: new URLSearchParams(location.search), person };
};
const EVT = "app:navigate";

export function navigate(url: string, replace = false) {
  url = withPerson(url);
  if (url === location.pathname + location.search) return;
  history[replace ? "replaceState" : "pushState"](null, "", url);
  window.dispatchEvent(new Event(EVT));
}

/** Merge `patch` into the current query (null/"" removes a key). Replaces history by default. */
export function setQuery(patch: Record<string, string | number | null | undefined>, replace = true) {
  const q = new URLSearchParams(location.search);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === "") q.delete(k);
    else q.set(k, String(v));
  }
  const s = q.toString();
  navigate(location.pathname + (s ? "?" + s : ""), replace);
}

export function useLocation(): Loc {
  const [loc, set] = useState(read);
  useEffect(() => {
    const on = () => set(read());
    window.addEventListener("popstate", on);
    window.addEventListener(EVT, on);
    set((o) => { const n = read(); return o.path === n.path && o.person === n.person && o.query.toString() === n.query.toString() ? o : n; });   // a child may have navigated before this effect subscribed (child effects run first, e.g. a redirect)
    return () => { window.removeEventListener("popstate", on); window.removeEventListener(EVT, on); };
  }, []);
  return loc;
}

/** Match "/activities/:id" against a path; returns params or null. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const a = pattern.split("/"), b = path.split("/");
  if (a.length !== b.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith(":")) out[a[i].slice(1)] = decodeURIComponent(b[i]);
    else if (a[i] !== b[i]) return null;
  }
  return out;
}

/** Plain <a href="/..."> links navigate client-side (except /api, new-tab clicks, external). */
export function interceptLinks() {
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as Element).closest?.("a");
    if (!a || a.target || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.pathname.startsWith("/api")) return;
    e.preventDefault();
    navigate(url.pathname + url.search);
  });
}
