/* Loaded globally by the integration, including on HA's device settings pages. */
import './device-settings.js?v=0.1.17';

const DEVICE_PATH = /^\/config\/devices\/device\/([^/]+)\/?$/;
const PARAMETER = 'moswitch_settings';
let opening = false;

function dialogTarget(value) {
  const url = new URL(value, location.href);
  const device = url.origin === location.origin && DEVICE_PATH.exec(url.pathname);
  const entry = url.searchParams.get(PARAMETER);
  return device && entry && /^[\w-]{1,64}$/.test(entry)
    ? {deviceId: decodeURIComponent(device[1]), entryId: entry, url} : null;
}

async function hassRoot() {
  for (let attempt = 0; attempt < 200; attempt++) {
    const root = document.querySelector('home-assistant');
    if (root?.hass?.connection && root.hass.user) return root;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error('Home Assistant ist noch nicht verbunden. Bitte die Seite neu laden.');
}

class MoSwitchDeviceDialog extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
  }
  set hass(value) {
    this._hass = value;
    if (this._content) this._content.hass = value;
  }
  showDialog(params) {
    this._content?.remove();
    this._closing = false;
    this._events?.abort();
    this._events = new AbortController();
    this.shadowRoot.innerHTML = `<style>
      ha-dialog{--ha-dialog-width-md:760px;--dialog-content-padding:0}
      .tabs{display:flex;gap:8px;padding:8px 20px 12px;border-bottom:1px solid var(--divider-color)}
      .tabs button{font:inherit;font-size:14px;font-weight:500;min-height:40px;flex:1;border:0;border-radius:20px;padding:8px 16px;background:transparent;color:var(--secondary-text-color);cursor:pointer}
      .tabs button[aria-selected="true"]{background:var(--secondary-background-color);color:var(--primary-color)}
      .tabs button:disabled{opacity:.45;cursor:default}.tabs button:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}
      .body{max-height:70vh;overflow:auto;overscroll-behavior:contain}#loading{padding:24px;color:var(--secondary-text-color)}
      @media(max-width:600px){.tabs{padding-inline:12px}.body{max-height:70dvh}}
    </style><ha-dialog open heading="MoSwitch einrichten" header-title="MoSwitch einrichten">
      <div class="tabs" role="tablist" aria-label="Schaltereinstellungen">
        <button id="touch-tab" role="tab" aria-selected="true" aria-controls="dialog-content" autofocus>Touchfläche</button>
        <button id="assignment-tab" role="tab" tabindex="-1" aria-selected="false" aria-controls="dialog-content">Zuordnung & Aktionen</button>
      </div><div class="body" id="dialog-content" role="tabpanel" aria-labelledby="touch-tab"></div>
    </ha-dialog>`;
    const dialog = this.shadowRoot.querySelector('ha-dialog');
    dialog.heading = params.name;
    dialog.setAttribute('header-title', params.name);
    this._dialog = dialog;
    dialog.addEventListener('closed', () => {if(this._dialog===dialog)this.closeDialog();}, {signal:this._events.signal});
    this._content = document.createElement('moswitch-device-settings');
    this._content.setAttribute('dialog-view', 'touch');
    this._content.hass = this._hass;
    this._content.route = {path: '/' + params.entryId};
    this._content.addEventListener('moswitch-finished', () => this.closeDialog());
    this._content.addEventListener('moswitch-status', event => {
      this.shadowRoot.getElementById('assignment-tab').disabled = event.detail.active || event.detail.busy;
    });
    const body = this.shadowRoot.getElementById('dialog-content');
    body.append(this._content);
    for (const [tab, view] of [['touch-tab', 'touch'], ['assignment-tab', 'assignment']]) {
      this.shadowRoot.getElementById(tab).onclick = () => {
        this._content.setAttribute('dialog-view', view);
        body.setAttribute('aria-labelledby', tab);
        body.scrollTop = 0;
        for (const button of this.shadowRoot.querySelectorAll('[role="tab"]')) {
          button.setAttribute('aria-selected', String(button.id === tab));
          button.tabIndex = button.id === tab ? 0 : -1;
        }
      };
    }
    this.shadowRoot.querySelector('.tabs').onkeydown = event => {
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      const tabs=Array.from(this.shadowRoot.querySelectorAll('[role="tab"]')).filter(tab=>!tab.disabled);
      const selected=tabs.findIndex(tab=>tab.getAttribute('aria-selected')==='true');
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(selected+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      tabs[next].click();tabs[next].focus();event.preventDefault();
    };
    dialog.open = true;
  }
  closeDialog() {
    if (this._closing) return true;
    this._closing = true;
    // Removing content closes its WS subscription and cancels owned learning.
    this._content?.remove();
    this._content = null;
    this.shadowRoot.querySelector('ha-dialog').open = false;
    this.dispatchEvent(new CustomEvent('dialog-closed', {
      bubbles: true, composed: true, detail: {dialog: this.localName},
    }));
    return true;
  }
}
customElements.define('moswitch-device-dialog', MoSwitchDeviceDialog);

async function openDialog(target) {
  if (opening) return;
  opening = true;
  let root;
  try {
    root = await hassRoot();
    const info = await root.hass.callWS({type: 'moswitch/device/info', entry_id: target.entryId});
    if (info.device_id !== target.deviceId) throw Error('Der Assistent gehört zu einem anderen Schalter. Bitte den Geräte-Link neu öffnen.');
    await customElements.whenDefined('ha-dialog');
    root.dispatchEvent(new CustomEvent('show-dialog', {
      bubbles: true, composed: true, detail: {
        dialogTag: 'moswitch-device-dialog', dialogImport: () => Promise.resolve(),
        dialogParams: {entryId: info.entry_id, name: info.name},
      },
    }));
  } catch (error) {
    root?.dispatchEvent(new CustomEvent('hass-notification', {
      bubbles: true, composed: true, detail: {message: error.message || String(error)},
    }));
  } finally { opening = false; }
}

// Intercept only our own configuration URL. No HA DOM or component is patched.
document.addEventListener('click', event => {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  for (const node of event.composedPath()) {
    const href = node.getAttribute?.('href');
    if (!href) continue;
    let target;
    try { target = dialogTarget(href); } catch { continue; }
    if (!target) continue;
    event.preventDefault();
    event.stopPropagation();
    openDialog(target);
    return;
  }
}, true);

// Direct links and opening in a new tab work without visiting a dashboard first.
async function openLocation() {
  const target = dialogTarget(location.href);
  if (!target) return;
  target.url.searchParams.delete(PARAMETER);
  history.replaceState(history.state, '', target.url.pathname + target.url.search + target.url.hash);
  await openDialog(target);
}
window.addEventListener('location-changed', openLocation);
window.addEventListener('popstate', openLocation);
openLocation();
