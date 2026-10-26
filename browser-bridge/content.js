"use strict";

(() => {
  const observedVideos = new WeakSet();
  const observedRoots = new WeakSet();
  const FORMAT_CACHE_TTL_MS = 4 * 60 * 1000;
  const PREFETCH_RETRY_COOLDOWN_MS = 8 * 1000;
  const FORMAT_CACHE_LIMIT = 4;
  const MENU_AUTO_CLOSE_MS = 5 * 1000;
  let activeVideo = null;
  let hideTimer = 0;
  let menuAutoCloseTimer = 0;
  let ui = null;
  let cachedFormats = new Map();
  let pendingFormats = new Map();
  let recentFormatFailures = new Map();
  const playerDataRetries = new Set();
  let observedPageUrl = location.href;
  let pageGeneration = 0;

  function sendBridgeMessage(message) {
    try {
      const response = chrome.runtime.sendMessage(message);
      if (response && typeof response.then === "function") return response;
      return Promise.reject(new Error("El navegador no pudo contactar el puente de XDM."));
    } catch (error) {
      return Promise.reject(error);
    }
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
    cachedFormats.delete(pageUrl);
    recentFormatFailures.delete(pageUrl);
  }

  function remember(map, pageUrl, value) {
    map.delete(pageUrl);
    map.set(pageUrl, value);
    while (map.size > FORMAT_CACHE_LIMIT) map.delete(map.keys().next().value);
  }

  function requestFormats(pageUrl) {
    const entry = { pageUrl, promise: null };
    entry.promise = sendBridgeMessage({
      type: "xdm-companion:get-formats",
      pageUrl,
      title: document.title,
    }).then(response => {
      if (hasUsableFormats(response)) {
        remember(cachedFormats, pageUrl, { response, expiresAt: Date.now() + FORMAT_CACHE_TTL_MS });
        recentFormatFailures.delete(pageUrl);
      } else {
        cachedFormats.delete(pageUrl);
        remember(recentFormatFailures, pageUrl, { response, failedAt: Date.now() });
      }
      return response;
    }).catch(error => {
      cachedFormats.delete(pageUrl);
      remember(recentFormatFailures, pageUrl, { error, failedAt: Date.now() });
      throw error;
    }).finally(() => {
      if (pendingFormats.get(pageUrl) === entry) pendingFormats.delete(pageUrl);
    });
    pendingFormats.set(pageUrl, entry);
    return entry.promise;
  }

  async function getFormats(pageUrl, { retryEmpty = false } = {}) {
    const now = Date.now();
    const cached = cachedFormats.get(pageUrl);
    if (cached && cached.expiresAt > now) {
      // Refresh insertion order so the small cache behaves as an LRU.
      remember(cachedFormats, pageUrl, cached);
      return cached.response;
    }
    if (cached) cachedFormats.delete(pageUrl);

    const pending = pendingFormats.get(pageUrl);
    if (pending) {
      const response = await pending.promise;
      if (hasUsableFormats(response) || !retryEmpty) return response;
      // A click is an explicit request. If prefetch ran before player data was
      // ready, retry once now.
      return requestFormats(pageUrl);
    }

    const failure = recentFormatFailures.get(pageUrl);
    if (
      !retryEmpty && failure
      && now - failure.failedAt < PREFETCH_RETRY_COOLDOWN_MS
    ) {
      if (failure.error) throw failure.error;
      return failure.response;
    }
    recentFormatFailures.delete(pageUrl);
    return requestFormats(pageUrl);
  }

  function prefetchFormats(pageUrl = location.href) {
    if (pageUrl !== location.href) return;
    void getFormats(pageUrl).catch(() => {});
  }

  function syncPageContext() {
    const pageUrl = location.href;
    if (pageUrl === observedPageUrl) return false;
    observedPageUrl = pageUrl;
    pageGeneration += 1;
    playerDataRetries.clear();

    const menuWasOpen = Boolean(ui && ui.widget.classList.contains("open"));
    if (ui) {
      ui.session = null;
      ui.pageUrl = pageUrl;
      ui.choices.replaceChildren();
      ui.status.textContent = "Preparando resoluciones…";
      ui.trigger.disabled = false;
    }

    if (menuWasOpen && activeVideo) {
      // Keep an already-open picker useful when the site changes videos.
      void loadFormats();
    } else {
      prefetchFormats(pageUrl);
    }
    return true;
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
        display: none; align-items: center; gap: 7px; min-height: 34px;
        padding: 0 12px; border: 1px solid rgba(255,255,255,.28);
        border-radius: 18px; background: #102b50; color: #fff;
        box-shadow: 0 2px 12px rgba(0,0,0,.38); font: 600 13px system-ui,sans-serif;
        cursor: pointer; pointer-events: auto;
      }
      #trigger:hover, #trigger:focus-visible { background: #17477b; outline: 2px solid #76d7ff; }
      #trigger:disabled { opacity: .65; cursor: wait; }
      #trigger .arrow { color: #68e3d3; font-size: 16px; line-height: 1; }
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
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "↓";
    trigger.append(arrow, document.createTextNode("XDM"));

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
    const controls = createUi();
    if (!controls) return;
    cancelHide();
    if (activeVideo !== video) {
      controls.widget.classList.remove("open");
      controls.trigger.setAttribute("aria-expanded", "false");
      controls.session = null;
      controls.pageUrl = null;
      controls.choices.replaceChildren();
      controls.status.textContent = "Pulsa el botón para buscar resoluciones.";
    }
    activeVideo = video;
    // Begin while the user is hovering the video, before opening the picker.
    // Playback events also warm the cache when the pointer is elsewhere.
    prefetchFormats();
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
    const top = Math.min(Math.max(8, rect.top + 10), Math.max(8, innerHeight - 46));
    ui.widget.style.left = `${left}px`;
    ui.widget.style.top = `${top}px`;
    ui.widget.classList.toggle("open-up", top + 390 > innerHeight);
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
    const pageUrl = location.href;
    const generation = pageGeneration;
    ui.session = null;
    ui.pageUrl = pageUrl;
    ui.choices.replaceChildren();
    ui.status.textContent = "Buscando resoluciones compatibles…";
    ui.trigger.disabled = true;
    cancelMenuAutoClose();
    openMenu();
    try {
      const response = await getFormats(pageUrl, { retryEmpty: true });
      if (generation !== pageGeneration || pageUrl !== location.href) return;
      if (!response || !response.ok) throw new Error(response && response.message || "No se pudieron obtener las resoluciones.");
      const formats = Array.isArray(response.formats) ? response.formats : [];
      if (!formats.length) {
        ui.status.textContent = response.message || "No se encontraron formatos completos compatibles con XDM.";
        return;
      }
      ui.session = response.session;
      const activeAudio = response.audio_track;
      const audioLabel = activeAudio && (activeAudio.name || activeAudio.language);
      const statusParts = [];
      if (audioLabel) statusParts.push(`Pista original detectada: ${audioLabel}`);
      statusParts.push(response.notice || "Elige una resolución:");
      ui.status.textContent = statusParts.join(" · ");
      for (const format of formats) addFormatChoice(format);
    } catch (error) {
      if (generation !== pageGeneration || pageUrl !== location.href) return;
      ui.status.textContent = error.message || "XDM Companion no está disponible.";
    } finally {
      if (generation === pageGeneration && pageUrl === location.href) {
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
        title: document.title,
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
      if (rect.width >= 240 && rect.height >= 135) prefetchFormats();
    }, { passive: true });
    video.addEventListener("loadedmetadata", () => {
      syncPageContext();
      const rect = video.getBoundingClientRect();
      if (rect.width >= 240 && rect.height >= 135) prefetchFormats();
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
      if (rect.width >= 240 && rect.height >= 135) prefetchFormats();
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
    const cached = cachedFormats.get(pageUrl);
    if ((cached && cached.expiresAt > Date.now()) || pendingFormats.has(pageUrl)
        || playerDataRetries.has(pageUrl)) return;

    // Some sites publish player data just after navigation. Retry an early
    // empty/failing prefetch once, without creating a request storm.
    playerDataRetries.add(pageUrl);
    recentFormatFailures.delete(pageUrl);
    prefetchFormats(pageUrl);
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

  observeRoot(document);
})();
