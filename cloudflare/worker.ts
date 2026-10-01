/**
 * calendrier-tempo.fr sur Cloudflare : Worker frontal + conteneur FastAPI.
 *
 * - L'app Python tourne telle quelle dans un conteneur (même Dockerfile).
 * - Une seule instance (max_instances = 1) : APScheduler tourne dans le
 *   conteneur, deux instances enverraient les alertes en double.
 * - Le cron toutes les 5 min garde le conteneur éveillé : le scheduler
 *   (11h30, 18h, polling EDF...) ne doit jamais s'endormir.
 * - Le Worker transmet l'IP réelle du visiteur (X-Forwarded-For) : sans ça,
 *   le rate limiting de /api/subscribe verrait une seule IP pour tout le monde.
 */
import { Container, getContainer } from "@cloudflare/containers";
import type { DurableObject } from "cloudflare:workers";

export interface Env {
  TEMPO_APP: DurableObjectNamespace<TempoApp>;
  [key: string]: unknown;
}

// Variables lues par l'app Python (config.py, app.py, agents).
// Chacune est transmise au conteneur si elle est définie comme secret/var du Worker.
const APP_ENV_KEYS = [
  "ADMIN_PASSWORD", "SESSION_SECRET", "PHONE_ENCRYPTION_KEY",
  "DATABASE_URL", "BASE_URL", "LOG_LEVEL",
  "TEMPO_REMAINING_ROUGE", "TEMPO_REMAINING_BLANC",
  "METEOFRANCE_API_KEY", "METEOFRANCE_APPLICATION_ID", "METEOFRANCE_AROME_KEY",
  "METEOFRANCE_ARPEGE_KEY", "METEOFRANCE_VIGILANCE_KEY",
  "RTE_CLIENT_ID", "RTE_CLIENT_SECRET", "RTE_CONSO_CLIENT_ID", "RTE_CONSO_CLIENT_SECRET",
  "RTE_GENERATION_CLIENT_ID", "RTE_GENERATION_CLIENT_SECRET", "RTE_NUCLEAR_CAPACITY_MW",
  "WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN",
  "WHATSAPP_APP_SECRET", "WHATSAPP_API_VERSION", "WHATSAPP_TEMPLATE_LANG",
  "WHATSAPP_TEMPLATE_ALERT_BLANC", "WHATSAPP_TEMPLATE_ALERT_ROUGE",
  "WHATSAPP_TEMPLATE_CHANGE", "WHATSAPP_TEMPLATE_CONFIRMATION",
  "WHATSAPP_TEMPLATE_MANAGE_LINK", "WHATSAPP_TEMPLATE_RECAP", "WHATSAPP_TEMPLATE_WELCOME",
  "WHATSAPP_TEST_NUMBERS",
  "ANTHROPIC_API_KEY", "SEO_AGENT_MODEL", "SEO_AGENT_MAX_TURNS", "BACKLINKS_AGENT_MAX_TURNS",
  "INDEXNOW_KEY", "GOOGLE_SITE_VERIFICATION", "BING_SITE_VERIFICATION",
] as const;

export class TempoApp extends Container<Env> {
  defaultPort = 5000;
  // Renouvelé à chaque requête ; le cron (5 min) le maintient éveillé en continu.
  sleepAfter = "1h";

  constructor(ctx: DurableObject["ctx"], env: Env) {
    super(ctx, env);
    const vars: Record<string, string> = {
      PORT: "5000",
      // Le conteneur n'est joignable que via ce Worker : on fait confiance
      // aux en-têtes X-Forwarded-* qu'il pose (uvicorn --proxy-headers).
      FORWARDED_ALLOW_IPS: "*",
    };
    for (const key of APP_ENV_KEYS) {
      const value = readSecret(env, key);
      if (value !== "") vars[key] = value;
    }
    this.envVars = vars;

    // Les variables ne sont lues qu'au démarrage du conteneur. Si les secrets ont changé
    // (ex. WHATSAPP_TOKEN ajouté à la bascule), on arrête le conteneur en cours : la requête
    // suivante le relance avec les nouvelles valeurs. Seule l'empreinte est stockée.
    ctx.blockConcurrencyWhile(async () => {
      const fingerprint = await sha256(JSON.stringify(Object.entries(vars).sort()));
      if ((await ctx.storage.get<string>("envFingerprint")) === fingerprint) return;
      if (ctx.container?.running) {
        console.log("[config] secrets modifiés : redémarrage du conteneur");
        await this.stop();
      }
      await ctx.storage.put("envFingerprint", fingerprint);
    });
  }
}

