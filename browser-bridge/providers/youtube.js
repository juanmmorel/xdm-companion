"use strict";

globalThis.XDM_SITE_ADAPTERS = globalThis.XDM_SITE_ADAPTERS || {};

function collectPlayerData(expectedVideoId, playerConfig) {
  const diagnostics = {
    player_found: false,
    response_found: false,
    video_id_matches: null,
    formats: 0,
    adaptive_formats: 0,
    video_formats: 0,
    direct_video_formats: 0,
    encrypted_video_formats: 0,
    drm_marked_formats: 0,
    source_heights: [],
    dash_manifest: false,
  };

  const selectors = Array.isArray(playerConfig.selectors) ? playerConfig.selectors : [];
  let player = null;
  for (const selector of selectors) {
    player = document.querySelector(selector);
    if (player) break;
  }
  if (!player) return { playback: null, diagnostics };
  diagnostics.player_found = true;

  const responseName = String(playerConfig.response_global || "");
  const response = player.getPlayerResponse?.()
    || (responseName ? window[responseName] : null);
  if (!response || typeof response !== "object") return { playback: null, diagnostics };
  diagnostics.response_found = true;
  const videoId = String(response?.videoDetails?.videoId || "");
  diagnostics.video_id_matches = !expectedVideoId || !videoId || expectedVideoId === videoId;
  if (!diagnostics.video_id_matches) return { playback: null, diagnostics };

  const streaming = response?.streamingData;
  const languageInfo = track => {
    try { return track?.getLanguageInfo?.() || null; } catch (_) { return null; }
  };
  const trackId = track => {
    if (typeof track === "string") return track;
    if (!track || typeof track !== "object") return "";
    const info = languageInfo(track);
    const direct = track.id || track.audioTrackId || track.trackId || info?.id;
    if (direct) return String(direct);
    try {
      for (const value of Object.values(track)) {
        if (value && typeof value === "object" && value.id) return String(value.id);
      }
    } catch (_) {}
    return "";
  };
  const trackLanguage = track => {
    if (typeof track === "string") return track.split(".")[0];
    if (!track || typeof track !== "object") return "";
    const info = languageInfo(track);
    const direct = track.languageCode || track.language || info?.languageCode || info?.id;
    return String(direct || trackId(track) || "").split(".")[0];
  };
  const trackName = track => {
    if (!track || typeof track !== "object") return "";
    const info = languageInfo(track);
    return String(track.displayName || track.name || track.label || info?.name || "");
  };
  const allFormats = [
    ...(Array.isArray(streaming?.formats) ? streaming.formats : []),
    ...(Array.isArray(streaming?.adaptiveFormats) ? streaming.adaptiveFormats : []),
  ];
  diagnostics.formats = Array.isArray(streaming?.formats) ? streaming.formats.length : 0;
  diagnostics.adaptive_formats = Array.isArray(streaming?.adaptiveFormats)
    ? streaming.adaptiveFormats.length : 0;
  const current = player.getAudioTrack?.();
  let available = [];
  try { available = Array.from(player.getAvailableAudioTracks?.() || []); } catch (_) {}
  const captionAudioTracks = response?.captions?.playerCaptionsTracklistRenderer?.audioTracks || [];
  const defaultAudioTrackIndex = Number(
    response?.captions?.playerCaptionsTracklistRenderer?.defaultAudioTrackIndex,
  );
  const captionDefaultTrack = Number.isInteger(defaultAudioTrackIndex)
    ? captionAudioTracks[defaultAudioTrackIndex] || null
    : null;
  const isOriginalTrack = track => Boolean(
    track && (
      track.audioIsDefault === true
      || track.isDefault === true
      || /\boriginal\b/i.test(trackName(track))
    ),
  );
  const originalTrack = available.find(isOriginalTrack)
    || allFormats.map(format => format.audioTrack).find(isOriginalTrack)
    || captionAudioTracks.find(isOriginalTrack)
    || captionDefaultTrack
    || (isOriginalTrack(current) ? current : null);
  const language = trackLanguage(originalTrack);
  const name = trackName(originalTrack);
  const id = trackId(originalTrack);
  const audioTrack = (!language && !name && !id)
    ? null
    : {
        language: String(language).slice(0, 40),
        name: String(name).slice(0, 100),
        id: String(id).slice(0, 128),
        is_original: true,
      };
  const videoFormats = allFormats.filter(format =>
    String(format?.mimeType || "").toLowerCase().startsWith("video/"),
  );
  diagnostics.video_formats = videoFormats.length;
  diagnostics.direct_video_formats = videoFormats.filter(format => Boolean(format?.url)).length;
  diagnostics.encrypted_video_formats = videoFormats.filter(format =>
    Boolean(format?.signatureCipher || format?.cipher),
  ).length;
  diagnostics.drm_marked_formats = videoFormats.filter(format =>
    Boolean(format?.drmFamilies?.length),
  ).length;
  const sourceHeights = [...new Set(videoFormats
    .map(format => Number(format.height) || 0)
    .filter(height => height > 0))]
    .sort((left, right) => right - left);
  diagnostics.source_heights = sourceHeights;
  diagnostics.dash_manifest = Boolean(streaming?.dashManifestUrl);
  const formats = videoFormats
    .map(format => {
      const track = format.audioTrack || {};
      return {
        itag: format.itag,
        url: format.url || "",
        mime_type: format.mimeType || "",
        bitrate: format.bitrate,
        width: format.width,
        height: format.height,
        fps: format.fps,
        quality: format.qualityLabel || "",
        audio_quality: format.audioQuality || "",
        audio_sample_rate: format.audioSampleRate,
        audio_track: {
          id: track.id || "",
          name: track.displayName || "",
          is_default: typeof track.audioIsDefault === "boolean"
            ? track.audioIsDefault
            : null,
        },
        drm: Boolean(format.drmFamilies?.length),
      };
    })
    .filter(format => format.url);
  return {
    playback: {
      video_id: videoId,
      title: String(response?.videoDetails?.title || "").slice(0, 500),
      dash_manifest_url: streaming?.dashManifestUrl || "",
      source_heights: sourceHeights,
      formats,
      audio_track: audioTrack,
    },
    diagnostics,
  };
}

