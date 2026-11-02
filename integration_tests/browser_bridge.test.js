const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const bridgeDir = path.join(__dirname, "..", "browser-bridge");
const siteCatalog = JSON.parse(fs.readFileSync(path.join(__dirname, "../core/domains.json"), "utf8"));

function loadBackground(context) {
  context.XDM_SITE_CATALOG = siteCatalog;
  context.XDM_SITE_ADAPTERS = {};
  context.importScripts = relativePath => {
    const script = path.join(bridgeDir, relativePath.replace(/^\.\//, ""));
    vm.runInNewContext(fs.readFileSync(script, "utf8"), context);
  };
  vm.runInNewContext(fs.readFileSync(path.join(bridgeDir, "background.js"), "utf8"), context);
}

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(...values) { for (const value of values) this.values.delete(value); }
  contains(value) { return this.values.has(value); }
  toggle(value, force) {
    const shouldAdd = force === undefined ? !this.values.has(value) : force;
    if (shouldAdd) this.values.add(value);
    else this.values.delete(value);
    return shouldAdd;
  }
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.style = {};
    this.classList = new FakeClassList();
    this.isConnected = true;
    this.disabled = false;
    this.textContent = "";
    this.parentElement = null;
    this.shadowRoot = null;
    this.hover = false;
  }
  append(...items) {
    for (const item of items) {
      this.children.push(item);
      if (item instanceof FakeElement) item.parentElement = this;
    }
  }
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  getBoundingClientRect() {
    return { left: 0, right: 252, top: 0, bottom: 38, width: 252, height: 38 };
  }
  addEventListener(name, callback, options = false) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    const capture = typeof options === "boolean" ? options : Boolean(options && options.capture);
    this.listeners.get(name).push({ callback, capture });
  }
  dispatch(name, props = {}) {
    const event = {
      type: name,
      key: props.key,
      bubbles: props.bubbles !== false,
      target: this,
      currentTarget: null,
      defaultPrevented: false,
      propagationStopped: false,
      immediatePropagationStopped: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
      stopImmediatePropagation() {
        this.propagationStopped = true;
        this.immediatePropagationStopped = true;
      },
    };
    const path = [];
    for (let current = this; current; current = current.parentElement) path.push(current);
    const invoke = (element, capture) => {
      event.currentTarget = element;
      event.immediatePropagationStopped = false;
      for (const listener of element.listeners.get(name) || []) {
        if (listener.capture !== capture) continue;
        listener.callback(event);
        if (event.immediatePropagationStopped) break;
      }
    };
    for (let index = path.length - 1; index > 0; index--) {
      invoke(path[index], true);
      if (event.propagationStopped) return event;
    }
    invoke(this, true);
    if (!event.immediatePropagationStopped) invoke(this, false);
    if (event.bubbles && !event.propagationStopped) {
      for (let index = 1; index < path.length; index++) {
        invoke(path[index], false);
        if (event.propagationStopped) break;
      }
    }
    return event;
  }
  attachShadow() {
    this.shadowRoot = new FakeElement("#shadow-root");
    this.shadowRoot.host = this;
    return this.shadowRoot;
  }
  getRootNode() {
    let current = this;
    while (current.parentElement) current = current.parentElement;
    return current;
  }
  matches(selector) { return selector === ":hover" && this.hover; }
  querySelectorAll(selector) {
    const matches = [];
    const visit = node => {
      for (const child of node.children || []) {
        if (!(child instanceof FakeElement)) continue;
        if (selector === "button" && child.tagName === "BUTTON") matches.push(child);
        if (selector === "video, *") matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
  getElementById(id) {
    if (this.id === id) return this;
    for (const child of this.children) {
      if (!(child instanceof FakeElement)) continue;
      const found = child.getElementById(id);
      if (found) return found;
    }
    return null;
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class FakeVideo extends FakeElement {
  constructor() {
    super("video");
    this.paused = true;
    this.ended = false;
  }
  getBoundingClientRect() {
    return { left: 100, right: 740, top: 100, width: 640, height: 360 };
  }
}

class FakeDocument {
  constructor() {
    this.title = "Video title";
    this.documentElement = new FakeElement("html");
    this.listeners = new Map();
  }
  createElement(tagName) { return new FakeElement(tagName); }
  createTextNode(text) { return { textContent: text }; }
  querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
}

function loadContentScript({
  onMessage = async () => ({ ok: true }),
  videos = [],
  api = "chrome",
  pageUrl = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
} = {}) {
  const observers = [];
  const timers = new Map();
  const windowListeners = new Map();
  let nextTimerId = 0;
  class MutationObserver {
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }
    observe() {}
  }
  const document = new FakeDocument();
  const location = { href: pageUrl };
  const browserWindow = {
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener(name, callback) {
      if (!windowListeners.has(name)) windowListeners.set(name, []);
      windowListeners.get(name).push(callback);
    },
    dispatch(name) {
      for (const callback of windowListeners.get(name) || []) callback({ type: name });
    },
  };
  document.documentElement.append(...videos);
  const context = {
    HTMLVideoElement: FakeVideo,
    MutationObserver,
    URL,
    Date,
    location,
    document,
    innerWidth: 1280,
    innerHeight: 800,
    setTimeout: (callback, delay) => {
      const timerId = ++nextTimerId;
      timers.set(timerId, { callback, delay });
      return timerId;
    },
    clearTimeout: timerId => timers.delete(timerId),
    window: browserWindow,
    XDM_SITE_CATALOG: siteCatalog,
  };
  const runtime = {
    sendMessage: onMessage,
    getURL: resource => `extension://test-extension/${resource}`,
  };
  if (api === "chrome") context.chrome = { runtime };
  if (api === "browser") context.browser = { runtime };
  vm.runInNewContext(fs.readFileSync(path.join(bridgeDir, "content.js"), "utf8"), context);
  return {
    document,
    location,
    dispatchWindowEvent: name => browserWindow.dispatch(name),
    windowEventNames: () => [...windowListeners.keys()],
    observers,
    runTimers() {
      const pending = [...timers.values()];
      timers.clear();
      for (const timer of pending) timer.callback();
    },
    timerDelays() { return [...timers.values()].map(timer => timer.delay); },
  };
}

function tick() {
  return new Promise(resolve => setImmediate(resolve));
}

test("the floating XDM button loads choices and sends the selected resolution", async () => {
  const messages = [];
  const video = new FakeVideo();
  const { document } = loadContentScript({
    videos: [video],
    onMessage: async message => {
      messages.push(message);
      if (message.type === "xdm-companion:get-formats") {
        return {
          ok: true,
          session: "a".repeat(32),
          audio_track: { id: "es.2", language: "es", name: "Español" },
          notice: "Se muestran las calidades HD disponibles.",
          formats: [
            { format_id: "720", height: 720, label: "720p", ext: "mp4", fps: 30, filesize: "~500 MB" },
            { format_id: "480", height: 480, label: "480p", ext: "mp4", fps: 30, filesize: "~250 MB" },
          ],
        };
      }
      return { ok: true, status: "queued" };
    },
  });

  video.paused = false;
  video.dispatch("playing");
  await tick();
  assert.equal(messages.length, 1, "playback starts format lookup before the floating button is clicked");
  assert.equal(messages[0].type, "xdm-companion:get-formats");

  video.dispatch("pointerenter");
  const host = document.documentElement.children.find(element => element.shadowRoot);
  assert.ok(host, "the overlay is mounted over the page");
  const shadow = host.shadowRoot;
  const trigger = shadow.getElementById("trigger");
  assert.ok(trigger);
  assert.equal(
    trigger.children[0].src,
    "extension://test-extension/icons/xdm-download-button.png",
  );
  assert.equal(messages.length, 1, "prefetching formats does not start a download");

  trigger.dispatch("click");
  await tick();
  assert.equal(messages[0].type, "xdm-companion:get-formats");
  assert.equal(messages.length, 1, "the click reuses the already prepared format response");
  assert.equal(shadow.getElementById("choices").children.length, 2);
  assert.equal(
    shadow.getElementById("status").textContent,
    "Pista original detectada: Español · Se muestran las calidades HD disponibles.",
  );

  shadow.getElementById("choices").children[0].dispatch("click");
  assert.equal(
    shadow.getElementById("widget").classList.contains("open"),
    false,
    "selecting a resolution closes the menu before waiting for XDM",
  );
  await tick();
  assert.equal(messages[1].type, "xdm-companion:select-format");
  assert.equal(messages[1].formatId, "720");
  assert.equal(messages[1].session, "a".repeat(32));
  assert.match(shadow.getElementById("status").textContent, /enviado a XDM/);
});

test("the floating button uses the browser Promise API when Chrome namespace is absent", async () => {
  const video = new FakeVideo();
  const { document } = loadContentScript({
    api: "browser",
    videos: [video],
    onMessage: async message => message.type === "xdm-companion:get-formats"
      ? {
          ok: true,
          session: "b".repeat(32),
          formats: [{ format_id: "1440", height: 1440, label: "1440p" }],
        }
      : { ok: true },
  });

  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();

  assert.equal(shadow.getElementById("choices").children.length, 1);
  assert.equal(shadow.getElementById("choices").children[0].children[0].textContent, "1440p");
});

test("the floating button supports callback-based Chrome messaging", async () => {
  const messages = [];
  const video = new FakeVideo();
  const { document } = loadContentScript({
    videos: [video],
    onMessage(message, callback) {
      messages.push(message);
      callback({
        ok: true,
        session: "c".repeat(32),
        formats: [{ format_id: "1080", height: 1080, label: "1080p" }],
      });
    },
  });

  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();

  assert.equal(messages.length, 1);
  assert.equal(shadow.getElementById("choices").children.length, 1);
  assert.equal(shadow.getElementById("status").textContent, "Elige una resolución:");
});

test("a missing extension runtime shows a reload hint instead of a sendMessage TypeError", async () => {
  const video = new FakeVideo();
  const { document } = loadContentScript({ api: "none", videos: [video] });

  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  const trigger = shadow.getElementById("trigger");
  assert.equal(trigger.children[0].textContent, "XDM", "the button keeps a visible fallback mark");
  trigger.dispatch("click");
  await tick();

  const status = shadow.getElementById("status").textContent;
  assert.match(status, /Recarga esta página/);
  assert.doesNotMatch(status, /Cannot read properties of undefined/);
});

test("a thumbnail preview requests formats for that card's video URL", async () => {
  const messages = [];
  const video = new FakeVideo();
  const card = new FakeElement("ytd-video-renderer");
  const link = new FakeElement("a");
  link.href = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  link.setAttribute("title", "Preview video title");
  card.matches = selector => selector === "ytd-video-renderer";
  card.querySelectorAll = selector => selector.startsWith("a#") ? [link] : [];
  card.append(video, link);
  const { document } = loadContentScript({
    pageUrl: "https://www.youtube.com/results?search_query=preview",
    videos: [card],
    onMessage: async message => {
      messages.push(message);
      return message.type === "xdm-companion:get-formats"
        ? {
            ok: true,
            session: "p".repeat(32),
            formats: [{ format_id: "preview-1080", height: 1080, label: "1080p" }],
          }
        : { ok: true };
    },
  });

  video.dispatch("pointerenter");
  await tick();
  assert.equal(messages[0].pageUrl, link.href);
  assert.equal(messages[0].title, "Preview video title");

  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();
  assert.equal(shadow.getElementById("choices").children[0].children[0].textContent, "1080p");
});

test("the missing background receiver is shown as a clear extension reload instruction", async () => {
  const video = new FakeVideo();
  const { document } = loadContentScript({
    api: "browser",
    videos: [video],
    onMessage: async () => {
      throw new Error("Could not establish connection. Receiving end does not exist.");
    },
  });

  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();

  const status = shadow.getElementById("status").textContent;
  assert.match(status, /La extensión XDM Companion no respondió/);
  assert.match(status, /Recarga la extensión/);
  assert.doesNotMatch(status, /Receiving end does not exist/);
});

test("the format menu closes after five seconds without a selection", async () => {
  const video = new FakeVideo();
  const { document, runTimers, timerDelays } = loadContentScript({
    videos: [video],
    onMessage: async message => message.type === "xdm-companion:get-formats"
      ? {
          ok: true,
          session: "e".repeat(32),
          formats: [{ format_id: "720", height: 720, label: "720p" }],
        }
      : { ok: true },
  });

  video.dispatch("pointerenter");
  await tick();
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();

  const widget = shadow.getElementById("widget");
  const trigger = shadow.getElementById("trigger");
  assert.equal(widget.classList.contains("open"), true);
  assert.ok(timerDelays().includes(5_000), "the menu gets a five-second selection window");
  widget.dispatch("pointermove");
  assert.ok(timerDelays().includes(5_000), "moving the pointer does not keep the menu open indefinitely");
  runTimers();
  assert.equal(widget.classList.contains("open"), false);

  trigger.dispatch("click");
  await tick();
  assert.equal(widget.classList.contains("open"), true, "the user can reopen the picker after it times out");
  assert.ok(timerDelays().includes(5_000), "reopening starts a fresh selection window");
});

test("changing videos preloads the new URL before the floating button is clicked", async () => {
  const requests = [];
  const video = new FakeVideo();
  const { document, location, dispatchWindowEvent, windowEventNames } = loadContentScript({
    videos: [video],
    onMessage: message => new Promise(resolve => requests.push({ message, resolve })),
  });
  assert.ok(windowEventNames().includes("yt-navigate-finish"), "provider events come from the catalog");

  video.paused = false;
  video.dispatch("playing");
  await tick();
  assert.equal(requests.length, 1, "playback preloads the first URL");

  location.href = "https://www.youtube.com/watch?v=a1b2c3d4e5F";
  dispatchWindowEvent("yt-navigate-finish");
  await tick();
  assert.equal(requests.length, 2, "navigation starts preloading before the user opens the picker");
  assert.equal(requests[1].message.pageUrl, location.href);

  requests[1].resolve({
    ok: true,
    session: "n".repeat(32),
    formats: [{ format_id: "new-1080", height: 1080, label: "1080p actual" }],
  });
  await tick();
  requests[0].resolve({
    ok: true,
    session: "o".repeat(32),
    formats: [{ format_id: "old-720", height: 720, label: "720p anterior" }],
  });
  await tick();

  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();
  assert.equal(requests.length, 2, "opening the picker reuses the new video's completed prefetch");
  const choices = shadow.getElementById("choices").children;
  assert.equal(choices.length, 1);
  assert.equal(choices[0].children[0].textContent, "1080p actual");
});

test("a late response from the previous video cannot replace an open new-video picker", async () => {
  const requests = [];
  const video = new FakeVideo();
  const { document, location, dispatchWindowEvent } = loadContentScript({
    videos: [video],
    onMessage: message => new Promise(resolve => requests.push({ message, resolve })),
  });

  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();
  assert.equal(requests.length, 1, "the first video has a single shared prefetch request");

  location.href = "https://www.youtube.com/watch?v=a1b2c3d4e5F";
  dispatchWindowEvent("yt-navigate-finish");
  await tick();
  assert.equal(requests.length, 2, "navigation starts fetching the new video without another click");
  assert.equal(requests[1].message.pageUrl, location.href);

  requests[1].resolve({
    ok: true,
    session: "n".repeat(32),
    formats: [{ format_id: "new-1080", height: 1080, label: "1080p actual" }],
  });
  await tick();
  requests[0].resolve({
    ok: true,
    session: "o".repeat(32),
    formats: [{ format_id: "old-720", height: 720, label: "720p anterior" }],
  });
  await tick();

  const choices = shadow.getElementById("choices").children;
  assert.equal(choices.length, 1);
  assert.equal(choices[0].children[0].textContent, "1080p actual");
  assert.equal(requests.length, 2, "opening the refreshed picker does not fetch the video again");
});

test("player-data readiness retries one early empty prefetch and warms the picker", async () => {
  const requests = [];
  const video = new FakeVideo();
  const { document, dispatchWindowEvent } = loadContentScript({
    videos: [video],
    onMessage: message => new Promise(resolve => requests.push({ message, resolve })),
  });

  video.paused = false;
  video.dispatch("playing");
  await tick();
  assert.equal(requests.length, 1);
  requests[0].resolve({ ok: true, session: "", formats: [] });
  await tick();

  dispatchWindowEvent("yt-page-data-updated");
  await tick();
  assert.equal(requests.length, 2, "player readiness retries the early empty response");
  dispatchWindowEvent("yt-page-data-updated");
  assert.equal(requests.length, 2, "repeated player events do not start duplicate requests");

  requests[1].resolve({
    ok: true,
    session: "r".repeat(32),
    formats: [{ format_id: "ready-1080", height: 1080, label: "1080p lista" }],
  });
  await tick();
  video.dispatch("pointerenter");
  const shadow = document.documentElement.children.find(element => element.shadowRoot).shadowRoot;
  shadow.getElementById("trigger").dispatch("click");
  await tick();
  assert.equal(requests.length, 2, "the picker opens from the warmed cache");
  assert.equal(shadow.getElementById("choices").children[0].children[0].textContent, "1080p lista");
});

test("the resolution picker stays visible while the video is left during lookup", async () => {
  let resolveFormats;
  const video = new FakeVideo();
  const { document, runTimers } = loadContentScript({
    videos: [video],
    onMessage: () => new Promise(resolve => { resolveFormats = resolve; }),
  });

  video.dispatch("pointerenter");
  const host = document.documentElement.children.find(element => element.shadowRoot);
  const shadow = host.shadowRoot;
  const widget = shadow.getElementById("widget");
  shadow.getElementById("trigger").dispatch("click");

  assert.equal(widget.classList.contains("open"), true, "clicking opens the menu immediately");
  assert.match(shadow.getElementById("status").textContent, /Buscando resoluciones/);
  video.dispatch("pointerleave");
  runTimers();

  assert.equal(widget.classList.contains("open"), true, "pointer exit cannot close an open picker");
  assert.equal(widget.attributes.has("hidden"), false, "the picker remains visible during lookup");
  resolveFormats({
    ok: true,
    session: "c".repeat(32),
    formats: [{ format_id: "720", height: 720, label: "720p" }],
  });
  await tick();

  assert.equal(shadow.getElementById("choices").children.length, 1);
  assert.match(shadow.getElementById("status").textContent, /Elige una resolución/);
});

test("the overlay detects dynamically inserted videos inside open shadow roots", () => {
  const { document, observers } = loadContentScript();
  const playerHost = new FakeElement("div");
  const playerShadow = playerHost.attachShadow();
  const video = new FakeVideo();
  playerShadow.append(video);
  document.documentElement.append(playerHost);

  observers[0].callback([{ addedNodes: [playerHost] }]);
  video.dispatch("pointerenter");

  const overlay = document.documentElement.children.find(
    element => element.shadowRoot && element.shadowRoot.getElementById("trigger"),
  );
  assert.ok(overlay, "the bridge observed the dynamically inserted player");
  assert.ok(overlay.shadowRoot.getElementById("trigger"));
});

test("background finds the original audio track from YouTube's default track index", async () => {
  let onMessage;
  const requests = [];
  const spanishTrack = {
    getLanguageInfo: () => ({ id: "es.2", name: "Español" }),
    audioIsDefault: false,
  };
  const englishTrack = {
    getLanguageInfo: () => ({ id: "en.1", name: "English" }),
  };
  const player = {
    getAudioTrack: () => spanishTrack,
    getAvailableAudioTracks: () => new Set([englishTrack, spanishTrack]),
    getPlayerResponse: () => ({
      videoDetails: { videoId: "dQw4w9WgXcQ", title: "Member video" },
      streamingData: {
        formats: [{
          itag: 18,
          url: "https://rr1---sn.googlevideo.com/videoplayback?expire=123",
          mimeType: 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
          width: 640,
          height: 360,
          audioTrack: { id: "en.1", displayName: "English" },
        }],
        adaptiveFormats: [
          ...Array.from({ length: 52 }, (_, index) => ({
            itag: 100 + index,
            url: `https://rr1---sn.googlevideo.com/videoplayback?itag=${100 + index}`,
            mimeType: 'video/mp4; codecs="avc1.42001E"',
            width: 640,
            height: 360,
            bitrate: 500000 + index,
          })),
          {
            itag: 999,
            url: "https://rr1---sn.googlevideo.com/videoplayback?itag=999",
            mimeType: 'video/mp4; codecs="avc1.640033"',
            width: 3840,
            height: 2160,
            bitrate: 20000000,
          },
          {
            itag: 1000,
            mimeType: 'video/mp4; codecs="avc1.640033"',
            width: 7680,
            height: 4320,
            bitrate: 40000000,
          },
        ],
      },
      captions: { playerCaptionsTracklistRenderer: {
        defaultAudioTrackIndex: 0,
        audioTracks: [
          { id: "en.1", languageCode: "en", displayName: "English" },
          { id: "es.2", languageCode: "es", displayName: "Español" },
        ],
      } },
    }),
  };
  const context = {
    URL,
    navigator: { userAgent: "Test browser", languages: ["es-DO", "es"] },
    document: {
      querySelector: selector => selector === "#movie_player" ? player : null,
      getElementById: id => id === "movie_player" ? player : null,
    },
    window: { ytInitialPlayerResponse: null },
    console: { warn: () => {} },
    chrome: {
      cookies: {
        getAll: async query => {
          if (query.url === "https://www.youtube.com/") {
            return [];
          }
          if (query.url === "https://youtube.com/") {
            return [];
          }
          if (query.url) {
            assert.equal(query.url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ");
            return [{ name: "SID", value: "session", domain: ".youtube.com", path: "/", secure: true }];
          }
          assert.equal(query.domain, "youtube.com");
          return [{ name: "SAPISID", value: "account", domain: ".youtube.com", path: "/", secure: true }];
        },
      },
      scripting: {
        executeScript: async options => {
          assert.equal(options.target.tabId, 42);
          assert.equal(options.world, "MAIN");
          assert.equal(options.args[0], "dQw4w9WgXcQ");
          return [{ result: options.func(...options.args) }];
        },
      },
      runtime: { onMessage: { addListener: callback => { onMessage = callback; } } },
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        json: async () => url.endsWith("/formats")
          ? { status: "ready", session: "b".repeat(32), formats: [{ format_id: "720", label: "720p" }] }
          : { status: "queued", message: "enviado" },
      };
    },
  };
  loadBackground(context);
  const pageUrl = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  const send = message => new Promise(resolve => onMessage(message, {
    tab: { id: 42, url: pageUrl, title: "Video" },
  }, resolve));

  const formats = await send({
    type: "xdm-companion:get-formats",
    pageUrl,
    title: "Video",
  });
  assert.equal(formats.ok, true);
  assert.equal(requests[0].url, "http://127.0.0.1:21121/formats");
  const formatPayload = JSON.parse(requests[0].options.body);
  assert.deepEqual(formatPayload.cookies.map(cookie => cookie.name).sort(), ["SAPISID", "SID"]);
  assert.equal(formatPayload.user_agent, "Test browser");
  assert.deepEqual(formatPayload.audio_track, {
    id: "en.1",
    language: "en",
    name: "English",
    is_original: true,
  });
  assert.equal(formatPayload.accept_language, "es-DO,es");
  assert.deepEqual(formatPayload.browser_diagnostics.account_markers, {
    login_info: false,
    sapisid: true,
    secure_1papisid: false,
    secure_3papisid: false,
  });
  assert.equal(formatPayload.youtube_player_diagnostics.player_found, true);
  assert.equal(formatPayload.youtube_player_diagnostics.video_formats, 55);
  assert.equal(formatPayload.youtube_player_diagnostics.audio_formats, 1);
  assert.equal(formatPayload.youtube_player_diagnostics.audio_streams_present, true);
  assert.equal(formatPayload.youtube_player_diagnostics.direct_video_formats, 54);
  assert.equal(formatPayload.youtube_player_diagnostics.encrypted_video_formats, 0);
  assert.equal(formatPayload.youtube_playback.video_id, "dQw4w9WgXcQ");
  assert.equal(formatPayload.youtube_playback.audio_streams_present, true);
  assert.equal(formatPayload.youtube_playback.formats[0].height, 360);
  assert.match(formatPayload.youtube_playback.formats[0].url, /googlevideo\.com/);
  assert.equal(formatPayload.youtube_playback.source_heights[0], 4320);
  assert.ok(
    formatPayload.youtube_playback.formats.some(format => format.height === 2160),
    "the browser bridge preserves the highest tier when it appears after many lower formats",
  );

  const selected = await send({
    type: "xdm-companion:select-format",
    session: "b".repeat(32),
    formatId: "720",
  });
  assert.equal(selected.ok, true);
  assert.equal(requests[1].url, "http://127.0.0.1:21121/select");
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    session: "b".repeat(32),
    format_id: "720",
  });
});

test("the YouTube adapter reports an explicit no-audio player response", async () => {
  const player = {
    getPlayerResponse: () => ({
      videoDetails: { videoId: "dQw4w9WgXcQ", title: "Silent video" },
      streamingData: {
        adaptiveFormats: [{
          itag: 137,
          url: "https://rr1---sn.googlevideo.com/videoplayback?itag=137",
          mimeType: 'video/mp4; codecs="avc1.640028"',
          height: 1080,
          width: 1920,
        }],
      },
    }),
  };
  const context = {
    URL,
    console: { warn: () => {} },
    document: { querySelector: () => player },
    window: { ytInitialPlayerResponse: null },
    chrome: {
      scripting: {
        executeScript: async options => [{ result: options.func(...options.args) }],
      },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(bridgeDir, "providers/youtube.js"), "utf8"),
    context,
  );
  const youtubeProvider = siteCatalog.providers.find(provider => provider.id === "youtube");
  const result = await context.XDM_SITE_ADAPTERS.youtube.readPlayback(
    42,
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    youtubeProvider,
  );

  assert.equal(result.playback.audio_streams_present, false);
  assert.equal(result.diagnostics.audio_formats, 0);
  assert.equal(result.playback.formats[0].height, 1080);
});

test("the YouTube adapter identifies one unlabelled music track as the unique original", async () => {
  const player = {
    getAvailableAudioTracks: () => [{}],
    getPlayerResponse: () => ({
      videoDetails: { videoId: "dQw4w9WgXcQ", title: "Instrumental video" },
      streamingData: {
        formats: [{
          itag: 18,
          url: "https://rr1---sn.googlevideo.com/videoplayback?itag=18",
          mimeType: 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
          height: 360,
          audioTrack: {},
        }],
      },
    }),
  };
  const context = {
    URL,
    console: { warn: () => {} },
    document: { querySelector: () => player },
    window: { ytInitialPlayerResponse: null },
    chrome: {
      scripting: {
        executeScript: async options => [{ result: options.func(...options.args) }],
      },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(bridgeDir, "providers/youtube.js"), "utf8"),
    context,
  );
  const youtubeProvider = siteCatalog.providers.find(provider => provider.id === "youtube");
  const result = await context.XDM_SITE_ADAPTERS.youtube.readPlayback(
    42,
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    youtubeProvider,
  );

  assert.equal(result.playback.audio_streams_present, true);
  assert.deepEqual(
    JSON.parse(JSON.stringify(result.playback.audio_track)),
    { language: "", name: "", id: "", is_original: true, inferred: true },
  );
});

test("background gathers YouTube account cookies for a youtu.be video", async () => {
  let onMessage;
  let queriedDomain = false;
  let queriedCanonicalYoutubeHost = false;
  let sentPayload;
  const context = {
    URL,
    navigator: { userAgent: "Test browser" },
    console: { warn: () => {} },
    chrome: {
      cookies: {
        getAll: async query => {
          if (query.url === "https://www.youtube.com/") {
            queriedCanonicalYoutubeHost = true;
            return [{ name: "SAPISID", value: "account", domain: ".youtube.com", path: "/" }];
          }
          if (query.url === "https://youtube.com/") return [];
          if (query.domain) {
            queriedDomain = true;
            return [{ name: "LOGIN_INFO", value: "state", domain: ".youtube.com", path: "/" }];
          }
          return [];
        },
      },
      runtime: { onMessage: { addListener: callback => { onMessage = callback; } } },
    },
    fetch: async (_url, options) => {
      sentPayload = JSON.parse(options.body);
      return { ok: true, json: async () => ({ status: "ready", formats: [] }) };
    },
  };
  loadBackground(context);
  const response = await new Promise(resolve => onMessage(
    { type: "xdm-companion:get-formats", pageUrl: "https://youtu.be/example" },
    { tab: {} },
    resolve,
  ));

  assert.equal(response.ok, true);
  assert.equal(queriedDomain, true);
  assert.equal(queriedCanonicalYoutubeHost, true);
  assert.deepEqual(sentPayload.cookies.map(cookie => cookie.name).sort(), ["LOGIN_INFO", "SAPISID"]);
});

test("background reads player data and browser cookies through the browser namespace", async () => {
  let onMessage;
  let resolvePlayer;
  let playerReadStarted = false;
  let playerReadFinished = false;
  let cookieReads = 0;
  const context = {
    URL,
    navigator: { userAgent: "Test browser", languages: ["en"] },
    document: { querySelector: () => null, getElementById: () => null },
    window: {},
    console: { warn: () => {} },
    browser: {
      cookies: {
        getAll: async () => {
          cookieReads++;
          assert.equal(playerReadFinished, false, "cookie lookup should not wait for player extraction");
          return [];
        },
      },
      scripting: {
        executeScript: () => {
          playerReadStarted = true;
          return new Promise(resolve => {
            resolvePlayer = result => {
              playerReadFinished = true;
              resolve(result);
            };
          });
        },
      },
      runtime: { onMessage: { addListener: callback => { onMessage = callback; } } },
    },
    fetch: async () => ({ ok: true, json: async () => ({ status: "ready", formats: [] }) }),
  };
  loadBackground(context);
  const responsePromise = new Promise(resolve => onMessage(
    { type: "xdm-companion:get-formats", pageUrl: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" },
    { tab: { id: 7, url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" } },
    resolve,
  ));

  await new Promise(resolve => setImmediate(resolve));
  assert.equal(playerReadStarted, true);
  assert.ok(cookieReads > 0, "cookie requests should begin while the player read is pending");
  assert.equal(playerReadFinished, false);
  resolvePlayer([{ result: { playback: null, diagnostics: {} } }]);
  assert.equal((await responsePromise).ok, true);
});

test("anonymous format detection uses the current page response when the player response is stale", async () => {
  let onMessage;
  let sentPayload;
  const pageUrl = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  const currentResponse = {
    videoDetails: { videoId: "dQw4w9WgXcQ", title: "Public video" },
    streamingData: {
      adaptiveFormats: [{
        itag: 999,
        url: "https://rr1---sn.googlevideo.com/videoplayback?itag=999",
        mimeType: 'video/mp4; codecs="avc1.640033"',
        width: 3840,
        height: 2160,
        audioTrack: { id: "en.1", displayName: "English", audioIsDefault: true },
      }],
      dashManifestUrl: "https://manifest.googlevideo.com/api/manifest/dash/video",
    },
  };
  const stalePlayer = {
    getPlayerResponse: () => ({
      videoDetails: { videoId: "M7lc1UVf-VE", title: "Previous video" },
      streamingData: {
        formats: [{
          itag: 18,
          url: "https://rr1---sn.googlevideo.com/videoplayback?itag=18",
          mimeType: 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
          width: 640,
          height: 360,
        }],
      },
    }),
  };
  let playerAvailable = true;
  const context = {
    URL,
    navigator: { userAgent: "Test browser", languages: ["en"] },
    document: {
      querySelector: selector => playerAvailable && selector === "ytd-player" ? stalePlayer : null,
    },
    window: { ytInitialPlayerResponse: currentResponse },
    console: { warn: () => {} },
    chrome: {
      cookies: { getAll: async () => [] },
      scripting: {
        executeScript: async options => [{ result: options.func(...options.args) }],
      },
      runtime: { onMessage: { addListener: callback => { onMessage = callback; } } },
    },
    fetch: async (_url, options) => {
      sentPayload = JSON.parse(options.body);
      return { ok: true, json: async () => ({ status: "ready", formats: [] }) };
    },
  };
  loadBackground(context);
  const response = await new Promise(resolve => onMessage(
    { type: "xdm-companion:get-formats", pageUrl },
    { tab: { id: 7, url: pageUrl } },
    resolve,
  ));

  assert.equal(response.ok, true);
  assert.deepEqual(sentPayload.cookies, [], "public video detection also works without a login session");
  assert.equal(sentPayload.youtube_playback.video_id, "dQw4w9WgXcQ");
  assert.equal(sentPayload.youtube_playback.source_heights[0], 2160);
  assert.equal(sentPayload.youtube_playback.formats[0].height, 2160);

  playerAvailable = false;
  const globalOnlyResponse = await new Promise(resolve => onMessage(
    { type: "xdm-companion:get-formats", pageUrl },
    { tab: { id: 7, url: pageUrl } },
    resolve,
  ));
  assert.equal(globalOnlyResponse.ok, true);
  assert.equal(sentPayload.youtube_playback.video_id, "dQw4w9WgXcQ");
  assert.equal(sentPayload.youtube_playback.source_heights[0], 2160);
});

test("background rejects unsupported pages before contacting the local service", async () => {
  let onMessage;
  let requestCount = 0;
  const context = {
    URL,
    navigator: { userAgent: "Test browser" },
    console: { warn: () => {} },
    chrome: {
      cookies: { getAll: async () => [] },
      runtime: { onMessage: { addListener: callback => { onMessage = callback; } } },
    },
    fetch: async () => { requestCount++; return { ok: true, json: async () => ({}) }; },
  };
  loadBackground(context);
  const response = await new Promise(resolve => onMessage(
    { type: "xdm-companion:get-formats", pageUrl: "https://youtube.com.attacker.example/watch" },
    { tab: {} },
    resolve,
  ));

  assert.equal(response.ok, false);
  assert.equal(requestCount, 0);
});
