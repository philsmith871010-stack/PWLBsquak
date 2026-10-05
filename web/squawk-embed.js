/*!
 * PWLB squawk ticker - drop-in embed for an existing page.
 *
 *   <div data-squawk-ticker></div>
 *   <script src="https://philsmith871010-stack.github.io/pwlb-squawk-feed/web/squawk-embed.js" defer></script>
 *
 * The strip carries today's squawks only, so it always reads as live. Hovering pauses the
 * scroll; clicking opens a modal with the last 24 hours of squawks.
 *
 * Everything lives in a shadow root, so the host page's CSS and this file's cannot reach each
 * other. The modal is a <dialog> promoted to the top layer, so it overlays the page whatever
 * the host's stacking contexts and overflow look like.
 *
 * Options, as attributes on the mount element:
 *   data-src     feed URL (default: data/squawks.json next to this script's repo root)
 *   data-theme   "light" | "dark" (default: follow the viewer's system setting)
 *   data-label   strip label (default: "PWLB Squawk")
 *   data-motion  "scroll" (default: continuous tape) or "rotate" (one squawk at a time,
 *                swapped every few seconds)
 *   data-interval seconds each squawk shows in rotate mode (default 6)
 *   data-variant "banner" (light, orange-ruled row inside page content) or "topbar"
 *                (full-width strip for the very top of the page, above the site header)
 *
 * Colours can be overridden by setting --squawk-* custom properties on the mount element;
 * custom properties inherit through the shadow boundary.
 */
