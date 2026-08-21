/*
 * Lemma app editor bridge.
 *
 * Injected into every served app entrypoint, next to window.__LEMMA_CONFIG__.
 * An app runs on its own subdomain, so the pod shell that frames it cannot read
 * its DOM: picking an element to talk to the agent about has to be done by code
 * running *inside* the app, which is this.
 *
 * It is inert until the framing window says hello from an origin on the
 * allowlist stamped onto this script tag. That matters because the bridge reads
 * the rendered page and hands it to whoever framed it — without the origin
 * check, any site could embed a public app and use this to read what the
 * viewer's session renders.
 *
 * Injected verbatim (no build step), so it stays plain browser JS.
 */
(() => {
  const NS = 'lemma-app-editor:';
  const MESSAGE = {
    hello: NS + 'hello',
    ready: NS + 'ready',
    selectMode: NS + 'select-mode',
    selection: NS + 'selection',
  };
  const MAX_HTML = 1200;
  const MAX_TEXT = 200;
  const MAX_CHAIN = 6;
  const MAX_PATH_DEPTH = 8;
  const FALLBACK_ACCENT = '#5b6cff';
  const REPORTED_STYLES = [
    'display',
    'position',
    'width',
    'height',
    'padding',
    'margin',
    'color',
    'background-color',
    'font-size',
    'font-weight',
    'line-height',
    'border-radius',
    'gap',
    'flex-direction',
  ];

  const script = document.currentScript;
  if (!script || window.parent === window) return;

  let allowedOrigins = [];
  try {
    const raw = script.getAttribute('data-lemma-editor-origins');
    const parsed = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) {
      allowedOrigins = parsed.filter((value) => typeof value === 'string' && value);
    }
  } catch (error) {
    allowedOrigins = [];
  }
  if (!allowedOrigins.length) return;

  let hostOrigin = null;
  let active = false;
  let hovered = null;
  let overlay = null;
  let box = null;
  let label = null;
  let previousCursor = '';

  // --- messaging ----------------------------------------------------------

  function post(type, payload) {
    if (!hostOrigin) return;
    window.parent.postMessage(Object.assign({ type }, payload), hostOrigin);
  }

  function appInfo() {
    const config = window.__LEMMA_CONFIG__ || {};
    const app = config.app || {};
    return {
      name: app.name || null,
      id: config.appId || null,
      podId: config.podId || null,
    };
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return;
    if (allowedOrigins.indexOf(event.origin) === -1) return;
    const data = event.data;
    if (!data || typeof data !== 'object') return;

    if (data.type === MESSAGE.hello) {
      hostOrigin = event.origin;
      post(MESSAGE.ready, { app: appInfo() });
      return;
    }
    // Every other message requires a completed handshake, so a frame that never
    // introduced itself cannot drive the picker.
    if (event.origin !== hostOrigin) return;
    if (data.type === MESSAGE.selectMode) setActive(data.active === true);
  });

  // --- element resolution -------------------------------------------------

  function elementFrom(node) {
    let candidate = node;
    if (candidate && candidate.nodeType === Node.TEXT_NODE) {
      candidate = candidate.parentElement;
    }
    return candidate instanceof Element ? candidate : null;
  }

  /* The outermost element written by the same component as `element`.
   *
   * This is what makes a click land on the component someone can talk about
   * ("this order row") rather than on whichever nested <div> happened to sit
   * under the cursor. Elements carry the component that *wrote* them, so
   * walking up while that name holds steady walks to that component's root.
   * A component that renders itself recursively defeats it; alt-click selects
   * the exact element under the cursor for that case. */
  function componentRoot(element) {
    const name = element.getAttribute('data-lemma-component');
    if (!name) return element;
    let current = element;
    while (
      current.parentElement &&
      current.parentElement.getAttribute('data-lemma-component') === name
    ) {
      current = current.parentElement;
    }
    return current;
  }

  function resolveTarget(node, exact) {
    const element = elementFrom(node);
    if (!element || element === document.documentElement || element === document.body) {
      return null;
    }
    return exact ? element : componentRoot(element);
  }

  // --- selection payload --------------------------------------------------

  function parseSourceLocation(value) {
    if (!value) return null;
    const match = /^(.*):(\d+):(\d+)$/.exec(value);
    if (!match) return { file: value, line: null, column: null };
    return { file: match[1], line: Number(match[2]), column: Number(match[3]) };
  }

  function componentChain(element) {
    const chain = [];
    let current = element;
    while (current && chain.length < MAX_CHAIN) {
      const name = current.getAttribute('data-lemma-component');
      if (name && chain[chain.length - 1] !== name) chain.push(name);
      current = current.parentElement;
    }
    return chain;
  }

  /* A CSS path to the element. The only handle an app without source stamps
   * (a single-file HTML app, or a Vite build made before stamping) can offer,
   * and still the fastest way to find the element in a source file. */
  function domPath(element) {
    const parts = [];
    let current = element;
    while (current && current !== document.body && parts.length < MAX_PATH_DEPTH) {
      if (current.id) {
        parts.unshift('#' + current.id);
        break;
      }
      const tag = current.tagName.toLowerCase();
      const parent = current.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const siblings = Array.prototype.filter.call(
        parent.children,
        (child) => child.tagName === current.tagName,
      );
      parts.unshift(
        siblings.length > 1 ? tag + ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')' : tag,
      );
      current = parent;
    }
    return parts.join(' > ');
  }

  function truncate(value, limit) {
    if (!value) return '';
    return value.length > limit ? value.slice(0, limit) + '…' : value;
  }

  function computedStyles(element) {
    const computed = window.getComputedStyle(element);
    const styles = {};
    REPORTED_STYLES.forEach((name) => {
      const value = computed.getPropertyValue(name);
      if (value) styles[name] = value.trim();
    });
    return styles;
  }

  function selectionPayload(element) {
    const rect = element.getBoundingClientRect();
    return {
      app: appInfo(),
      route: window.location.pathname + window.location.search,
      source: parseSourceLocation(element.getAttribute('data-lemma-loc')),
      component: element.getAttribute('data-lemma-component') || null,
      componentChain: componentChain(element),
      tag: element.tagName.toLowerCase(),
      domId: element.id || null,
      className:
        typeof element.className === 'string' ? truncate(element.className, 300) : null,
      domPath: domPath(element),
      text: truncate((element.textContent || '').replace(/\s+/g, ' ').trim(), MAX_TEXT),
      html: truncate(element.outerHTML || '', MAX_HTML),
      rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      styles: computedStyles(element),
    };
  }

  // --- overlay ------------------------------------------------------------

  function setStyles(element, styles) {
    Object.keys(styles).forEach((name) => {
      // `important` because the overlay lives in the app's document and must
      // survive whatever the app's own stylesheet says about bare divs.
      element.style.setProperty(name, styles[name], 'important');
    });
  }

  function accentColor() {
    const themed = window
      .getComputedStyle(document.documentElement)
      .getPropertyValue('--lemma-app-accent')
      .trim();
    return themed || FALLBACK_ACCENT;
  }

  function ensureOverlay() {
    if (overlay) return;
    const accent = accentColor();

    overlay = document.createElement('div');
    setStyles(overlay, {
      position: 'fixed',
      top: '0',
      left: '0',
      right: '0',
      bottom: '0',
      'z-index': '2147483646',
      'pointer-events': 'none',
    });

    box = document.createElement('div');
    setStyles(box, {
      position: 'fixed',
      border: '2px solid ' + accent,
      'border-radius': '3px',
      background: 'color-mix(in srgb, ' + accent + ' 12%, transparent)',
      'pointer-events': 'none',
      display: 'none',
    });

    label = document.createElement('div');
    setStyles(label, {
      position: 'absolute',
      left: '0',
      top: '-22px',
      padding: '2px 6px',
      'border-radius': '3px',
      background: accent,
      color: '#fff',
      font: '500 11px/1.4 ui-sans-serif, system-ui, sans-serif',
      'white-space': 'nowrap',
      'pointer-events': 'none',
    });

    box.appendChild(label);
    overlay.appendChild(box);
    document.documentElement.appendChild(overlay);
  }

  function labelFor(element) {
    const component = element.getAttribute('data-lemma-component');
    const source = parseSourceLocation(element.getAttribute('data-lemma-loc'));
    const name = component || element.tagName.toLowerCase();
    if (!source || !source.line) return name;
    const file = source.file.split('/').pop();
    return name + ' · ' + file + ':' + source.line;
  }

  function highlight(element) {
    hovered = element;
    if (!element) {
      if (box) setStyles(box, { display: 'none' });
      return;
    }
    ensureOverlay();
    const rect = element.getBoundingClientRect();
    setStyles(box, {
      display: 'block',
      left: rect.left + 'px',
      top: rect.top + 'px',
      width: rect.width + 'px',
      height: rect.height + 'px',
    });
    // Keep the label on screen when the element is flush against the top.
    setStyles(label, { top: rect.top < 24 ? '100%' : '-22px' });
    label.textContent = labelFor(element);
  }

  // --- select mode --------------------------------------------------------

  function onPointerMove(event) {
    highlight(resolveTarget(event.target, event.altKey));
  }

  function onClick(event) {
    event.preventDefault();
    event.stopPropagation();
    const element = resolveTarget(event.target, event.altKey || event.detail > 1);
    if (!element) return;
    post(MESSAGE.selection, { selection: selectionPayload(element) });
    // One pick per activation: the person's next move is to describe the change
    // in the composer, not to keep clicking. The host toggle follows this.
    setActive(false);
    post(MESSAGE.selectMode, { active: false });
  }

  /* Swallow the rest of the pointer sequence too. Preventing `click` alone
   * still lets an app that acts on mousedown (drag handles, menus) react to a
   * pick, which would edit the very state the person is pointing at. */
  function swallow(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function onKeyDown(event) {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    setActive(false);
    post(MESSAGE.selectMode, { active: false });
  }

  function onViewportChange() {
    if (hovered) highlight(hovered);
  }

  const CAPTURED_EVENTS = ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'dblclick'];

  function setActive(next) {
    if (next === active) return;
    active = next;

    if (active) {
      document.addEventListener('pointermove', onPointerMove, true);
      document.addEventListener('click', onClick, true);
      document.addEventListener('keydown', onKeyDown, true);
      CAPTURED_EVENTS.forEach((name) => document.addEventListener(name, swallow, true));
      window.addEventListener('scroll', onViewportChange, true);
      window.addEventListener('resize', onViewportChange, true);
      previousCursor = document.documentElement.style.cursor;
      document.documentElement.style.setProperty('cursor', 'crosshair', 'important');
      return;
    }

    document.removeEventListener('pointermove', onPointerMove, true);
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('keydown', onKeyDown, true);
    CAPTURED_EVENTS.forEach((name) => document.removeEventListener(name, swallow, true));
    window.removeEventListener('scroll', onViewportChange, true);
    window.removeEventListener('resize', onViewportChange, true);
    document.documentElement.style.cursor = previousCursor;
    highlight(null);
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    overlay = null;
    box = null;
    label = null;
  }
})();