// Cloudflare limite un secret à 5,1 ko : push_secrets.py découpe les valeurs plus longues
// (ex. METEOFRANCE_AROME_KEY, 6,4 ko) en CLÉ__PART1, CLÉ__PART2... recollées ici.
function readSecret(env: Env, key: string): string {
  const whole = env[key];
  if (typeof whole === "string" && whole !== "") return whole;
  let joined = "";
  for (let i = 1; typeof env[`${key}__PART${i}`] === "string"; i++) {
    joined += env[`${key}__PART${i}`] as string;
  }
  return joined;
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------------------
// Cache au bord (Cache API, caches.default) : respecte les Cache-Control de l'app.
// - Seules les réponses 200 « public » avec max-age > 0 sont stockées (jamais private,
//   no-store, no-cache, Set-Cookie, ni les 503 de démarrage de main.py).
// - Durée de stockage = max-age + stale-while-revalidate : entre les deux, la copie
//   en cache est servie (STALE) et rafraîchie en arrière-plan (ctx.waitUntil).
// - Clé = URL complète (query comprise), hôte normalisé + version des données
//   (/api/edge-version, relue toutes les 15 s au plus) : un changement de couleur,
//   un calcul ou une évaluation rend toutes les copies obsolètes en <= 15 s.
// - En-tête de diagnostic X-Edge-Cache.
// ---------------------------------------------------------------------------

const EDGE_STATUS = "X-Edge-Cache"; // HIT | STALE | MISS | BYPASS
// En-têtes internes, stockés avec la copie et retirés avant l'envoi au client.
const STORED_AT = "X-Edge-Stored-At";
const ORIGIN_CC = "X-Edge-Origin-Cache-Control";

// Jamais en cache : back-office, gestion d'abonnement, webhooks, inscriptions, santé.
const BYPASS_PREFIXES = ["/admin", "/manage/", "/api/webhook/"] as const;
const BYPASS_EXACT = new Set([
  "/manage", "/api/webhook", "/api/subscribe", "/api/resend-manage-link", "/health", "/keepalive",
  "/api/edge-version",
]);
// Cookies sans effet sur le rendu (Cloudflare, mesure d'audience) : ils n'empêchent pas le cache.
const NEUTRAL_COOKIE = /^(__cf|_cf|cf_|__cflb|_ga|_gid|umami)/i;

function isCacheableRequest(request: Request, url: URL): boolean {
  const method = request.method.toUpperCase();
  if (method !== "GET" && method !== "HEAD") return false;
  if (request.headers.has("Authorization") || request.headers.has("Range")) return false;
  const cookie = request.headers.get("Cookie");
  if (cookie) {
    const names = cookie.split(";").map((c) => c.split("=")[0].trim()).filter((n) => n !== "");
    if (names.some((n) => !NEUTRAL_COOKIE.test(n))) return false; // cookie de session possible
  }
  if (url.searchParams.has(VERSION_PARAM)) return false; // paramètre interne, jamais fourni par le client
  const path = url.pathname;
  if (BYPASS_EXACT.has(path)) return false;
  if (BYPASS_PREFIXES.some((p) => path.startsWith(p))) return false;
  return true;
}

// ---------------------------------------------------------------------------
// Version des données (app.py : invalidate_predictions_cache -> /api/edge-version).
// Par isolat : dernière version connue + date de lecture. Au-delà de 15 s, relue auprès du
// conteneur (2 s max, une seule relecture en vol). Échec (503 de démarrage, délai) : on garde
// la dernière version connue (nouvel essai 5 s plus tard) ; aucune connue : pas de cache.
// ---------------------------------------------------------------------------

const EDGE_VERSION_PATH = "/api/edge-version";
const VERSION_PARAM = "__edge_v"; // ajouté à la clé de cache uniquement
const VERSION_MAX_AGE_MS = 15_000;
const VERSION_TIMEOUT_MS = 2_000;
const VERSION_RETRY_MS = 5_000;
const VERSION_FORMAT = /^[0-9A-Za-z._:-]{1,80}$/;

let knownVersion: { value: string; readAt: number } | null = null;
let lastVersionFailure = 0;
let versionInFlight: { promise: Promise<string | null>; startedAt: number } | null = null;

function timeout(ms: number): Promise<null> {
  return new Promise((resolve) => setTimeout(() => resolve(null), ms));
}

async function fetchDataVersion(env: Env): Promise<string | null> {
  try {
    const request = new Request(`${CANONICAL_ORIGIN}${EDGE_VERSION_PATH}`, { method: "GET" });
    const response = await Promise.race([getContainer(env.TEMPO_APP).fetch(request), timeout(VERSION_TIMEOUT_MS)]);
    if (!response) return null; // délai dépassé
    if (response.status !== 200) {
      discard(response); // 503 pendant le démarrage de main.py
      return null;
    }
    const text = (await Promise.race([response.text(), timeout(VERSION_TIMEOUT_MS)]))?.trim() ?? "";
    return VERSION_FORMAT.test(text) ? text : null;
  } catch (e) {
    console.error(`[edge-cache] version : ${e}`);
    return null;
  }
}

// Version courante pour la clé de cache, ou null (=> BYPASS).
async function currentDataVersion(env: Env): Promise<string | null> {
  const now = Date.now();
  if (knownVersion && now - knownVersion.readAt < VERSION_MAX_AGE_MS) return knownVersion.value;
  if (knownVersion && now - lastVersionFailure < VERSION_RETRY_MS) return knownVersion.value;
  // Une relecture bloquée (requête initiatrice annulée) ne doit pas tout figer.
  if (!versionInFlight || now - versionInFlight.startedAt > VERSION_TIMEOUT_MS * 2) {
    const flight = {
      startedAt: now,
      promise: fetchDataVersion(env).then((value) => {
        if (value !== null) knownVersion = { value, readAt: Date.now() };
        else lastVersionFailure = Date.now();
        return value;
      }).finally(() => {
        if (versionInFlight === flight) versionInFlight = null;
      }),
    };
    versionInFlight = flight;
  }
  // Délai propre à chaque requête : une promesse née dans une autre requête peut ne jamais aboutir.
  const value = await Promise.race([versionInFlight.promise, timeout(VERSION_TIMEOUT_MS + 500)]);
  return value ?? knownVersion?.value ?? null;
}

function cacheKey(url: URL, version: string): Request {
  const key = new URL(url.toString());
  key.protocol = "https:";
  key.hostname = key.hostname.toLowerCase().replace(/\.$/, "");
  key.port = "";
  key.hash = "";
  key.searchParams.set(VERSION_PARAM, version);
  return new Request(key.toString(), { method: "GET" });
}

function parseCacheControl(value: string | null): Map<string, string> {
  const directives = new Map<string, string>();
  for (const part of (value ?? "").split(",")) {
    const [name, ...rest] = part.trim().split("=");
    if (name) directives.set(name.toLowerCase(), rest.join("=").replace(/^"|"$/g, ""));
  }
  return directives;
}

function seconds(raw: string | undefined): number {
  const n = Number.parseInt(raw ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// Durées de fraîcheur / SWR d'une réponse, ou null si elle ne doit pas être stockée.
function edgeTtl(response: Response, cacheControl: string | null): { fresh: number; swr: number } | null {
  if (response.status !== 200) return null; // jamais les 503 de démarrage ni les erreurs
  if (response.headers.has("Set-Cookie") || response.headers.has("Retry-After")) return null;
  const vary = (response.headers.get("Vary") ?? "").toLowerCase();
  if (vary.split(",").some((v) => v.trim() !== "" && v.trim() !== "accept-encoding")) return null;
  const cc = parseCacheControl(cacheControl);
  if (!cc.has("public") || cc.has("private") || cc.has("no-store") || cc.has("no-cache")) return null;
  const fresh = cc.has("s-maxage") ? seconds(cc.get("s-maxage")) : seconds(cc.get("max-age"));
  if (fresh <= 0) return null;
  const mustRevalidate = cc.has("must-revalidate") || cc.has("proxy-revalidate");
  const swr = mustRevalidate ? 0 : seconds(cc.get("stale-while-revalidate"));
  return { fresh, swr };
}

function withEdgeStatus(response: Response, status: string): Response {
  const copy = new Response(response.body, response);
  copy.headers.set(EDGE_STATUS, status);
  return copy;
}

// Requête vers le conteneur pour remplir le cache : GET complet, sans en-têtes conditionnels
// (on veut un 200 à stocker, pas un 304) et sans compression (copie stockée en clair,
// Cloudflare recompresse vers le visiteur).
function fillRequest(url: URL, headers: Headers): Request {
  const h = new Headers(headers);
  for (const name of ["If-None-Match", "If-Modified-Since", "Accept-Encoding"]) h.delete(name);
  return new Request(url.toString(), { method: "GET", headers: h, redirect: "manual" });
}

// Stocke la réponse si ses en-têtes l'autorisent. Renvoie true si elle a été mise en cache.
function storeIfCacheable(response: Response, key: Request, ctx: ExecutionContext): boolean {
  const originCc = response.headers.get("Cache-Control");
  const ttl = edgeTtl(response, originCc);
  if (!ttl) return false;
  const stored = new Response(response.clone().body, response);
  stored.headers.delete(EDGE_STATUS);
  stored.headers.set(STORED_AT, String(Date.now()));
  stored.headers.set(ORIGIN_CC, originCc ?? "");
  stored.headers.set("Cache-Control", `public, max-age=${ttl.fresh + ttl.swr}`);
  ctx.waitUntil(caches.default.put(key, stored).catch((e) => console.error(`[edge-cache] put : ${e}`)));
  return true;
}

// Libère un corps non lu sans attendre (une branche de tee ne se ferme qu'avec l'autre).
function discard(response: Response): void {
  response.body?.cancel().catch(() => {});
}

// Copie servie depuis le cache : Cache-Control d'origine restauré, Age réel, en-têtes internes retirés.
function fromCache(cached: Response, age: number, status: string, head: boolean): Response {
  const headers = new Headers(cached.headers);
  headers.set("Cache-Control", headers.get(ORIGIN_CC) ?? "");
  headers.delete(ORIGIN_CC);
  headers.delete(STORED_AT);
  headers.delete("CF-Cache-Status");
  headers.set("Age", String(age));
  headers.set(EDGE_STATUS, status);
  if (head) discard(cached);
  return new Response(head ? null : cached.body, { status: cached.status, statusText: cached.statusText, headers });
}

// Une seule revalidation à la fois par URL et par isolat (best effort).
const revalidating = new Map<string, number>();
const REVALIDATE_LOCK_MS = 30_000;

function revalidateInBackground(url: URL, headers: Headers, key: Request, env: Env, ctx: ExecutionContext): void {
  const now = Date.now();
  const since = revalidating.get(key.url);
  if (since !== undefined && now - since < REVALIDATE_LOCK_MS) return;
  revalidating.set(key.url, now);
  ctx.waitUntil(
    getContainer(env.TEMPO_APP)
      .fetch(fillRequest(url, headers))
      .then((fresh) => {
        storeIfCacheable(fresh, key, ctx);
        discard(fresh);
      })
      .catch((e) => console.error(`[edge-cache] revalidation ${url.pathname} : ${e}`))
      .finally(() => revalidating.delete(key.url)),
  );
}

async function serveWithEdgeCache(
  request: Request, url: URL, headers: Headers, env: Env, ctx: ExecutionContext,
): Promise<Response> {
  const container = getContainer(env.TEMPO_APP);
  const passThrough = () => container.fetch(new Request(request, { headers, redirect: "manual" }));
  if (!isCacheableRequest(request, url)) return withEdgeStatus(await passThrough(), "BYPASS");

  const version = await currentDataVersion(env);
  if (version === null) return withEdgeStatus(await passThrough(), "BYPASS"); // version inconnue
  const head = request.method.toUpperCase() === "HEAD";
  const key = cacheKey(url, version);
  let cached: Response | undefined;
  try {
    cached = await caches.default.match(key);
  } catch (e) {
    console.error(`[edge-cache] match : ${e}`);
  }
  if (cached) {
    const storedAt = Number(cached.headers.get(STORED_AT));
    const ttl = edgeTtl(cached, cached.headers.get(ORIGIN_CC));
    const age = Math.max(0, Math.floor((Date.now() - storedAt) / 1000));
    if (ttl && Number.isFinite(storedAt) && storedAt > 0 && age < ttl.fresh + ttl.swr) {
      if (age < ttl.fresh) return fromCache(cached, age, "HIT", head);
      revalidateInBackground(url, headers, key, env, ctx);
      return fromCache(cached, age, "STALE", head);
    }
    discard(cached);
  }
  // HEAD absent du cache : transmis tel quel, rien n'est stocké.
  if (head) return withEdgeStatus(await passThrough(), "BYPASS");

  const response = await container.fetch(fillRequest(url, headers));
  const stored = storeIfCacheable(response, key, ctx);
  return withEdgeStatus(response, stored ? "MISS" : "BYPASS");
}

const CANONICAL_ORIGIN = "https://www.calendrier-tempo.fr";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    // Domaine nu ou http → https://www en UN seul saut (les canonicals, le sitemap et les
    // liens pointent vers https://www). Jamais pour *.workers.dev. Concaténation de chaînes
    // (pas new URL(path, base)) : un chemin « //autre.site » ne doit pas changer d'hôte.
    const isWorkersDev = url.hostname.endsWith(".workers.dev");
    if (!isWorkersDev && (url.hostname === "calendrier-tempo.fr" || url.protocol === "http:")) {
      return Response.redirect(`${CANONICAL_ORIGIN}${url.pathname}${url.search}`, 301);
    }
    const headers = new Headers(request.headers);
    // On écrase tout X-Forwarded-For fourni par le client (anti-usurpation).
    headers.delete("X-Forwarded-For");
    const clientIp = request.headers.get("CF-Connecting-IP");
    if (clientIp) headers.set("X-Forwarded-For", clientIp);
    headers.set("X-Forwarded-Proto", url.protocol.replace(":", ""));

    const response = await serveWithEdgeCache(request, url, headers, env, ctx);

    // L'URL de test *.workers.dev ne doit jamais être indexée (contenu dupliqué).
    if (isWorkersDev) {
      const copy = new Response(response.body, response);
      copy.headers.set("X-Robots-Tag", "noindex, nofollow");
      return copy;
    }
    return response;
  },

  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    // Keep-alive : démarre le conteneur s'il est arrêté et renouvelle sleepAfter.
    ctx.waitUntil(
      getContainer(env.TEMPO_APP)
        .fetch(new Request("https://www.calendrier-tempo.fr/health"))
        .then((r) => console.log(`[keepalive] /health ${r.status}`))
        .catch((e) => console.error(`[keepalive] échec : ${e}`)),
    );
  },
};