(function () {
  'use strict';

  var SCRIPT = document.currentScript;
  var REFRESH_MS = 60000;
  var SPEED_PX_PER_S = 55;        // marquee speed; slow enough to read at a glance
  var STRIP_MAX = 12;             // a busy day runs to 40+ squawks, and a strip carrying
                                  // all of them takes ten minutes to come round. The strip
                                  // samples the latest; the modal holds the day.
  var WINDOW_MS = 24 * 3600 * 1000; // the modal shows the last 24 hours
  var TZ = 'Europe/London';       // "today" means today in the UK, wherever the reader is
  var CATS = {
    boe: 'BoE', data: 'Data', fiscal: 'Fiscal', supply: 'Supply',
    geopolitics: 'Geopolitics', energy: 'Energy', global_rates: 'Global rates'
  };

  function defaultSrc() {
    // data/squawks.json sits one level up from web/, alongside this script.
    try { return new URL('../data/squawks.json', SCRIPT.src).href; } catch (e) { return 'data/squawks.json'; }
  }

  // -- dates ---------------------------------------------------------------------------

  var dayFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  var timeFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
  var longFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'long', day: 'numeric', month: 'long' });

  function dayKey(iso) { return dayFmt.format(new Date(iso)); }
  function todayKey() { return dayFmt.format(new Date()); }
  function dayLabel(key, iso) {
    var today = todayKey();
    if (key === today) return 'Today';
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (key === dayFmt.format(y)) return 'Yesterday';
    return longFmt.format(new Date(iso));
  }
  function ago(iso) {
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' d ago';
  }

  // -- helpers -------------------------------------------------------------------------

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  function impMark(n) { return n === 3 ? '\u25cf' : n === 2 ? '\u25d0' : '\u25cb'; }

  // -- styles --------------------------------------------------------------------------

  var STYLES = `
:host {
  --bg: var(--squawk-bg, #ffffff);
  --text: var(--squawk-text, #16202a);
  --muted: var(--squawk-muted, #5d6b7a);
  --line: var(--squawk-line, #e3e7ec);
  --accent: var(--squawk-accent, #0b5fff);
  --strip-bg: var(--squawk-strip-bg, #16202a);
  --strip-text: var(--squawk-strip-text, #f6f7f9);
  --strip-hover: var(--squawk-strip-hover, #223042);
  --boe: #7c3aed; --data: #0284c7; --fiscal: #b45309; --supply: #047857;
  --geopolitics: #be123c; --energy: #c2410c; --global_rates: #4338ca;
  --imp3: #d92d20; --imp2: #f79009; --imp1: #98a2b3;
  --radius: 10px;
  --fade: var(--strip-bg);
  display: block;
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  color: var(--text);
}
@media (prefers-color-scheme: dark) {
  :host(:not([data-theme="light"])) {
    --bg: var(--squawk-bg, #171f28);
    --text: var(--squawk-text, #e8edf2);
    --muted: var(--squawk-muted, #9aa8b6);
    --line: var(--squawk-line, #273240);
    --accent: var(--squawk-accent, #6ea0ff);
    --strip-bg: var(--squawk-strip-bg, #050810);
    --strip-hover: var(--squawk-strip-hover, #16202a);
    --boe: #b794f6; --data: #5cc0f2; --fiscal: #f2b264; --supply: #4ade80;
    --geopolitics: #fb7185; --energy: #fb923c; --global_rates: #a5b4fc;
  }
}
:host([data-theme="dark"]) {
  --bg: var(--squawk-bg, #171f28);
  --text: var(--squawk-text, #e8edf2);
  --muted: var(--squawk-muted, #9aa8b6);
  --line: var(--squawk-line, #273240);
  --accent: var(--squawk-accent, #6ea0ff);
  --strip-bg: var(--squawk-strip-bg, #050810);
  --strip-hover: var(--squawk-strip-hover, #16202a);
  --boe: #b794f6; --data: #5cc0f2; --fiscal: #f2b264; --supply: #4ade80;
  --geopolitics: #fb7185; --energy: #fb923c; --global_rates: #a5b4fc;
}
* { box-sizing: border-box; }

/* -- strip ------------------------------------------------------------------ */
.strip {
  display: flex; align-items: stretch;
  background: var(--strip-bg); color: var(--strip-text);
  border-radius: var(--radius); overflow: hidden;
  border: 1px solid transparent;
}
.strip:focus-within { border-color: var(--accent); }
.badge {
  display: flex; align-items: center; gap: 7px; flex: 0 0 auto;
  padding: 0 12px; font-size: 12px; font-weight: 700;
  letter-spacing: .06em; text-transform: uppercase;
  background: rgba(255,255,255,.07); border-right: 1px solid rgba(255,255,255,.09);
  white-space: nowrap;
}
.pulse {
  width: 7px; height: 7px; border-radius: 50%; background: var(--imp3);
  animation: pulse 2.4s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
.viewport { position: relative; flex: 1 1 auto; overflow: hidden; min-width: 0; }
.track {
  display: inline-flex; align-items: center; white-space: nowrap;
  padding: 9px 0; will-change: transform;
  animation: marquee var(--dur, 60s) linear infinite;
}
@keyframes marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.viewport:hover .track, .viewport:focus-within .track, .strip.paused .track { animation-play-state: paused; }
.item {
  display: inline-flex; align-items: baseline; gap: 7px;
  margin-right: 42px; font: inherit; font-size: 14px;
  color: inherit; background: none; border: 0; padding: 2px 4px;
  border-radius: 5px; cursor: pointer; white-space: nowrap;
}
.item .hl {
  text-decoration: underline; text-decoration-style: dotted; text-underline-offset: 4px;
  text-decoration-color: color-mix(in srgb, currentColor 40%, transparent);
  transition: text-decoration-color .12s ease, color .12s ease;
}
.item:hover, .item:focus-visible { background: var(--strip-hover); outline: none; }
.item:hover .hl, .item:focus-visible .hl {
  text-decoration-style: solid; text-decoration-color: currentColor;
}
.item .go { opacity: 0; margin-left: -4px; transition: opacity .12s ease, transform .12s ease; transform: translateX(-2px); }
.item:hover .go, .item:focus-visible .go { opacity: .8; transform: translateX(0); }
.item .mark { font-size: 10px; }
.item .mark.i3 { color: var(--imp3); } .item .mark.i2 { color: var(--imp2); } .item .mark.i1 { color: var(--imp1); }
.item .at { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
.item.more { color: var(--muted); font-weight: 600; }
.quiet { padding: 9px 14px; font-size: 14px; color: var(--muted); white-space: nowrap; }
.open {
  flex: 0 0 auto; display: flex; align-items: center; gap: 6px;
  padding: 0 12px; font: inherit; font-size: 12px; font-weight: 600;
  color: var(--strip-text); background: rgba(255,255,255,.07);
  border: 0; border-left: 1px solid rgba(255,255,255,.09); cursor: pointer;
  white-space: nowrap;
}
.open:hover { background: rgba(255,255,255,.16); }
.open:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.open .chev { transition: transform .15s ease; }
.open:hover .chev { transform: translateY(-1px); }
/* Fade the strip edges so items appear and leave rather than being cut off. */
.viewport::before, .viewport::after {
  content: ''; position: absolute; top: 0; bottom: 0; width: 28px; pointer-events: none; z-index: 1;
}
.viewport::before { left: 0; background: linear-gradient(to right, var(--fade), transparent); }
.viewport::after { right: 0; background: linear-gradient(to left, var(--fade), transparent); }

/* -- banner variant ---------------------------------------------------------
   The light, orange-ruled strip used by the host site's own banner rows, for
   pages where a black tape would read as an advert dropped into the layout. */
:host([data-variant="banner"]) {
  --fade: var(--squawk-banner-bg, rgba(253, 126, 20, .05));
}
:host([data-variant="banner"]) .strip {
  background: var(--squawk-banner-bg, rgba(253, 126, 20, .05));
  border: 1px solid var(--squawk-banner-border, rgba(253, 126, 20, .18));
  border-left: 3px solid var(--accent);
  color: var(--text);
}
:host([data-variant="banner"]) .badge {
  background: transparent; border-right: 0; color: var(--text);
  font-size: 14px; font-weight: 700; letter-spacing: 0; text-transform: none;
  padding-right: 10px;
}
:host([data-variant="banner"]) .badge .accent { color: var(--accent); }
:host([data-variant="banner"]) .pulse { background: var(--squawk-live, #23c552); }
:host([data-variant="banner"]) .item:hover,
:host([data-variant="banner"]) .item:focus-visible { background: rgba(253, 126, 20, .10); }
:host([data-variant="banner"]) .open {
  background: transparent; border-left: 1px solid var(--squawk-banner-border, rgba(253, 126, 20, .18));
  color: var(--accent);
}
:host([data-variant="banner"]) .open:hover { background: rgba(253, 126, 20, .10); }
:host([data-variant="banner"]) .item .at { color: var(--muted); }

/* -- topbar variant ---------------------------------------------------------
   Edge-to-edge strip for the very top of the page, above the site header: no
   radius, no side borders, the host's navy with its accent on the label. */
:host([data-variant="topbar"]) {
  --strip-bg: var(--squawk-topbar-bg, #0a2540);
  --strip-text: var(--squawk-topbar-text, #ffffff);
  --strip-hover: var(--squawk-topbar-hover, rgba(255, 255, 255, .10));
  --fade: var(--strip-bg);
}
:host([data-variant="topbar"]) .strip { border-radius: 0; border: 0; }
:host([data-variant="topbar"]) .strip:focus-within { box-shadow: inset 0 -2px 0 var(--accent); }
:host([data-variant="topbar"]) .track { padding: 7px 0; }
:host([data-variant="topbar"]) .item { font-size: 13.5px; }
:host([data-variant="topbar"]) .item .at { color: rgba(255, 255, 255, .6); }
:host([data-variant="topbar"]) .badge {
  background: rgba(255, 255, 255, .06); border-right: 1px solid rgba(255, 255, 255, .12);
  font-size: 13px; letter-spacing: 0; text-transform: none; padding: 0 14px;
}
:host([data-variant="topbar"]) .badge .accent { color: var(--accent); }
:host([data-variant="topbar"]) .pulse { background: var(--squawk-live, #23c552); }
:host([data-variant="topbar"]) .open {
  background: rgba(255, 255, 255, .06); border-left: 1px solid rgba(255, 255, 255, .12);
  color: var(--strip-text); padding: 0 14px;
}
:host([data-variant="topbar"]) .open:hover { background: rgba(255, 255, 255, .14); }

/* -- modal ------------------------------------------------------------------ */
dialog {
  width: min(680px, calc(100vw - 32px));
  max-height: min(78vh, 820px);
  padding: 0; border: 1px solid var(--line); border-radius: 14px;
  background: var(--bg); color: var(--text);
  box-shadow: 0 24px 60px rgba(0,0,0,.28);
  overflow: hidden;
}
dialog::backdrop { background: rgba(8,12,18,.55); backdrop-filter: blur(2px); }
.sheet { display: flex; flex-direction: column; max-height: inherit; }
.head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 14px 16px; border-bottom: 1px solid var(--line); flex: 0 0 auto;
}
.head h2 { margin: 0; font-size: 16px; letter-spacing: .01em; }
.head .sub { margin-top: 2px; font-size: 12px; color: var(--muted); }
.close {
  flex: 0 0 auto; width: 30px; height: 30px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--text);
  font-size: 16px; line-height: 1; display: grid; place-items: center;
}
.close:hover { background: var(--line); }
.close:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.body { overflow-y: auto; overscroll-behavior: contain; padding: 0 16px 8px; flex: 1 1 auto; }
.daybar {
  position: sticky; top: 0; z-index: 1; margin: 0 -16px; padding: 9px 16px;
  background: var(--bg); border-bottom: 1px solid var(--line);
  font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
}
.daybar.earlier { color: var(--muted); }
.card { padding: 14px 0; border-bottom: 1px solid var(--line); scroll-margin-top: 44px; }
.card:last-child { border-bottom: 0; }
.card:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 6px; }
.card.flash { animation: flash 1.4s ease-out; }
@keyframes flash { from { background: color-mix(in srgb, var(--accent) 14%, transparent); } to { background: transparent; } }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
.dot.i3 { background: var(--imp3); box-shadow: 0 0 0 3px color-mix(in srgb, var(--imp3) 25%, transparent); }
.dot.i2 { background: var(--imp2); } .dot.i1 { background: var(--imp1); }
.cat {
  padding: 1px 8px; border-radius: 999px; color: #fff;
  font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
}
.cat.boe { background: var(--boe); } .cat.data { background: var(--data); } .cat.fiscal { background: var(--fiscal); }
.cat.supply { background: var(--supply); } .cat.geopolitics { background: var(--geopolitics); }
.cat.energy { background: var(--energy); } .cat.global_rates { background: var(--global_rates); }
.card h3 { margin: 0 0 5px; font-size: 15.5px; line-height: 1.35; font-weight: 650; }
.card p { margin: 0; font-size: 14px; color: var(--text); }
.none { padding: 28px 0 32px; text-align: center; color: var(--muted); font-size: 14px; }

@media (max-width: 560px) {
  .badge span.txt { display: none; }
  .open span.txt { display: none; }
  dialog { width: 100vw; max-height: 88vh; border-radius: 14px 14px 0 0; }
}
/* -- rotate mode -------------------------------------------------------------
   One squawk at a time: the new one slides up into place, the old one is gone. */
.strip.rotate .viewport::before, .strip.rotate .viewport::after { display: none; }
.strip.rotate .track {
  display: flex; width: 100%; animation: none; overflow: hidden; padding: 0;
}
.strip.rotate .item {
  flex: 1 1 auto; min-width: 0; margin: 0; padding: 9px 10px; border-radius: 0;
  animation: rot-in .45s cubic-bezier(.2, .7, .2, 1) both;
}
.strip.rotate .item .hl { overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 0 1 auto; }
.strip.rotate .item .count {
  margin-left: auto; padding-left: 12px; flex: none;
  font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums;
}
.strip.rotate .item .cat {
  flex: none; padding: 1px 7px; border-radius: 999px; color: #fff;
  font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
}
.strip.rotate .bar { position: absolute; left: 0; bottom: 0; height: 2px; background: var(--accent); opacity: .7;
  animation: rot-bar var(--rot, 6s) linear both; }
.strip.rotate.paused .bar, .strip.rotate .viewport:hover .bar { animation-play-state: paused; }
@media (max-width: 600px) {
  .strip.rotate .item .at, .strip.rotate .item .cat, .strip.rotate .item .count { display: none; }
  .strip.rotate .item { white-space: normal; align-items: flex-start; text-align: left; line-height: 1.3; }
  .strip.rotate .item .hl {
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; white-space: normal;
  }
}
@keyframes rot-in { from { transform: translateY(70%); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes rot-bar { from { width: 0; } to { width: 100%; } }
:host([data-variant="topbar"]) .strip.rotate .item { padding: 7px 12px; }
:host([data-variant="topbar"]) .strip.rotate .item .count { color: rgba(255, 255, 255, .55); }

@media (prefers-reduced-motion: reduce) {
  .strip.rotate .item { animation: none; }
  .strip.rotate .bar { display: none; }
  .track { animation: none; }
  .viewport { overflow-x: auto; scrollbar-width: thin; }
  .pulse { animation: none; }
  .card.flash { animation: none; }
  .open .chev { transition: none; }
}
`;

  // -- widget --------------------------------------------------------------------------

  function mount(host) {
    var src = host.getAttribute('data-src') || defaultSrc();
    var label = host.getAttribute('data-label') || 'PWLB Squawk';
    var labelAccent = host.getAttribute('data-label-accent') || '';
    var root = host.attachShadow({ mode: 'open' });
    var sheet = document.createElement('style');
    sheet.textContent = STYLES;
    root.appendChild(sheet);

    var wrap = document.createElement('div');
    wrap.innerHTML = [
      '<div class="strip" part="strip">',
      '  <div class="badge"><span class="pulse"></span><span class="txt">', esc(label),
        labelAccent ? '<span class="accent">' + esc(labelAccent) + '</span>' : '', '</span></div>',
      '  <div class="viewport"><div class="track"></div></div>',
      '  <button class="open" type="button"><span class="txt">Today</span><span class="chev" aria-hidden="true">\u2303</span></button>',
      '</div>',
      '<dialog part="dialog"><div class="sheet">',
      '  <div class="head"><div><h2></h2><div class="sub"></div></div>',
      '    <button class="close" type="button" aria-label="Close">\u2715</button></div>',
      '  <div class="body"></div>',
      '</div></dialog>'
    ].join('');
    while (wrap.firstChild) root.appendChild(wrap.firstChild);

    var strip = root.querySelector('.strip');
    var viewport = root.querySelector('.viewport');
    var track = root.querySelector('.track');
    var openBtn = root.querySelector('.open');
    var dlg = root.querySelector('dialog');
    var body = root.querySelector('.body');
    var sub = root.querySelector('.sub');
    root.querySelector('.head h2').textContent =
      (host.getAttribute('data-title') || 'Squawks');

    var rotate = (host.getAttribute('data-motion') || '') === 'rotate';
    var rotSecs = Math.max(3, parseFloat(host.getAttribute('data-interval')) || 6);
    var rotIdx = 0, rotTimer = null, rotShown = [];
    if (rotate) {
      strip.classList.add('rotate');
      strip.style.setProperty('--rot', rotSecs + 's');
    }

    var all = [];          // every squawk, newest event first
    var today = [];        // today's only
    var generatedAt = null;
    var failed = false;

    // -- strip -------------------------------------------------------------------------

    function renderStrip() {
      if (failed && !all.length) {
        track.style.animation = 'none';
        track.innerHTML = '<span class="quiet">Squawk feed unavailable</span>';
        openBtn.hidden = true;
        return;
      }
      openBtn.hidden = false;
      openBtn.setAttribute('aria-label',
        today.length ? 'Open today\u2019s squawks (' + today.length + ')' : 'Open the squawk blotter');
      openBtn.querySelector('.txt').textContent = today.length ? 'Read all \u00b7 ' + today.length : 'Blotter';

      if (!today.length) {
        track.style.animation = 'none';
        var last = all[0];
        track.innerHTML = '<span class="quiet">No squawks yet today' +
          (last ? ' \u00b7 last one ' + esc(ago(last.ts)) : '') + '</span>';
        return;
      }
      if (rotate) { startRotation(today.slice(0, STRIP_MAX)); return; }
      // Rendered twice so the loop is seamless: the animation travels exactly half the track.
      var shown = today.slice(0, STRIP_MAX);
      var once = shown.map(function (s) {
        return '<button class="item" type="button" tabindex="-1" title="Read this squawk"' +
          ' data-id="' + esc(s.id) + '">' +
          '<span class="mark i' + s.importance + '">' + impMark(s.importance) + '</span>' +
          '<span class="at">' + esc(timeFmt.format(new Date(s.ts))) + '</span>' +
          '<span class="hl">' + esc(s.headline) + '</span>' +
          '<span class="go" aria-hidden="true">\u203a</span></button>';
      }).join('');
      var rest = today.length - shown.length;
      if (rest > 0) {
        once += '<button class="item more" type="button" tabindex="-1">+' + rest +
          ' more today \u2192</button>';
      }
      track.innerHTML = once + once;
      track.style.animation = '';
      sizeTrack();
    }

    // -- rotate mode -------------------------------------------------------------------

    function rotItem(s, i, n) {
      return '<button class="item" type="button" title="Read this squawk" data-id="' + esc(s.id) + '">' +
        '<span class="mark i' + s.importance + '">' + impMark(s.importance) + '</span>' +
        '<span class="at">' + esc(timeFmt.format(new Date(s.ts))) + '</span>' +
        '<span class="cat ' + esc(s.category) + '">' + esc(CATS[s.category] || s.category) + '</span>' +
        '<span class="hl">' + esc(s.headline) + '</span>' +
        '<span class="count">' + (i + 1) + '/' + n + '</span></button>';
    }

    function showRot() {
      if (!rotShown.length) return;
      rotIdx = rotIdx % rotShown.length;
      track.innerHTML = rotItem(rotShown[rotIdx], rotIdx, rotShown.length) + '<span class="bar"></span>';
    }

    function rotPaused() {
      return strip.classList.contains('paused') || viewport.matches(':hover') ||
        (root.activeElement && viewport.contains(root.activeElement));
    }

    function startRotation(list) {
      var currentId = rotShown[rotIdx] && rotShown[rotIdx].id;
      rotShown = list;
      // keep the reader on the same squawk across a refresh when it is still there
      var keep = list.findIndex(function (s) { return s.id === currentId; });
      rotIdx = keep > -1 ? keep : 0;
      track.style.animation = 'none';
      showRot();
      if (rotTimer) clearInterval(rotTimer);
      var waited = 0;
      rotTimer = setInterval(function () {
        if (rotPaused()) return;          // hold while hovered, focused or the modal is open
        waited += 0.25;
        if (waited >= rotSecs) { waited = 0; rotIdx += 1; showRot(); }
      }, 250);
    }

    function sizeTrack() {
      if (rotate) return;
      // Duration from width so the reading speed is constant however much news there is.
      var half = track.scrollWidth / 2;
      if (!half) return;
      if (half < viewport.clientWidth) {
        track.style.animation = 'none';       // it all fits; scrolling would be silly
        track.style.transform = 'none';
        return;
      }
      track.style.animation = '';
      track.style.setProperty('--dur', Math.round(half / SPEED_PX_PER_S) + 's');
    }

    // -- modal -------------------------------------------------------------------------

    function card(s, isToday) {
      return '<article class="card" data-id="' + esc(s.id) + '" tabindex="-1">' +
        '<div class="meta">' +
          '<span class="dot i' + s.importance + '" title="importance ' + s.importance + '"></span>' +
          '<span class="cat ' + esc(s.category) + '">' + esc(CATS[s.category] || s.category) + '</span>' +
          '<time datetime="' + esc(s.ts) + '">' + esc(timeFmt.format(new Date(s.ts))) +
            (isToday ? ' \u00b7 ' + esc(ago(s.ts)) : '') + '</time>' +
        '</div>' +
        '<h3>' + esc(s.headline) + '</h3>' +
        '<p>' + esc(s.detail) + '</p>' +
        '</article>';
    }

    function renderModal() {
      if (!all.length) {
        body.innerHTML = '<div class="none">' +
          (failed ? 'The squawk feed could not be loaded.' : 'Nothing published yet.') + '</div>';
        sub.textContent = '';
        return;
      }
      // The modal holds the last 24 hours, grouped by UK day (Today, Yesterday).
      var cutoff = Date.now() - WINDOW_MS;
      var recent = all.filter(function (s) { return new Date(s.ts).getTime() >= cutoff; });
      var tKey = todayKey();
      var groups = [];
      var current = null;
      recent.forEach(function (s) {
        var k = dayKey(s.ts);
        if (!current || current.key !== k) { current = { key: k, label: dayLabel(k, s.ts), items: [] }; groups.push(current); }
        current.items.push(s);
      });
      var html = '';
      if (!recent.length) {
        html = '<div class="none">No squawks in the last 24 hours.</div>';
      }
      groups.forEach(function (g) {
        html += '<div class="daybar' + (g.key === tKey ? '' : ' earlier') + '">' + esc(g.label) + '</div>';
        html += g.items.map(function (s) { return card(s, g.key === tKey); }).join('');
      });
      body.innerHTML = html;
      sub.textContent = recent.length
        ? recent.length + (recent.length === 1 ? ' squawk' : ' squawks') + ' in the last 24 hours'
        : '';
    }

    function openModal(focusId) {
      renderModal();
      if (typeof dlg.showModal === 'function') { if (!dlg.open) dlg.showModal(); }
      else { dlg.setAttribute('open', ''); }
      strip.classList.add('paused');   // hold the tape where the reader left it
      body.scrollTop = 0;
      if (focusId) {
        var el = body.querySelector('[data-id="' + cssEscape(focusId) + '"]');
        if (el) {
          el.scrollIntoView({ block: 'center' });
          el.classList.add('flash');
          el.focus({ preventScroll: true });
        }
      } else {
        root.querySelector('.close').focus({ preventScroll: true });
      }
    }

    function cssEscape(v) {
      return (window.CSS && window.CSS.escape) ? window.CSS.escape(v) : String(v).replace(/["\\]/g, '\\$&');
    }

    openBtn.addEventListener('click', function () { openModal(null); });
    track.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.item') : null;
      if (btn) openModal(btn.dataset.id || null);
    });
    root.querySelector('.close').addEventListener('click', function () { dlg.close(); });
    // The browser returns focus to whatever opened the dialog. When that is a strip item it
    // sits inside .viewport, and :focus-within would keep the marquee paused for good, so
    // park focus on the button instead: still visible, still keyboard-sane, outside the tape.
    dlg.addEventListener('close', function () {
      strip.classList.remove('paused');
      var a = root.activeElement;
      if (a && a.closest && a.closest('.viewport')) a.blur();
      openBtn.focus({ preventScroll: true });
    });
    // Clicking the backdrop closes: the dialog element itself is the backdrop hit area.
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });

    // -- data --------------------------------------------------------------------------

    function ingest(doc) {
      var list = Array.isArray(doc) ? doc : (doc && doc.squawks) || [];
      generatedAt = (doc && doc.generated_at) || null;
      // squawks.json is ordered by publication time; a backfill publishes days at once, so
      // order by when each thing actually happened.
      all = list.slice().sort(function (a, b) { return String(b.ts).localeCompare(String(a.ts)); });
      var tKey = todayKey();
      today = all.filter(function (s) { return dayKey(s.ts) === tKey; });
    }

    async function load() {
      try {
        var r = await fetch(src + (src.indexOf('?') > -1 ? '&' : '?') + 't=' + Date.now(), { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        ingest(await r.json());
        failed = false;
      } catch (e) {
        failed = true;
        if (window.console) console.warn('[squawk] could not load ' + src + ':', e.message);
      }
      renderStrip();
      if (dlg.open) {
        var keep = body.scrollTop;
        renderModal();
        body.scrollTop = keep;   // a refresh must not yank the reader back to the top
      }
    }

    load();
    setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
    window.addEventListener('resize', function () { clearTimeout(mount._t); mount._t = setTimeout(sizeTrack, 150); });
  }

  function init() {
    document.querySelectorAll('[data-squawk-ticker]').forEach(function (el) {
      if (!el.shadowRoot) mount(el);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
