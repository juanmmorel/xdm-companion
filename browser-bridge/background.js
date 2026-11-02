"use strict";

const EXTENSION_API = globalThis.browser || globalThis.chrome;

if (typeof importScripts === "function") {
  if (!globalThis.XDM_SITE_CATALOG) importScripts("./site-config.js");
  for (const provider of globalThis.XDM_SITE_CATALOG?.providers || []) {
    const module = String(provider?.browser?.module || "");
    if (!/^[a-z0-9_-]+\.js$/i.test(module)) continue;
    const adapter = String(provider?.browser?.adapter || "");
    if (adapter && globalThis.XDM_SITE_ADAPTERS?.[adapter]) continue;
    importScripts(`./providers/${module}`);
  }
}

const SITE_CATALOG = globalThis.XDM_SITE_CATALOG || {
  providers: [],
  companion: { service_url: "http://127.0.0.1:21121" },
};
const COMPANION_BASE_URL = SITE_CATALOG.companion?.service_url || "http://127.0.0.1:21121";

function siteProviderForUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    return (SITE_CATALOG.providers || []).find(provider =>
      (provider.domains || []).some(domain => host === domain || host.endsWith(`.${domain}`)),
    ) || null;
  } catch (_) {
    return null;
  }
}

function isComplexPage(rawUrl) {
  return Boolean(siteProviderForUrl(rawUrl));
}

async function browserCookies(pageUrl, provider) {
  const queries = [{ kind: "page", query: { url: pageUrl } }];
  for (const item of provider?.browser?.cookie_queries || []) {
    const query = item.url ? { url: item.url } : item.domain ? { domain: item.domain } : null;
    if (query) queries.push({ kind: String(item.kind || "provider"), query });
  }
  // A browser may reject one query (for example, a partitioned-cookie query)
  // while still allowing the others. Keep every successful cookie batch.
  const results = await Promise.all(queries.map(async ({ kind, query }) => {
    try {
      const cookies = await EXTENSION_API.cookies.getAll(query);
      return { kind, cookies, error: "" };
    } catch (error) {
      return {
        kind,
        cookies: [],
        error: String(error && error.name || "Error").slice(0, 48),
      };
    }
  }));
  const unique = new Map();
  for (const cookie of results.flatMap(result => result.cookies)) {
    const key = [cookie.storeId, cookie.domain, cookie.path, cookie.name,
      JSON.stringify(cookie.partitionKey || null)].join("\u0000");
    unique.set(key, cookie);
  }
  const cookies = [...unique.values()].map(cookie => ({
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    hostOnly: cookie.hostOnly,
    path: cookie.path,
    secure: cookie.secure,
    httpOnly: cookie.httpOnly,
    session: cookie.session,
    expirationDate: cookie.expirationDate,
  }));
  return {
    cookies,
    queries: results,
  };
}

async function requestFormats(message, sender) {
  const pageUrl = message.pageUrl || (sender.tab && sender.tab.url);
  const provider = pageUrl && siteProviderForUrl(pageUrl);
  if (!provider) {
    return { ok: false, message: "Esta página no está configurada para XDM Companion." };
  }
  const adapterName = String(provider.browser?.adapter || "");
  const adapter = globalThis.XDM_SITE_ADAPTERS?.[adapterName];
  // Reading the in-page player and querying browser cookies do not depend on
  // one another. Start both immediately so prefetch spends less time waiting
  // before it can ask the local companion for formats.
  const playbackPromise = adapter
    ? adapter.readPlayback(sender.tab && sender.tab.id, pageUrl, provider)
    : Promise.resolve({ playback: null, diagnostics: {} });
  const cookiesPromise = browserCookies(pageUrl, provider);
  const [playbackResult, browserSession] = await Promise.all([
    playbackPromise,
    cookiesPromise,
  ]);
  const browserLanguages = Array.isArray(navigator.languages) && navigator.languages.length
    ? navigator.languages
    : [navigator.language].filter(Boolean);
  const payload = {
    url: pageUrl,
    cookies: browserSession.cookies,
    user_agent: navigator.userAgent,
    accept_language: browserLanguages.join(","),
    title: message.title || (sender.tab && sender.tab.title) || "",
  };
  adapter?.attachPayload(payload, browserSession, playbackResult, provider);
  return sendToCompanion("/formats", payload);
}

async function selectFormat(message) {
  if (typeof message.session !== "string" || typeof message.formatId !== "string") {
    return { ok: false, message: "La opción de resolución venció. Vuelve a abrir el menú de XDM." };
  }
  return sendToCompanion("/select", {
    session: message.session,
    format_id: message.formatId,
  });
}

async function sendToCompanion(path, payload) {
  try {
    const response = await fetch(`${COMPANION_BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      return {
        ok: false,
        message: result.message || "XDM Companion no pudo procesar el video.",
      };
    }
    return { ok: true, ...result };
  } catch (error) {
    console.warn("XDM Companion no pudo contactar el servicio local:", error);
    return {
      ok: false,
      message: "No se pudo contactar XDM Companion. Comprueba que el servicio esté activo.",
    };
  }
}

EXTENSION_API.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !["xdm-companion:get-formats", "xdm-companion:select-format"].includes(message.type)) {
    return false;
  }
  const operation = message.type === "xdm-companion:get-formats"
    ? requestFormats(message, sender || {})
    : selectFormat(message);
  void operation.then(
    result => {
      if (typeof sendResponse === "function") sendResponse(result);
    },
    error => {
      console.warn("XDM Companion no pudo procesar la selección:", error);
      if (typeof sendResponse === "function") {
        sendResponse({ ok: false, message: "No se pudo procesar la solicitud de XDM Companion." });
      }
    },
  );
  return true;
});
