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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    // Domaine nu → www (les canonicals, le sitemap et les liens pointent vers www).
    if (url.hostname === "calendrier-tempo.fr") {
      url.hostname = "www.calendrier-tempo.fr";
      return Response.redirect(url.toString(), 301);
    }
    const headers = new Headers(request.headers);
    // On écrase tout X-Forwarded-For fourni par le client (anti-usurpation).
    headers.delete("X-Forwarded-For");
    const clientIp = request.headers.get("CF-Connecting-IP");
    if (clientIp) headers.set("X-Forwarded-For", clientIp);
    headers.set("X-Forwarded-Proto", url.protocol.replace(":", ""));

    const upstream = new Request(request, { headers, redirect: "manual" });
    const response = await getContainer(env.TEMPO_APP).fetch(upstream);

    // L'URL de test *.workers.dev ne doit jamais être indexée (contenu dupliqué).
    if (url.hostname.endsWith(".workers.dev")) {
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