function expectedVideoId(pageUrl, playerConfig) {
  try {
    const url = new URL(pageUrl);
    const shortDomains = Array.isArray(playerConfig.short_link_domains)
      ? playerConfig.short_link_domains : [];
    let candidate = "";
    if (shortDomains.includes(url.hostname)) {
      candidate = url.pathname.split("/").filter(Boolean)[0] || "";
    } else {
      candidate = url.searchParams.get(playerConfig.video_id_query || "") || "";
      if (!candidate) {
        for (const prefix of playerConfig.video_path_prefixes || []) {
          if (url.pathname.startsWith(prefix)) {
            candidate = url.pathname.slice(prefix.length).split("/")[0] || "";
            break;
          }
        }
      }
    }
    const pattern = new RegExp(`^(?:${playerConfig.video_id_pattern})$`);
    return pattern.test(candidate) ? candidate : "";
  } catch (_) {
    return "";
  }
}

async function readPlayback(tabId, pageUrl, provider) {
  if (!Number.isInteger(tabId) || !globalThis.chrome?.scripting?.executeScript) {
    return { playback: null, diagnostics: { script_available: false } };
  }
  const playerConfig = provider?.browser?.player || {};
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      args: [expectedVideoId(pageUrl, playerConfig), playerConfig],
      func: collectPlayerData,
    });
    const result = results && results[0] && results[0].result;
    return result && typeof result === "object"
      ? {
          playback: result.playback && typeof result.playback === "object" ? result.playback : null,
          diagnostics: result.diagnostics && typeof result.diagnostics === "object"
            ? result.diagnostics : {},
        }
      : { playback: null, diagnostics: { script_result_missing: true } };
  } catch (error) {
    console.warn("XDM Companion no pudo leer los formatos del adaptador:", error);
    return {
      playback: null,
      diagnostics: { script_error: String(error && error.name || "Error").slice(0, 48) },
    };
  }
}

function browserDiagnostics(cookieSession, provider) {
  const browserConfig = provider?.browser || {};
  const markerConfig = browserConfig.account_cookie_markers || {};
  const cookieNames = new Set((cookieSession.cookies || [])
    .filter(cookie => cookie && cookie.value)
    .map(cookie => String(cookie.name || "").toUpperCase()));
  const accountMarkers = {};
  for (const [marker, names] of Object.entries(markerConfig)) {
    accountMarkers[marker] = (Array.isArray(names) ? names : [names])
      .some(name => cookieNames.has(String(name).toUpperCase()));
  }
  return {
    cookie_count: (cookieSession.cookies || []).length,
    account_markers: accountMarkers,
    queries: (cookieSession.queries || []).map(result => ({
      kind: result.kind,
      count: result.cookies.length,
      error: result.error,
    })),
  };
}

function attachPayload(payload, cookieSession, playerResult, provider) {
  const keys = provider?.browser?.payload_keys || {};
  const browserDiagnosticsKey = keys.browser_diagnostics;
  const playerDiagnosticsKey = keys.player_diagnostics;
  const audioTrackKey = keys.audio_track;
  const playbackKey = keys.playback;
  if (browserDiagnosticsKey) payload[browserDiagnosticsKey] = browserDiagnostics(cookieSession, provider);
  if (playerDiagnosticsKey) payload[playerDiagnosticsKey] = playerResult.diagnostics || {};
  if (playerResult.playback?.audio_track && audioTrackKey) {
    payload[audioTrackKey] = playerResult.playback.audio_track;
  }
  if (playerResult.playback && playbackKey) payload[playbackKey] = playerResult.playback;
}

globalThis.XDM_SITE_ADAPTERS.youtube = {
  readPlayback,
  attachPayload,
};
