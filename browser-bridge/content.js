"use strict";

(() => {
  const observedVideos = new WeakSet();
  const observedRoots = new WeakSet();
  const FORMAT_CACHE_TTL_MS = 4 * 60 * 1000;
  const PREFETCH_RETRY_COOLDOWN_MS = 8 * 1000;
  const FORMAT_CACHE_LIMIT = 4;
  const MENU_AUTO_CLOSE_MS = 5 * 1000;
  let activeVideo = null;
  let activePageUrl = location.href;
  let activeTitle = document.title;
  let activeIsPreview = false;
  let hideTimer = 0;
  let menuAutoCloseTimer = 0;
  let ui = null;
  let playlistUi = null;
  let playlistPollTimer = 0;
  let playlistMenuTimer = 0;
  let playlistStatusTimer = 0;
  let cachedFormats = new Map();
  let pendingFormats = new Map();
  let recentFormatFailures = new Map();
  const playerDataRetries = new Set();
  let observedPageUrl = location.href;
  let pageGeneration = 0;

  function extensionRuntime() {
    return globalThis.browser?.runtime || globalThis.chrome?.runtime || null;
  }

  function extensionMessageError(error) {
    const detail = String(error && error.message || "");
    if (!detail || /context invalidated|receiving end does not exist|message port closed/i.test(detail)) {
      return new Error(
        "La extensión XDM Companion no respondió. Recarga la extensión en el navegador y después recarga esta página.",
      );
    }
    return error instanceof Error ? error : new Error(detail);
  }

  function sendBridgeMessage(message) {
    const runtime = extensionRuntime();
    if (!runtime || typeof runtime.sendMessage !== "function") {
      return Promise.reject(new Error(
        "La conexión con la extensión de XDM no está disponible. Recarga esta página y vuelve a intentarlo.",
      ));
    }

    if (globalThis.browser?.runtime === runtime) {
      try {
        return Promise.resolve(runtime.sendMessage(message)).catch(error => {
          throw extensionMessageError(error);
        });
      } catch (error) {
        return Promise.reject(extensionMessageError(error));
      }
    }

    try {
      return new Promise((resolve, reject) => {
        let settled = false;
        const succeed = value => {
          if (settled) return;
          settled = true;
          resolve(value);
        };
        const fail = error => {
          if (settled) return;
          settled = true;
          reject(extensionMessageError(error));
        };
        const callback = value => {
          const lastError = runtime.lastError;
          if (lastError) fail(new Error(lastError.message || "No se pudo contactar el puente de XDM."));
          else succeed(value);
        };
        const response = runtime.sendMessage(message, callback);
        if (response && typeof response.then === "function") {
          response.then(succeed, fail);
        }
      });
    } catch (error) {
      return Promise.reject(extensionMessageError(error));
    }
  }

  function providerForUrl(rawUrl) {
    try {
      const url = new URL(rawUrl);
      const host = url.hostname.toLowerCase().replace(/\.$/, "");
      return (globalThis.XDM_SITE_CATALOG?.providers || []).find(provider =>
        (provider.domains || []).some(domain => host === domain || host.endsWith(`.${domain}`)),
      ) || null;
    } catch (_) {
      return null;
    }
  }

  function hasVideoId(rawUrl, provider) {
    try {
      const url = new URL(rawUrl);
      const player = provider?.browser?.player || {};
      let candidate = "";
      if ((player.short_link_domains || []).includes(url.hostname)) {
        candidate = url.pathname.split("/").filter(Boolean)[0] || "";
      } else {
        candidate = url.searchParams.get(player.video_id_query || "") || "";
        if (!candidate) {
          for (const prefix of player.video_path_prefixes || []) {
            if (url.pathname.startsWith(prefix)) {
              candidate = url.pathname.slice(prefix.length).split("/")[0] || "";
              break;
            }
          }
        }
      }
      return Boolean(candidate && new RegExp(`^(?:${player.video_id_pattern || "[A-Za-z0-9_-]{11}"})$`).test(candidate));
    } catch (_) {
      return false;
    }
  }

  function composedParent(element) {
    if (element?.parentElement) return element.parentElement;
    const root = element?.getRootNode?.();
    return root && root !== element && root.host ? root.host : null;
  }

  function videoTargetFor(video) {
    const provider = providerForUrl(location.href);
    if (!provider) return { pageUrl: location.href, title: document.title, isPreview: false };

    const preview = provider.browser?.preview || {};
    const cardSelectors = Array.isArray(preview.card_selectors) ? preview.card_selectors : [];
    const linkSelectors = Array.isArray(preview.link_selectors) ? preview.link_selectors : [];
    let card = null;
    for (let node = video; node && node !== document.documentElement; node = composedParent(node)) {
      if (cardSelectors.some(selector => {
        try { return node.matches(selector); } catch (_) { return false; }
      })) {
        card = node;
        break;
      }
    }

    if (card && linkSelectors.length) {
      const links = new Set();
      for (const selector of linkSelectors) {
        try {
          if (card.matches(selector)) links.add(card);
          for (const link of card.querySelectorAll(selector)) links.add(link);
        } catch (_) {}
      }
      for (const link of links) {
        const href = link.href || link.getAttribute("href");
        if (!href) continue;
        let pageUrl;
        try { pageUrl = new URL(href, location.href).href; } catch (_) { continue; }
        if (!providerForUrl(pageUrl) || !hasVideoId(pageUrl, provider)) continue;

        let title = String(link.getAttribute("title") || link.getAttribute("aria-label") || "").trim();
        if (!title) {
          for (const selector of preview.title_selectors || []) {
            try {
              title = String(card.querySelector(selector)?.textContent || "").trim();
            } catch (_) { title = ""; }
            if (title) break;
          }
        }
        return { pageUrl, title: title.slice(0, 500) || document.title, isPreview: true };
      }
    }

    if (card) {
      const attributes = Array.isArray(preview.video_id_attributes)
        ? preview.video_id_attributes : [];
      const template = String(preview.video_url_template || "");
      const player = provider.browser?.player || {};
      const pattern = new RegExp(`^(?:${player.video_id_pattern || "[A-Za-z0-9_-]{11}"})$`);
      const nodes = [card];
      try { nodes.push(...card.querySelectorAll("*")); } catch (_) {}
      if (template.includes("{video_id}")) {
        for (const node of nodes) {
          for (const attribute of attributes) {
            const candidate = String(node.getAttribute?.(attribute) || "").trim();
            if (!candidate || !pattern.test(candidate)) continue;
            let pageUrl;
            try {
              pageUrl = new URL(
                template.replace(/\{video_id\}/g, encodeURIComponent(candidate)),
                location.href,
              ).href;
            } catch (_) { continue; }
            if (!providerForUrl(pageUrl) || !hasVideoId(pageUrl, provider)) continue;

            let title = "";
            for (const selector of preview.title_selectors || []) {
              try {
                title = String(card.querySelector(selector)?.textContent || "").trim();
              } catch (_) { title = ""; }
              if (title) break;
            }
            return { pageUrl, title: title.slice(0, 500) || document.title, isPreview: true };
          }
        }
      }
    }

    // If this video belongs to a thumbnail card, never fall back to the
    // currently open watch URL. That could send the wrong video to XDM.
    if (card) return null;
    if (hasVideoId(location.href, provider)) {
      return { pageUrl: location.href, title: document.title, isPreview: false };
    }
    // A preview without a matching video link must not accidentally request
    // the search/feed URL as though it were an individual video.
    if (cardSelectors.length) return null;
    return { pageUrl: location.href, title: document.title, isPreview: false };
  }

  function formatCacheKey(pageUrl, isPreview) {
    return `${isPreview ? "preview" : "page"}\u0000${pageUrl}`;
  }

  function hasUsableFormats(response) {
    return Boolean(
      response && response.ok && typeof response.session === "string"
      && Array.isArray(response.formats) && response.formats.length > 0,
    );
  }

  function invalidateFormats(pageUrl) {
    if (!pageUrl) {
      cachedFormats.clear();
      recentFormatFailures.clear();
      return;
    }
    for (const isPreview of [false, true]) {
      const key = formatCacheKey(pageUrl, isPreview);
      cachedFormats.delete(key);
      recentFormatFailures.delete(key);
    }
  }

  function remember(map, pageUrl, value) {
    map.delete(pageUrl);
    map.set(pageUrl, value);
    while (map.size > FORMAT_CACHE_LIMIT) map.delete(map.keys().next().value);
  }

  function requestFormats(pageUrl, title = document.title, isPreview = false) {
    const key = formatCacheKey(pageUrl, isPreview);
    const entry = { pageUrl, promise: null };
    entry.promise = sendBridgeMessage({
      type: "xdm-companion:get-formats",
      pageUrl,
      title,
      preview: isPreview,
    }).then(response => {
      if (hasUsableFormats(response)) {
        remember(cachedFormats, key, { response, expiresAt: Date.now() + FORMAT_CACHE_TTL_MS });
        recentFormatFailures.delete(key);
      } else {
        cachedFormats.delete(key);
        remember(recentFormatFailures, key, { response, failedAt: Date.now() });
      }
      return response;
    }).catch(error => {
      cachedFormats.delete(key);
      remember(recentFormatFailures, key, { error, failedAt: Date.now() });
      throw error;
    }).finally(() => {
      if (pendingFormats.get(key) === entry) pendingFormats.delete(key);
    });
    pendingFormats.set(key, entry);
    return entry.promise;
  }

  async function getFormats(pageUrl, { retryEmpty = false, title = document.title, isPreview = false } = {}) {
    const key = formatCacheKey(pageUrl, isPreview);
    const now = Date.now();
    const cached = cachedFormats.get(key);
    if (cached && cached.expiresAt > now) {
      // Refresh insertion order so the small cache behaves as an LRU.
      remember(cachedFormats, key, cached);
      return cached.response;
    }
    if (cached) cachedFormats.delete(key);

    const pending = pendingFormats.get(key);
    if (pending) {
      const response = await pending.promise;
      if (hasUsableFormats(response) || !retryEmpty) return response;
      // A click is an explicit request. If prefetch ran before player data was
      // ready, retry once now.
      return requestFormats(pageUrl, title, isPreview);
    }

    const failure = recentFormatFailures.get(key);
    if (
      !retryEmpty && failure
      && now - failure.failedAt < PREFETCH_RETRY_COOLDOWN_MS
    ) {
      if (failure.error) throw failure.error;
      return failure.response;
    }
    recentFormatFailures.delete(key);
    return requestFormats(pageUrl, title, isPreview);
  }

  function prefetchFormats(
    pageUrl = activePageUrl || location.href,
    title = activeTitle || document.title,
    isPreview = activeIsPreview,
  ) {
    if (!pageUrl) return;
    void getFormats(pageUrl, { title, isPreview }).catch(() => {});
  }

  function prefetchVideo(video) {
    if (isPlaylistListingPage(location.href)) return;
    const target = videoTargetFor(video);
    if (target) prefetchFormats(target.pageUrl, target.title, target.isPreview);
  }

  function syncPageContext() {
    const pageUrl = location.href;
    syncPlaylistControl();
    if (isPlaylistListingPage(pageUrl)) hideVideoPicker();
    if (pageUrl === observedPageUrl) return false;
    observedPageUrl = pageUrl;
    pageGeneration += 1;
    playerDataRetries.clear();
    const liveVideo = activeVideo?.isConnected ? activeVideo : null;
    const activeTarget = liveVideo ? videoTargetFor(liveVideo) : null;
    const provider = providerForUrl(pageUrl);
    const currentPageCanResolve = !provider || hasVideoId(pageUrl, provider);
    activePageUrl = activeTarget?.pageUrl || (liveVideo ? "" : currentPageCanResolve ? pageUrl : "");
    activeTitle = activeTarget?.title || (activePageUrl ? document.title : "");
    activeIsPreview = Boolean(activeTarget?.isPreview);

    const menuWasOpen = Boolean(ui && ui.widget.classList.contains("open"));
    if (ui) {
      ui.session = null;
      ui.pageUrl = activePageUrl;
      ui.choices.replaceChildren();
      ui.status.textContent = activePageUrl
        ? "Preparando resoluciones…"
        : "No se pudo identificar el video de esta vista previa.";
      ui.trigger.disabled = false;
    }

    if (menuWasOpen && activeVideo) {
      // Keep an already-open picker useful when the site changes videos.
      void loadFormats();
    } else if (activePageUrl) {
      prefetchFormats(activePageUrl, activeTitle, activeIsPreview);
    }
    return true;
  }

  function playlistTargetFor(rawUrl) {
    try {
      const url = new URL(rawUrl);
      const provider = providerForUrl(rawUrl);
      const config = provider?.browser?.playlist || {};
      if (config.enabled !== true) return null;
      const queryParameters = Array.isArray(config.query_parameters)
        ? config.query_parameters : [];
      const pathPrefixes = Array.isArray(config.path_prefixes)
        ? config.path_prefixes : [];
      const playlistParameter = queryParameters.find(parameter =>
        typeof parameter === "string" && Boolean(url.searchParams.get(parameter)),
      );
      const playlistId = playlistParameter ? url.searchParams.get(playlistParameter) : "";
      const hasQuery = Boolean(playlistId);
      const hasPath = pathPrefixes.some(prefix =>
        typeof prefix === "string" && prefix && url.pathname.startsWith(prefix),
      );
      let pageUrl = url.href;
      const template = String(config.url_template || "");
      if (hasQuery && template.includes("{playlist_id}")) {
        pageUrl = template.replace("{playlist_id}", encodeURIComponent(playlistId));
      }
      return hasQuery || hasPath
        ? { provider, pageUrl, isListingPage: hasPath }
        : null;
    } catch (_) {
      return null;
    }
  }

  function isPlaylistListingPage(rawUrl) {
    return Boolean(playlistTargetFor(rawUrl)?.isListingPage);
  }

  function createPlaylistUi() {
    if (playlistUi || !document.documentElement) return playlistUi;
    const host = document.createElement("div");
    host.setAttribute("aria-label", "Descargar lista con XDM");
    host.style.cssText = [
      "all:initial",
      "position:fixed",
      "display:block",
      "margin:0",
      "z-index:2147483647",
      "pointer-events:auto",
    ].join(";");
    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; color-scheme: light; }
      #playlist-widget {
        position: relative; z-index: 20;
        display: flex; flex-direction: column; align-items: flex-start; gap: 7px;
        pointer-events: auto; font: 13px/1.35 system-ui, sans-serif; color: #f8fbff;
      }
      #playlist-trigger {
        display: inline-flex; align-items: center; justify-content: center;
        width: 86px; max-width: calc(100vw - 24px); padding: 0; border: 0;
        background: transparent; cursor: pointer; pointer-events: auto;
        filter: drop-shadow(0 2px 8px rgba(0,0,0,.35));
      }
      #playlist-trigger img { display: block; width: 100%; height: auto; }
      #playlist-trigger:disabled { opacity: .65; cursor: wait; }
      #playlist-trigger:focus-visible { outline: 2px solid #76d7ff; outline-offset: 2px; }
      #playlist-status {
        display: none; position: fixed; top: 0; left: 0;
        width: min(252px, calc(100vw - 16px)); box-sizing: border-box;
        max-height: min(360px, calc(100vh - 64px)); overflow: hidden;
        border: 1px solid rgba(255,255,255,.18); border-radius: 10px;
        background: #101923; color: #f8fbff; box-shadow: 0 8px 28px rgba(0,0,0,.5);
        pointer-events: auto; z-index: 2147483647;
      }
      #playlist-widget.open #playlist-status { display: block; }
      #playlist-widget[hidden] { display: none; }
      #playlist-menu-title { padding: 10px 12px 8px; color: #b9d9f6; font-weight: 650; }
      #playlist-message { padding: 2px 12px 10px; color: #c4cfdd; overflow-wrap: anywhere; }
      #playlist-choices {
        max-height: 270px; overflow-y: auto; padding: 0 6px 6px;
      }
      #playlist-choices[hidden] { display: none; }
      .playlist-choice {
        display: flex; width: 100%; align-items: center; justify-content: space-between;
        gap: 12px; box-sizing: border-box; padding: 9px 8px; border: 0;
        border-radius: 6px; background: transparent; color: #fff; text-align: left;
        font: 500 13px system-ui,sans-serif; cursor: pointer;
      }
      .playlist-choice:hover:not(:disabled), .playlist-choice:focus-visible { background: #23476a; outline: none; }
      .playlist-choice:disabled { opacity: .55; cursor: wait; }
    `;
    const widget = document.createElement("div");
    widget.id = "playlist-widget";
    widget.setAttribute("hidden", "");
    const trigger = document.createElement("button");
    trigger.id = "playlist-trigger";
    trigger.type = "button";
    trigger.title = "Enviar la lista a XDM en orden, con la mejor calidad compatible por video";
    trigger.setAttribute("aria-label", "Enviar lista a XDM con la mejor calidad compatible por video");
    trigger.setAttribute("aria-expanded", "false");
    const art = document.createElement("img");
    art.alt = "";
    art.setAttribute("aria-hidden", "true");
    const runtime = extensionRuntime();
    if (typeof runtime?.getURL === "function") art.src = runtime.getURL("icons/xdm-download-button.png");
    else {
      art.style.display = "none";
      trigger.textContent = "XDM";
    }
    const status = document.createElement("div");
    status.id = "playlist-status";
    status.setAttribute("role", "dialog");
    status.setAttribute("aria-label", "Descargar lista con XDM");
    const menuTitle = document.createElement("div");
    menuTitle.id = "playlist-menu-title";
    menuTitle.textContent = "Descargar lista con XDM";
    const message = document.createElement("div");
    message.id = "playlist-message";
    message.setAttribute("role", "status");
    message.textContent = "Pulsa el botón para buscar los videos de la lista.";
    const choices = document.createElement("div");
    choices.id = "playlist-choices";
    choices.setAttribute("role", "group");
    choices.setAttribute("aria-label", "Cantidad de videos para enviar a XDM");
    choices.setAttribute("hidden", "");
    status.append(menuTitle, message, choices);
    trigger.append(art);
    widget.append(trigger, status);
    shadow.append(style, widget);
    document.documentElement.append(host);
    playlistUi = {
      host, shadow, widget, trigger, status, message, choices, pageUrl: "", jobId: "", anchor: null,
      phase: "idle", downloadableCount: 0,
    };
    trigger.addEventListener("click", event => {
      consumeEvent(event);
      openPlaylistMenu();
    }, true);
    widget.addEventListener("pointerdown", event => event.stopPropagation());
    widget.addEventListener("click", event => event.stopPropagation());
    window.addEventListener("resize", repositionPlaylistUi, { passive: true });
    window.addEventListener("scroll", repositionPlaylistUi, { passive: true, capture: true });
    return playlistUi;
  }

  function queryPlaylistSelector(selector) {
    try {
      const findInOpenRoots = (root, query) => {
        if (!root || typeof root.querySelector !== "function") return null;
        try {
          const direct = root.querySelector(query);
          if (direct) return direct;
        } catch (_) {}
        if (typeof root.querySelectorAll !== "function") return null;
        let nodes = [];
        try { nodes = root.querySelectorAll("*"); } catch (_) {}
        for (const node of nodes) {
          if (!node.shadowRoot) continue;
          const nested = findInOpenRoots(node.shadowRoot, query);
          if (nested) return nested;
        }
        return null;
      };
      const separator = selector.indexOf(" ");
      if (separator < 0) return findInOpenRoots(document, selector);
      const host = findInOpenRoots(document, selector.slice(0, separator));
      const innerSelector = selector.slice(separator + 1).trim();
      if (!host) return null;
      return findInOpenRoots(host.shadowRoot || host, innerSelector);
    } catch (_) {
      return null;
    }
  }

  function playlistAnchorFor(provider) {
    const selectors = Array.isArray(provider?.browser?.playlist?.anchor_selectors)
      ? provider.browser.playlist.anchor_selectors : [];
    for (const selector of selectors) {
      const element = queryPlaylistSelector(selector);
      if (!element) continue;
      return element.tagName === "BUTTON" ? element.parentElement || element : element;
    }
    return null;
  }

  function playlistInsertionPoint(anchor, provider) {
    const selectors = Array.isArray(provider?.browser?.playlist?.insert_before_selectors)
      ? provider.browser.playlist.insert_before_selectors : [];
    for (const selector of selectors) {
      let element = null;
      try { element = anchor.querySelector(selector); } catch (_) {}
      if (element) return element;
    }
    return null;
  }

  function positionPlaylistMenu() {
    if (!playlistUi || !playlistUi.widget.classList.contains("open")) return;
    const triggerRect = playlistUi.trigger.getBoundingClientRect();
    const viewportWidth = Number(window.innerWidth || globalThis.innerWidth) || 0;
    const viewportHeight = Number(window.innerHeight || globalThis.innerHeight) || 0;
    if (!viewportWidth || !viewportHeight) return;
    const menuRect = playlistUi.status.getBoundingClientRect();
    const menuWidth = Math.min(menuRect.width || 300, viewportWidth - 16);
    const menuHeight = Math.min(menuRect.height || 240, viewportHeight - 16);
    const left = Math.max(8, Math.min(triggerRect.right - menuWidth, viewportWidth - menuWidth - 8));
    let top = triggerRect.bottom + 6;
    if (top + menuHeight > viewportHeight - 8) {
      top = Math.max(8, triggerRect.top - menuHeight - 6);
    }
    playlistUi.status.style.left = `${left}px`;
    playlistUi.status.style.top = `${top}px`;
  }

  function repositionPlaylistUi() {
    syncPlaylistControl();
    positionPlaylistMenu();
  }

  function positionPlaylistControl(anchor, before) {
    if (!playlistUi || !anchor) return;
    const reference = before || anchor;
    const rect = reference.getBoundingClientRect();
    const viewportWidth = Number(window.innerWidth || globalThis.innerWidth) || 0;
    const viewportHeight = Number(window.innerHeight || globalThis.innerHeight) || 0;
    if (!viewportWidth || !viewportHeight || (!rect.width && !rect.height)) return;
    const buttonWidth = 86;
    const buttonHeight = 30;
    const desiredLeft = before ? rect.left - buttonWidth - 6 : rect.right - buttonWidth;
    const left = Math.max(8, Math.min(desiredLeft, viewportWidth - buttonWidth - 8));
    const top = Math.max(8, Math.min(
      rect.top + (rect.height - buttonHeight) / 2,
      viewportHeight - buttonHeight - 8,
    ));
    playlistUi.host.style.left = `${left}px`;
    playlistUi.host.style.top = `${top}px`;
  }

  function syncPlaylistControl() {
    if (typeof window !== "undefined" && window.top && window.top !== window) return;
    const target = playlistTargetFor(location.href);
    if (!target) {
      if (playlistUi) {
        playlistUi.widget.setAttribute("hidden", "");
        playlistUi.widget.classList.remove("open");
      }
      return;
    }
    const anchor = playlistAnchorFor(target.provider);
    // Align the fixed control with the playlist actions so it stays above the
    // page's overflow clipping while occupying the slot before the menu.
    if (!anchor) {
      if (playlistUi) playlistUi.widget.setAttribute("hidden", "");
      return;
    }
    const controls = createPlaylistUi();
    if (!controls) return;
    controls.pageUrl = target.pageUrl;
    const before = playlistInsertionPoint(anchor, target.provider);
    if (controls.host.parentElement !== document.documentElement) document.documentElement.append(controls.host);
    controls.anchor = anchor;
    controls.before = before;
    positionPlaylistControl(anchor, before);
    controls.widget.removeAttribute("hidden");
  }

  function hideVideoPicker() {
    cancelHide();
    if (!ui) {
      activeVideo = null;
      return;
    }
    ui.widget.classList.remove("visible", "open");
    ui.widget.setAttribute("hidden", "");
    ui.trigger.setAttribute("aria-expanded", "false");
    activeVideo = null;
  }

  function renderPlaylistStatus(job) {
    if (!playlistUi || !job) return;
    if (playlistStatusTimer) clearTimeout(playlistStatusTimer);
    playlistStatusTimer = 0;
    const total = job.total !== null && job.total !== undefined && Number.isFinite(Number(job.total))
      ? Number(job.total) : null;
    if (job.status === "enumerating") {
      playlistUi.phase = "previewing";
      playlistUi.choices.setAttribute("hidden", "");
      playlistUi.message.textContent = "Leyendo la lista de reproducción…";
    } else if (job.status === "awaiting_selection") {
      playlistUi.phase = "ready";
      playlistUi.downloadableCount = Number(job.downloadable_count) || 0;
      playlistUi.trigger.disabled = false;
      playlistUi.message.textContent = `Se detectaron ${playlistUi.downloadableCount} videos con enlace en esta lista.`
        + (playlistUi.downloadableCount ? " Elige cuántos enviar a XDM." : "");
      renderPlaylistChoices(playlistUi.downloadableCount);
      playlistUi.choices.removeAttribute("hidden");
      schedulePlaylistMenuAutoClose();
    } else if (["completed", "completed_with_issues"].includes(job.status)) {
      playlistUi.phase = "complete";
      playlistUi.choices.setAttribute("hidden", "");
      const totalLabel = total === null ? job.processed : total;
      playlistUi.message.textContent =
        `Lista procesada: ${job.queued} en cola, ${job.skipped} omitidos y ${job.failed} fallidos de ${totalLabel}.`;
      playlistUi.trigger.disabled = false;
      if (Array.isArray(job.issues) && job.issues.length) {
        const sample = job.issues.slice(0, 4).map(issue =>
          `${issue.position}. ${issue.title}: ${issue.reason}`,
        ).join(" · ");
        playlistUi.message.textContent += ` Detalles: ${sample}`;
      }
      playlistStatusTimer = setTimeout(closePlaylistMenu, MENU_AUTO_CLOSE_MS);
    } else if (job.status === "failed") {
      playlistUi.phase = "failed";
      playlistUi.choices.setAttribute("hidden", "");
      playlistUi.message.textContent = job.message || "No se pudo procesar la lista.";
      playlistUi.trigger.disabled = false;
      playlistStatusTimer = setTimeout(closePlaylistMenu, MENU_AUTO_CLOSE_MS);
    } else {
      playlistUi.phase = "processing";
      playlistUi.choices.setAttribute("hidden", "");
      const current = job.current_title ? ` · ${job.current_title}` : "";
      playlistUi.message.textContent =
        `Enviando a XDM: ${job.processed} de ${total ?? "…"}${current}. `
        + `${job.queued} en cola, ${job.skipped} omitidos, ${job.failed} fallidos.`;
      playlistUi.trigger.disabled = false;
    }
    positionPlaylistMenu();
  }

  function renderPlaylistChoices(count) {
    if (!playlistUi) return;
    playlistUi.choices.replaceChildren();
    const limits = count < 5
      ? ["all"]
      : count <= 20
        ? [5, 10, 15, "all"]
        : count < 40
          ? [10, 20, "all"]
          : [10, 20, 30, "all"];
    for (const limit of limits) {
      const choice = document.createElement("button");
      choice.type = "button";
      choice.className = "playlist-choice";
      choice.setAttribute("data-limit", String(limit));
      if (limit === "all") {
        choice.textContent = `Descargar todos (${count})`;
        choice.disabled = count < 1;
      } else {
        choice.textContent = `Descargar ${limit}`;
        choice.disabled = count < limit;
      }
      choice.addEventListener("click", event => {
        consumeEvent(event);
        void queuePlaylistSelection(limit);
      }, true);
      playlistUi.choices.append(choice);
    }
  }

  function closePlaylistMenu() {
    if (playlistMenuTimer) clearTimeout(playlistMenuTimer);
    playlistMenuTimer = 0;
    if (playlistUi) {
      playlistUi.widget.classList.remove("open");
      playlistUi.trigger.setAttribute("aria-expanded", "false");
    }
  }

  function schedulePlaylistMenuAutoClose() {
    if (playlistMenuTimer) clearTimeout(playlistMenuTimer);
    playlistMenuTimer = setTimeout(closePlaylistMenu, MENU_AUTO_CLOSE_MS);
  }

  function openPlaylistMenu() {
    const controls = playlistUi;
    if (!controls) return;
    controls.widget.classList.add("open");
    controls.trigger.setAttribute("aria-expanded", "true");
    if (["idle", "failed", "complete"].includes(controls.phase)) {
      void startPlaylistPreview();
    } else if (controls.phase === "ready") {
      renderPlaylistChoices(controls.downloadableCount);
      controls.choices.removeAttribute("hidden");
      schedulePlaylistMenuAutoClose();
    } else {
      schedulePlaylistMenuAutoClose();
    }
    positionPlaylistMenu();
  }

  function schedulePlaylistPoll(jobId) {
    if (playlistPollTimer) clearTimeout(playlistPollTimer);
    playlistPollTimer = setTimeout(() => {
      playlistPollTimer = 0;
      void pollPlaylistJob(jobId);
    }, 1_500);
  }

  async function pollPlaylistJob(jobId) {
    if (!playlistUi || playlistUi.jobId !== jobId) return;
    try {
      const response = await sendBridgeMessage({
        type: "xdm-companion:playlist-status",
        jobId,
      });
      if (!response || !response.ok) throw new Error(response?.message || "No se pudo consultar el progreso de la lista.");
      const job = response.job;
      renderPlaylistStatus(job);
      if (job && !["awaiting_selection", "completed", "completed_with_issues", "failed"].includes(job.status)) {
        schedulePlaylistPoll(jobId);
      } else if (playlistUi && job.status !== "awaiting_selection") {
        playlistUi.jobId = "";
      }
    } catch (error) {
      if (!playlistUi || playlistUi.jobId !== jobId) return;
      playlistUi.message.textContent = `${error.message || "No se pudo consultar el progreso."} La lista puede seguir procesándose en segundo plano.`;
      playlistUi.trigger.disabled = false;
      playlistUi.jobId = "";
    }
  }

  async function startPlaylistPreview() {
    const controls = playlistUi;
    if (!controls || !controls.pageUrl) return;
    if (playlistStatusTimer) clearTimeout(playlistStatusTimer);
    playlistStatusTimer = 0;
    if (playlistMenuTimer) clearTimeout(playlistMenuTimer);
    playlistMenuTimer = 0;
    controls.jobId = "";
    controls.phase = "previewing";
    controls.choices.setAttribute("hidden", "");
    controls.widget.classList.add("open");
    controls.trigger.setAttribute("aria-expanded", "true");
    controls.message.textContent = "Detectando los videos disponibles en la lista…";
    positionPlaylistMenu();
    try {
      const response = await sendBridgeMessage({
        type: "xdm-companion:start-playlist",
        pageUrl: controls.pageUrl,
        title: document.title,
      });
      if (!response || !response.ok) throw new Error(response?.message || "No se pudo iniciar la lista.");
      const job = response.job;
      if (!job || typeof job.id !== "string") throw new Error("XDM Companion no devolvió el estado de la lista.");
      controls.jobId = job.id;
      renderPlaylistStatus(job);
      if (!["awaiting_selection", "completed", "completed_with_issues", "failed"].includes(job.status)) {
        schedulePlaylistPoll(job.id);
      } else {
        if (job.status !== "awaiting_selection") controls.jobId = "";
      }
    } catch (error) {
      controls.phase = "failed";
      controls.message.textContent = error.message || "No se pudo iniciar la lista.";
      playlistStatusTimer = setTimeout(closePlaylistMenu, MENU_AUTO_CLOSE_MS);
    }
  }

  async function queuePlaylistSelection(limit) {
    const controls = playlistUi;
    if (!controls || controls.phase !== "ready" || !controls.jobId) return;
    if (playlistMenuTimer) clearTimeout(playlistMenuTimer);
    playlistMenuTimer = 0;
    controls.phase = "queueing";
    controls.choices.setAttribute("hidden", "");
    controls.message.textContent = limit === "all"
      ? `Enviando los ${controls.downloadableCount} videos a XDM…`
      : `Enviando los primeros ${Math.min(limit, controls.downloadableCount)} videos a XDM…`;
    schedulePlaylistMenuAutoClose();
    positionPlaylistMenu();
    try {
      const response = await sendBridgeMessage({
        type: "xdm-companion:queue-playlist",
        pageUrl: controls.pageUrl,
        jobId: controls.jobId,
        limit,
      });
      if (!response || !response.ok) throw new Error(response?.message || "No se pudo enviar la lista a XDM.");
      const job = response.job;
      if (!job || typeof job.id !== "string") throw new Error("XDM Companion no devolvió el estado de la lista.");
      controls.jobId = job.id;
      renderPlaylistStatus(job);
      if (!["completed", "completed_with_issues", "failed"].includes(job.status)) {
        schedulePlaylistPoll(job.id);
      } else {
        controls.jobId = "";
      }
    } catch (error) {
      controls.phase = "ready";
      controls.message.textContent = error.message || "No se pudo enviar la lista a XDM.";
      renderPlaylistChoices(controls.downloadableCount);
      controls.choices.removeAttribute("hidden");
      schedulePlaylistMenuAutoClose();
    }
  }

  function consumeEvent(event) {
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
  }

  function createUi() {
    if (ui || !document.documentElement) return ui;

    const host = document.createElement("div");
    host.setAttribute("aria-label", "XDM Companion");
    host.style.cssText = [
      "all:initial",
      "position:fixed",
      "left:0",
      "top:0",
      "width:0",
      "height:0",
      "z-index:2147483647",
      "pointer-events:none",
      "overflow:visible",
    ].join(";");

    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; color-scheme: light; }
      #widget {
        position: fixed; width: 252px; z-index: 2147483647;
        display: flex; flex-direction: column; align-items: flex-end;
        gap: 7px; pointer-events: none; font: 13px/1.35 system-ui, sans-serif;
        color: #f8fbff;
      }
      #trigger {
        display: none; align-items: center; justify-content: center;
        width: 120px; max-width: calc(100vw - 16px); min-height: 0;
        padding: 0; border: 0; border-radius: 0; background: transparent;
        box-shadow: none; cursor: pointer; pointer-events: auto;
      }
      #trigger .button-art { display: block; width: 100%; height: auto; }
      #trigger:hover { filter: brightness(1.04); }
      #trigger:focus-visible { outline: 2px solid #76d7ff; outline-offset: 2px; }
      #trigger:disabled { opacity: .65; cursor: wait; }
      #menu {
        display: none; width: 252px; max-height: min(360px, calc(100vh - 64px));
        box-sizing: border-box; overflow: hidden; border: 1px solid rgba(255,255,255,.18);
        border-radius: 10px; background: #101923; color: #f8fbff;
        box-shadow: 0 8px 28px rgba(0,0,0,.5); pointer-events: auto;
      }
      #widget.open #menu { display: block; }
      #widget.visible #trigger { display: inline-flex; }
      #menu-title { padding: 10px 12px 8px; color: #b9d9f6; font-weight: 650; }
      #status { padding: 2px 12px 10px; color: #c4cfdd; }
      #choices { max-height: 270px; overflow-y: auto; padding: 0 6px 6px; }
      .choice {
        display: flex; width: 100%; align-items: center; justify-content: space-between;
        gap: 12px; box-sizing: border-box; padding: 9px 8px; border: 0;
        border-radius: 6px; background: transparent; color: #fff; text-align: left;
        font: 500 13px system-ui,sans-serif; cursor: pointer;
      }
      .choice:hover, .choice:focus-visible { background: #23476a; outline: none; }
      .choice:disabled { opacity: .55; cursor: wait; }
      .detail { color: #b6c3d1; font-size: 11px; white-space: nowrap; }
      #widget.open-up { flex-direction: column-reverse; }
      #widget.open-up #menu { margin-bottom: 7px; }
      #widget[hidden] { display: none; }
    `;
    const widget = document.createElement("div");
    widget.id = "widget";
    widget.setAttribute("hidden", "");

    const trigger = document.createElement("button");
    trigger.id = "trigger";
    trigger.type = "button";
    trigger.title = "Elegir resolución y descargar con XDM";
    trigger.setAttribute("aria-label", "XDM Download: elegir resolución y descargar");
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    const buttonArt = document.createElement("img");
    buttonArt.className = "button-art";
    const runtime = extensionRuntime();
    if (typeof runtime?.getURL === "function") {
      buttonArt.src = runtime.getURL("icons/xdm-download-button.png");
    } else {
      buttonArt.style.display = "none";
      const fallbackArt = document.createElement("span");
      fallbackArt.textContent = "XDM";
      fallbackArt.style.cssText = [
        "padding:8px 18px",
        "border-radius:999px",
        "background:linear-gradient(110deg,#368de0,#8748db)",
        "color:white",
        "font:700 14px system-ui,sans-serif",
      ].join(";");
      trigger.append(fallbackArt);
    }
    buttonArt.alt = "";
    buttonArt.setAttribute("aria-hidden", "true");
    trigger.append(buttonArt);

    const menu = document.createElement("div");
    menu.id = "menu";
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Resoluciones disponibles");
    const title = document.createElement("div");
    title.id = "menu-title";
    title.textContent = "Descargar con XDM";
    const status = document.createElement("div");
    status.id = "status";
    status.setAttribute("role", "status");
    status.textContent = "Pulsa el botón para buscar resoluciones.";
    const choices = document.createElement("div");
    choices.id = "choices";
    choices.setAttribute("role", "list");
    menu.append(title, status, choices);
    widget.append(trigger, menu);
    shadow.append(style, widget);
    document.documentElement.append(host);

    ui = { host, shadow, widget, trigger, menu, title, status, choices, session: null, pageUrl: null };
    trigger.addEventListener("click", event => {
      consumeEvent(event);
      if (widget.classList.contains("open") && ui.session) {
        closeMenu();
        return;
      }
      void loadFormats();
    }, true);
    widget.addEventListener("pointerenter", cancelHide);
    widget.addEventListener("pointerleave", scheduleHide);
    for (const eventName of ["pointerdown", "click", "dblclick"]) {
      // Stop player/page handlers after the control's own target listener has
      // run. Capturing here prevents the trigger and resolution buttons from
      // ever receiving their click events.
      widget.addEventListener(eventName, event => event.stopPropagation());
    }
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && widget.classList.contains("open")) closeMenu();
    }, true);
    window.addEventListener("resize", positionUi, { passive: true });
    window.addEventListener("scroll", positionUi, { passive: true, capture: true });
    return ui;
  }

  function cancelHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = 0;
  }

  function scheduleHide() {
    cancelHide();
    hideTimer = setTimeout(() => {
      hideTimer = 0;
      // Keep the picker visible while it is loading or showing choices. The
      // pointer leaves the video when it moves onto this floating panel.
      if (ui && ui.widget.classList.contains("open")) return;
      if (ui && !ui.widget.matches(":hover")) {
        ui.widget.classList.remove("visible");
        ui.widget.setAttribute("hidden", "");
        activeVideo = null;
      }
    }, 1_200);
  }

  function showFor(video) {
    if (isPlaylistListingPage(location.href)) {
      hideVideoPicker();
      return;
    }
    const controls = createUi();
    if (!controls) return;
    const target = videoTargetFor(video);
    const targetUrl = target?.pageUrl || "";
    const targetTitle = target?.title || "";
    cancelHide();
    if (activeVideo !== video || activePageUrl !== targetUrl
        || activeIsPreview !== Boolean(target?.isPreview)) {
      controls.widget.classList.remove("open");
      controls.trigger.setAttribute("aria-expanded", "false");
      controls.session = null;
      controls.pageUrl = null;
      controls.choices.replaceChildren();
      controls.status.textContent = targetUrl
        ? "Pulsa el botón para buscar resoluciones."
        : "No se pudo identificar el video de esta vista previa. Abre el video y vuelve a intentarlo.";
    }
    activeVideo = video;
    activePageUrl = targetUrl;
    activeTitle = targetTitle;
    activeIsPreview = Boolean(target?.isPreview);
    // Begin while the user is hovering the video, before opening the picker.
    // Playback events also warm the cache when the pointer is elsewhere.
    prefetchFormats(targetUrl, targetTitle, activeIsPreview);
    controls.widget.removeAttribute("hidden");
    controls.widget.classList.add("visible");
    positionUi();
  }

  function positionUi() {
    if (!ui || !activeVideo || !activeVideo.isConnected) return;
    const rect = activeVideo.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 30) return;
    const width = 252;
    const left = Math.min(Math.max(8, rect.right - width - 10), Math.max(8, innerWidth - width - 8));
    const triggerHeight = ui.trigger.getBoundingClientRect().height || 60;
    const top = Math.min(
      Math.max(8, rect.top + 10),
      Math.max(8, innerHeight - triggerHeight - 8),
    );
    ui.widget.style.left = `${left}px`;
    ui.widget.style.top = `${top}px`;
    const widgetHeight = ui.widget.getBoundingClientRect().height || triggerHeight;
    ui.widget.classList.toggle("open-up", top + widgetHeight > innerHeight - 8);
  }

  function openMenu() {
    if (!ui) return;
    cancelMenuAutoClose();
    cancelHide();
    ui.widget.removeAttribute("hidden");
    ui.widget.classList.add("visible");
    ui.widget.classList.add("open");
    ui.trigger.setAttribute("aria-expanded", "true");
    positionUi();
  }

  function closeMenu() {
    if (!ui) return;
    cancelMenuAutoClose();
    ui.widget.classList.remove("open");
    ui.trigger.setAttribute("aria-expanded", "false");
    scheduleHide();
  }

  function cancelMenuAutoClose() {
    if (menuAutoCloseTimer) clearTimeout(menuAutoCloseTimer);
    menuAutoCloseTimer = 0;
  }

  function scheduleMenuAutoClose() {
    cancelMenuAutoClose();
    if (!ui || !ui.widget.classList.contains("open")) return;
    menuAutoCloseTimer = setTimeout(() => {
      menuAutoCloseTimer = 0;
      if (ui && ui.widget.classList.contains("open")) closeMenu();
    }, MENU_AUTO_CLOSE_MS);
  }

  async function loadFormats() {
    if (!ui || !activeVideo) return;
    const pageUrl = activePageUrl;
    const pageTitle = activeTitle || document.title;
    const isPreview = activeIsPreview;
    if (!pageUrl) {
      openMenu();
      ui.status.textContent = "No se pudo identificar el enlace de esta vista previa. Abre el video y vuelve a intentarlo.";
      return;
    }
    const generation = pageGeneration;
    ui.session = null;
    ui.pageUrl = pageUrl;
    ui.choices.replaceChildren();
    ui.status.textContent = "Buscando resoluciones compatibles…";
    ui.trigger.disabled = true;
    cancelMenuAutoClose();
    openMenu();
    try {
      const response = await getFormats(pageUrl, { retryEmpty: true, title: pageTitle, isPreview });
      if (generation !== pageGeneration || pageUrl !== activePageUrl || isPreview !== activeIsPreview) return;
      if (!response || !response.ok) throw new Error(response && response.message || "No se pudieron obtener las resoluciones.");
      const formats = Array.isArray(response.formats) ? response.formats : [];
      if (!formats.length) {
        ui.status.textContent = response.message || "No se encontraron formatos completos compatibles con XDM.";
        return;
      }
      ui.session = response.session;
      ui.pageTitle = pageTitle;
      const activeAudio = response.audio_track;
      const audioLabel = activeAudio && (activeAudio.name || activeAudio.language);
      const statusParts = [];
      if (audioLabel) statusParts.push(`Pista original detectada: ${audioLabel}`);
      statusParts.push(response.notice || "Elige una resolución:");
      ui.status.textContent = statusParts.join(" · ");
      for (const format of formats) addFormatChoice(format);
    } catch (error) {
      if (generation !== pageGeneration || pageUrl !== activePageUrl || isPreview !== activeIsPreview) return;
      ui.status.textContent = error.message || "XDM Companion no está disponible.";
    } finally {
      if (generation === pageGeneration && pageUrl === activePageUrl && isPreview === activeIsPreview) {
        ui.trigger.disabled = false;
        positionUi();
        scheduleMenuAutoClose();
      }
    }
  }

  function addFormatChoice(format) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice";
    const label = document.createElement("span");
    label.textContent = format.label || `${format.height}p`;
    const details = document.createElement("span");
    details.className = "detail";
    details.textContent = [format.audio_language, format.ext && format.ext.toUpperCase(), format.fps && `${format.fps} fps`, format.filesize].filter(Boolean).join(" · ");
    button.append(label, details);
    button.addEventListener("click", event => {
      consumeEvent(event);
      void selectFormat(format, button);
    }, true);
    ui.choices.append(button);
  }

  async function selectFormat(format, selectedButton) {
    if (!ui || !ui.session) return;
    const pageUrl = ui.pageUrl;
    const session = ui.session;
    invalidateFormats(pageUrl);
    for (const button of ui.choices.querySelectorAll("button")) button.disabled = true;
    ui.status.textContent = `Enviando ${format.label || "el video"} a XDM…`;
    ui.session = null;
    ui.trigger.disabled = true;
    ui.trigger.title = `Enviando ${format.label || "el video"} a XDM…`;
    closeMenu();
    try {
      const response = await sendBridgeMessage({
        type: "xdm-companion:select-format",
        pageUrl,
        title: ui.pageTitle || activeTitle || document.title,
        session,
        formatId: format.format_id,
      });
      if (!response || !response.ok) throw new Error(response && response.message || "XDM no aceptó este formato.");
      ui.status.textContent = `${format.label || "El video"} enviado a XDM.`;
      ui.trigger.title = `${format.label || "El video"} enviado a XDM.`;
      setTimeout(() => {
        if (ui && !ui.widget.matches(":hover")) {
          ui.widget.classList.remove("visible", "open");
          ui.widget.setAttribute("hidden", "");
          activeVideo = null;
        }
      }, 2_000);
    } catch (error) {
      ui.choices.replaceChildren();
      ui.status.textContent = `${error.message || "No se pudo enviar el video a XDM."} Pulsa XDM para volver a cargar las resoluciones.`;
      ui.trigger.title = "Elegir resolución y descargar con XDM";
      openMenu();
      scheduleMenuAutoClose();
      selectedButton.blur();
    } finally {
      ui.trigger.disabled = false;
    }
  }

  function observeVideo(video) {
    if (!(video instanceof HTMLVideoElement) || observedVideos.has(video)) return;
    observedVideos.add(video);
    video.addEventListener("playing", () => {
      syncPageContext();
      const rect = video.getBoundingClientRect();
      if (rect.width >= 240 && rect.height >= 135) prefetchVideo(video);
    }, { passive: true });
    video.addEventListener("loadedmetadata", () => {
      syncPageContext();
      const rect = video.getBoundingClientRect();
      if (rect.width >= 240 && rect.height >= 135) prefetchVideo(video);
    }, { passive: true });
    video.addEventListener("pointerenter", () => showFor(video), { passive: true });
    video.addEventListener("pointermove", () => {
      if (activeVideo === video) positionUi();
    }, { passive: true });
    video.addEventListener("pointerleave", scheduleHide, { passive: true });

    // Site controls often sit above the video element. Listen on its immediate
    // player wrapper too, so moving from the picture to those controls keeps the
    // XDM affordance available without tracking the whole page.
    const wrapper = video.parentElement;
    if (wrapper && wrapper !== document.body && wrapper !== document.documentElement) {
      wrapper.addEventListener("pointerenter", () => showFor(video), { passive: true });
      wrapper.addEventListener("pointermove", event => {
        const rect = video.getBoundingClientRect();
        if (event.clientX >= rect.left && event.clientX <= rect.right
            && event.clientY >= rect.top && event.clientY <= rect.bottom) {
          showFor(video);
        }
      }, { passive: true });
      wrapper.addEventListener("pointerleave", scheduleHide, { passive: true });
    }

    // The content script can be injected after playback already started.
    if (!video.paused && !video.ended) {
      const rect = video.getBoundingClientRect();
      if (rect.width >= 240 && rect.height >= 135) prefetchVideo(video);
    }
  }

  function scan(root) {
    if (!root) return;
    if (ui && typeof root.getRootNode === "function" && root.getRootNode() === ui.shadow) return;
    observeVideo(root);
    if (root.shadowRoot) observeRoot(root.shadowRoot);
    if (typeof root.querySelectorAll !== "function") return;
    for (const element of root.querySelectorAll("video, *")) {
      observeVideo(element);
      if (element.shadowRoot) observeRoot(element.shadowRoot);
    }
  }

  function observeRoot(root) {
    if (!root || observedRoots.has(root)) return;
    observedRoots.add(root);
    observer.observe(root, { childList: true, subtree: true });
    scan(root);
  }

  const observer = new MutationObserver(records => {
    syncPageContext();
    for (const record of records) {
      for (const node of record.addedNodes) scan(node);
    }
  });

  function handlePageEvent(event) {
    const changed = syncPageContext();
    if (changed || !pageDataEventsFor(location.href).includes(event.type)) return;

    const pageUrl = location.href;
    const cacheKey = formatCacheKey(pageUrl, false);
    const cached = cachedFormats.get(cacheKey);
    if ((cached && cached.expiresAt > Date.now()) || pendingFormats.has(cacheKey)
        || playerDataRetries.has(pageUrl)) return;
    const provider = providerForUrl(pageUrl);
    if (provider && !hasVideoId(pageUrl, provider)) return;

    // Some sites publish player data just after navigation. Retry an early
    // empty/failing prefetch once, without creating a request storm.
    playerDataRetries.add(pageUrl);
    recentFormatFailures.delete(cacheKey);
    prefetchFormats(pageUrl, document.title, false);
  }

  function pageDataEventsFor(rawUrl) {
    try {
      const url = new URL(rawUrl);
      const host = url.hostname.toLowerCase().replace(/\.$/, "");
      const providers = globalThis.XDM_SITE_CATALOG?.providers || [];
      const provider = providers.find(item =>
        (item.domains || []).some(domain => host === domain || host.endsWith(`.${domain}`)),
      );
      const events = provider?.browser?.page_events;
      return Array.isArray(events) ? events.filter(value => typeof value === "string") : [];
    } catch (_) {
      return [];
    }
  }

  const navigationEvents = pageDataEventsFor(location.href);
  for (const eventName of [...new Set([...navigationEvents, "popstate", "hashchange"])]) {
    window.addEventListener(eventName, handlePageEvent, { passive: true });
  }

  syncPlaylistControl();
  observeRoot(document);
})();
