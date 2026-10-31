/* Shared device settings content for the HA dialog and legacy panel route. */
const ACTIVE = [1, 2, 7, 9, 11, 12, 13];
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
class MoSwitchDeviceSettings extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:'open'});
    this._generation = 0;
    this._connected = false;
    this._busy = false;
    this._dirty = false;
    this._session = (crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join("-"));
  }
  set hass(hass) {
    this._hass = hass;
    if (this._connected && this._entry && !this._unsub && !this._loading) this._load();
  }
  set route(route) {
    const entry = route.path.replace(/^\/moswitch(?=\/|$)/,'').replace(/^\//,'').split('/')[0];
    if (entry === this._entry) return;
    this._dispose();
    this._entry = entry;
    this._dirty = false;
    this._learning = false;
    this._session = (crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join("-"));
    if (this._connected && this._hass) this._load();
  }
  connectedCallback() {
    this._connected = true;
    if (this._entry && this._hass) this._load();
  }
  disconnectedCallback() { this._connected = false; this._dispose(); }
  _dispose() {
    this._generation++;
    if (this._unsub) this._unsub();
    this._unsub = null;
    this._loading = false;
    clearInterval(this._clock);
  }
  async _load() {
    if (this._loading) return;
    this._loading = true;
    const generation = ++this._generation;
    this._shell();
    try {
      const unsub = await this._hass.connection.subscribeMessage(data => {
        if (generation !== this._generation) return;
        this._data = data;
        this._updatedAt = Date.now();
        if (!this._initialized) this._initialize(data);
        this._updateTouch();
      }, {type:'moswitch/device/subscribe',entry_id:this._entry,session:this._session});
      if (generation !== this._generation) { unsub(); return; }
      this._unsub = unsub;
      this._clock = setInterval(() => this._updateTouch(), 1000);
    } catch (error) { this._error(error); }
    finally { if (generation === this._generation) this._loading = false; }
  }
  _shell() {
    this._initialized = false;
    this._busy = false;
    this._actionsInvalid = false;
    this._formReady = false;
    this._parameterDirty = new Set();
    this.shadowRoot.innerHTML = `<style>
      :host{display:block;height:100%;overflow:auto;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,Arial,sans-serif);--accent:var(--primary-color)}
      *{box-sizing:border-box} header{height:64px;display:flex;align-items:center;gap:16px;padding:0 20px;background:var(--app-header-background-color,var(--card-background-color));color:var(--app-header-text-color,var(--primary-text-color));border-bottom:1px solid var(--divider-color);position:sticky;top:0;z-index:2}
      header a{color:inherit;padding:8px;display:flex;border-radius:50%}header span{font-size:20px}main{max-width:920px;margin:0 auto;padding:32px 24px 64px}
      h1{font-size:30px;line-height:1.2;font-weight:500;margin:8px 0 12px;overflow-wrap:anywhere}h2{font-size:20px;font-weight:500;margin:0}h3{font-size:18px;font-weight:500;margin:0 0 12px}p{line-height:1.55;margin:8px 0;color:var(--secondary-text-color)}
      .eyebrow{font-size:13px;color:var(--secondary-text-color)}.intro{margin-bottom:28px}.meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.pill{display:inline-flex;align-items:center;gap:7px;border-radius:20px;background:var(--secondary-background-color);padding:6px 12px;font-size:13px}.pill i{width:7px;height:7px;background:var(--success-color,#43a047);border-radius:50%}.pill.quiet i{background:var(--warning-color,#ffa600)}
      section{background:var(--ha-card-background,var(--card-background-color));border:1px solid var(--divider-color);border-radius:var(--ha-card-border-radius,12px);margin-bottom:16px;padding:24px}.section-head{display:flex;gap:16px;align-items:flex-start;margin-bottom:20px}.section-head ha-icon{color:var(--accent);flex:none;margin-top:1px}.section-head p{margin-bottom:0}.section-head>div{flex:1}
      label{display:block;font-size:14px;margin:0 0 8px}select,input{font:inherit;color:var(--primary-text-color);background:var(--input-fill-color,var(--secondary-background-color));border:1px solid var(--divider-color);border-radius:8px;padding:12px;min-height:48px;width:100%}select:focus-visible,input:focus-visible,button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}button{font:inherit;font-weight:500;font-size:14px;border:1px solid var(--divider-color);border-radius:20px;padding:10px 20px;min-height:40px;cursor:pointer;background:transparent;color:var(--accent)}button.primary{background:var(--accent);color:var(--text-primary-color);border-color:var(--accent)}button:disabled{opacity:.45;cursor:default}button:hover:not(:disabled){filter:brightness(.94)}
      .actions{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin-top:20px}.row{display:flex;gap:16px;align-items:center;justify-content:space-between}.room{max-width:400px}.lights{border-top:1px solid var(--divider-color);margin-top:20px;padding-top:16px}.light{display:flex;gap:10px;align-items:center;margin-top:12px;font-size:14px}.light ha-icon{color:var(--secondary-text-color);--mdc-icon-size:20px}.hint{font-size:13px}.gesture{padding:14px 0;border-bottom:1px solid var(--divider-color);font-size:14px}.gesture strong{display:inline-block;min-width:100px;font-weight:500}.gesture span{color:var(--secondary-text-color)}
      .editor-group{margin-top:24px}.check{display:flex;align-items:center;gap:12px;min-height:44px;margin:0;font-size:14px}.check input{width:18px;height:18px;min-height:0;accent-color:var(--accent)}.scene-row{display:flex;gap:8px;align-items:center}.scene-row label{flex:1}.icon-button{padding:8px 12px;min-width:40px}.action-editor{margin-top:20px}textarea{width:100%;font:13px monospace;line-height:1.5;padding:12px;background:var(--secondary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:8px;resize:vertical}
      .notice{padding:12px 16px;border-radius:8px;margin:16px 0;line-height:1.5;background:var(--secondary-background-color);font-size:14px}.error{border-inline-start:4px solid var(--error-color);color:var(--primary-text-color)}.success{border-inline-start:4px solid var(--success-color);color:var(--primary-text-color)}[hidden]{display:none!important}
      #wizard{scroll-margin-top:80px}.steps{display:flex;gap:8px;padding:0;list-style:none;margin:24px 0}.steps li{flex:1;border-top:3px solid var(--divider-color);padding-top:8px;font-size:12px;color:var(--secondary-text-color)}.steps li.current,.steps li.done{border-color:var(--accent);color:var(--primary-text-color)}.stage{padding:28px 24px;border-radius:12px;background:var(--secondary-background-color);text-align:center}.stage p{max-width:520px;margin:12px auto 0}.count{font-size:44px;line-height:1.2;font-weight:500;margin-bottom:16px;font-variant-numeric:tabular-nums}.count small{font-size:20px;color:var(--secondary-text-color)}.orb{margin:0 auto 18px;width:40px;height:40px;border:5px solid var(--accent);border-radius:50%;background:var(--card-background-color)}.orb.pulse{animation:breathe 1.6s ease-in-out infinite}.orb.touch{background:var(--accent)}@keyframes breathe{50%{opacity:.4;transform:scale(.85)}}@media(prefers-reduced-motion:reduce){.orb.pulse{animation:none}}
      details>summary{cursor:pointer;display:flex;align-items:center;gap:12px;font-weight:500;padding:4px 0;list-style:none}details>summary::after{content:'⌄';margin-inline-start:auto;font-size:20px}details[open]>summary::after{content:'⌃'}details .body{padding-top:20px}.parameters{display:grid;grid-template-columns:1fr 1fr;gap:16px 24px;margin-top:24px}.parameter .row{gap:8px}.parameter input{max-width:110px}.parameter small{color:var(--secondary-text-color)}.parameter button{padding:8px 12px}.footer{display:flex;align-items:center;gap:16px;justify-content:flex-end;margin:24px 0}.footer span{font-size:14px;color:var(--secondary-text-color)}ha-form{display:block;margin-top:20px}.divider{margin-top:20px;padding-top:16px;border-top:1px solid var(--divider-color)}
      @media(max-width:600px){header{height:56px;padding:0 8px}header span{font-size:18px}main{padding:24px 16px 40px}h1{font-size:26px}section{padding:20px}.section-head{gap:12px}.stage{padding:24px 14px}.parameters{grid-template-columns:1fr}.steps{gap:5px}.steps li{font-size:11px}.footer{flex-wrap:wrap}.actions button{flex-grow:1}.gesture strong{min-width:90px}}
      :host([dialog-view]){height:auto;overflow:visible;background:transparent}
      :host([dialog-view]) header,:host([dialog-view]) .intro .eyebrow,:host([dialog-view]) h1,:host([dialog-view]) #area-name{display:none}
      :host([dialog-view]) main{padding:16px 20px 24px;max-width:none}
      :host([dialog-view]) .intro{margin:0 0 16px}
      :host([dialog-view]) section{padding:20px}
      :host([dialog-view="touch"]) #room-section,:host([dialog-view="touch"]) #actions-section,:host([dialog-view="touch"]) #assignment-footer{display:none}
      :host([dialog-view="assignment"]) #touch,:host([dialog-view="assignment"]) #advanced-section{display:none}
      @media(max-width:600px){:host([dialog-view]) main{padding:16px 12px}:host([dialog-view]) section{padding:16px}}
      .manual-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}.manual-metrics>div{padding:12px;background:var(--secondary-background-color);border-radius:8px}.manual-metrics span{display:block;color:var(--secondary-text-color);font-size:12px;margin-bottom:6px}.manual-metrics b{font-size:18px;font-weight:500}.manual [data-param]{display:none}@media(max-width:600px){.manual-metrics{grid-template-columns:1fr 1fr}}
    </style>
    <header><a id="back" aria-label="Zurück zum Gerät"><ha-icon icon="mdi:arrow-left"></ha-icon></a><span>MoSwitch · Einstellungen</span></header>
    <main><div class="intro"><div class="eyebrow">SCHALTER EINRICHTEN</div><h1 id="name">Einstellungen werden geladen …</h1><div class="meta"><span id="area-name" class="eyebrow"></span><span id="mode" class="pill"></span></div></div>
    <div id="error" role="alert" class="notice error" hidden></div><div id="saved" role="status" class="notice success" hidden></div>
    <div id="content" hidden>
    <section id="room-section"><div class="section-head"><ha-icon icon="mdi:floor-plan"></ha-icon><div><h2>Raum & Licht</h2><p>Der Schalter steuert die Zigbee-Leuchten seines Raums.</p></div></div><div class="room"><label for="area">Raum zuordnen</label><select id="area"></select></div><p id="area-change" class="hint" hidden>Nach dem Raumwechsel Mediengeräte und Szenen neu auswählen. Eigene Aktionen bleiben erhalten.</p><div id="lights" class="lights"></div></section>
    <section id="touch"><div class="section-head"><ha-icon icon="mdi:gesture-tap"></ha-icon><div><h2>Touchfläche</h2><p id="touch-summary">Status wird geladen …</p></div></div>
      <div id="touch-home"><p>Stimme die montierte Touchfläche manuell ab oder starte das automatische Einlernen mit Ruhephase, zehn Tipps und gehaltenem Kontakt.</p><div class="actions"><button id="manual" class="primary">Manuell abstimmen</button><button id="learn">Automatisch einlernen</button><button id="reconnect" hidden>Verbindung prüfen</button><button id="activate-home" hidden>Zum Schalten freigeben</button></div></div>
      <div id="wizard" hidden><div class="notice">Während des Einlernens werden keine Licht-, Szenen- oder Medienaktionen ausgelöst.</div><ol class="steps"><li>Ruhe messen</li><li>10 Tipps</li><li>Halten</li><li>Fertig</li></ol><div class="stage" aria-live="polite"><div id="orb" class="orb"></div><div id="count" class="count" hidden></div><h3 id="stage-title"></h3><p id="instruction"></p><p id="feedback" class="hint"></p></div><div class="actions"><button id="baseline" class="primary" hidden>Fläche ist frei · Ruhemessung starten</button><button id="activate" class="primary" hidden>Zum Schalten freigeben</button><button id="keep" hidden>Fertig</button><button id="retry" hidden>Erneut einlernen</button><button id="manual-from-wizard">Parameter selbst einstellen</button><button id="cancel">Einlernen abbrechen</button></div></div>
      <div id="manual-panel" hidden><div class="notice">Schaltaktionen sind pausiert. Passe die Parameter unten an und prüfe Berühren, Halten und Loslassen am LED-Ring.</div><h3>Manuell abstimmen</h3><div class="manual-metrics" aria-live="polite"><div><span>Messwert</span><b id="manual-raw">–</b></div><div><span>Gefiltert</span><b id="manual-filtered">–</b></div><div><span>Kontakt</span><b id="manual-contact">–</b></div><div><span>Einschalten ab</span><b id="manual-threshold">–</b></div><div><span>Loslassen bis</span><b id="manual-release">–</b></div></div><p id="manual-state" class="hint"></p><p class="hint">Nach einer Wertänderung einmal vollständig loslassen. Größerer Einschaltabstand macht die Fläche weniger empfindlich. Längere Loslassentprellung überbrückt kurze Aussetzer, kann aber schnelle Doppeltipps zusammenführen.</p><div class="actions"><button id="manual-finish" class="primary">Zum Schalten freigeben</button><button id="manual-close">Schließen</button></div></div>
    </section>
    <section id="actions-section"><div class="section-head"><ha-icon icon="mdi:tune-variant"></ha-icon><div><h2>Bedienung & Aktionen</h2><p>Einmal tippen schaltet das Licht. Doppeltippen und Halten kannst du hier zuordnen.</p></div></div><div class="gesture"><strong>Einmal tippen</strong><span>Licht im Raum ein- oder ausschalten</span></div><div class="gesture"><strong>Halten</strong><span>Durch die Szenen im Raum wechseln</span></div><div id="options" class="action-editor"></div><p class="hint">Die Szenenreihenfolge gilt für alle MoSwitch-Schalter im selben Raum.</p></section>
    <div class="footer" id="assignment-footer"><span id="unsaved"></span><button id="save" class="primary" disabled>Zuordnung speichern</button></div>
    <section id="advanced-section"><details id="touch-parameters"><summary><ha-icon icon="mdi:tune"></ha-icon><span id="parameter-heading">Erweiterte Touch-Einstellungen</span></summary><div class="body"><p id="parameter-intro" class="hint">Normalerweise genügt das Einlernen. Bei Bedarf kannst du die Empfindlichkeit und Filter hier anpassen. Änderungen werden erst nach Bestätigung des Schalters angezeigt.</p><div id="parameters" class="parameters"></div><div id="manual-save" class="actions" hidden><button id="manual-apply" class="primary">Werte übernehmen</button></div><p id="manual-save-hint" class="hint" hidden>Übernehmen speichert die Werte im Schalter. Schaltaktionen bleiben bis zur Freigabe pausiert.</p></div></details></section>
    </div></main>`;
    const q = id => this.shadowRoot.getElementById(id);
    for (const [id,action] of [['learn','prepare'],['manual','manual'],['manual-from-wizard','manual'],['manual-finish','activate'],['reconnect','refresh'],['retry','prepare'],['baseline','baseline'],['cancel','cancel'],['activate','activate'],['activate-home','activate']]) q(id).onclick = () => this._command(action);
    q('keep').onclick = () => { this._learning = false; this._updateTouch(); this._finished(); };
    q('manual-close').onclick = () => this._command('manual_close');
    q('manual-apply').onclick = () => {
      const values={};
      for(const parameter of this._data.parameters){
        const input=q('p-'+parameter.key);
        if(!input.reportValidity() || input.value==='')return;
        values[parameter.key]=Number(input.value);
      }
      this._command('manual_apply',{values});
    };
    q('area').onchange = () => this._changeArea();
    q('save').onclick = () => this._save();

  }
  async _initialize(data) {
    this._initialized = true;
    const q = id => this.shadowRoot.getElementById(id);
    q('name').textContent = data.name;
    q('back').href = `/config/devices/device/${encodeURIComponent(data.device_id)}`;
    q('area-name').textContent = data.areas.find(a => a.value === data.area_id)?.label || 'Noch kein Raum';
    q('area').innerHTML = `<option value="" disabled>Raum auswählen</option>` + data.areas.map(a => `<option value="${esc(a.value)}">${esc(a.label)}</option>`).join('');
    q('area').value = data.area_id || '';
    this._area = data.area_id || '';
    this._options = structuredClone(data.options);
    this._schema = data.schema;
    this._learning = ACTIVE.includes(data.touch.state);
    q('content').hidden = false;
    this._renderLights(data);
    q('parameters').innerHTML = data.parameters.map(p => `<div class="parameter"><label for="p-${esc(p.key)}">${esc(p.label)}</label><div class="row"><input id="p-${esc(p.key)}" type="number" min="${p.min}" max="${p.max}" step="${p.step}" value="${data.touch[p.key] ?? ''}"><small>${esc(p.unit)}</small><button data-param="${esc(p.key)}" aria-label="${esc(p.label)} übernehmen">Übernehmen</button></div></div>`).join('');
    for (const input of this.shadowRoot.querySelectorAll('.parameter input')) input.oninput=()=>{this._parameterDirty.add(input.id);this._updateTouch();};
    for (const button of this.shadowRoot.querySelectorAll('[data-param]')) button.onclick = () => {
      const input = q('p-'+button.dataset.param);
      if (!input.reportValidity() || input.value === '') return;
      this._command('parameter',{key:button.dataset.param,value:Number(input.value)});
    };
    this._formReady = true;
    this._configureForm();
  }
  _entityName(id) { return this._hass.states[id]?.attributes.friendly_name || id; }
  _configureForm() {
    if (!this._formReady) return;
    const form=this.shadowRoot.getElementById('options');
    const selected=this._options;
    const entities=name=>this._schema.find(f=>f.name===name)?.selector?.entity?.include_entities || [];
    const players=entities('players');
    const scenes=entities('scene_order');
    const order=[...new Set([...(selected.scene_order||[]).filter(id=>scenes.includes(id)),...scenes])];
    const scripts=Object.keys(this._hass.states).filter(id=>id.startsWith('script.')).sort((a,b)=>this._entityName(a).localeCompare(this._entityName(b)));
    const action=selected.actions?.length===1?selected.actions[0]:null;
    const script=action && (action.action||action.service)==='script.turn_on' && typeof action.target?.entity_id==='string'?action.target.entity_id:'';
    form.innerHTML=`<label for="double">Doppeltippen</label><select id="double"><option value="media">Medienwiedergabe starten / pausieren</option><option value="custom">Eigene Aktionen ausführen</option><option value="none">Keine Aktion</option></select>
      <div id="players" class="editor-group" ${selected.double_action==='media'?'':'hidden'}><label>Mediengeräte im Raum</label>${players.length?players.map(id=>`<label class="check"><input type="checkbox" data-player="${esc(id)}" ${(selected.players||[]).includes(id)?'checked':''}><span>${esc(this._entityName(id))}</span></label>`).join(''):'<p class="hint">In diesem Raum sind keine Mediengeräte zugeordnet.</p>'}</div>
      <div id="custom" class="editor-group" ${selected.double_action==='custom'?'':'hidden'}><label for="script">Home-Assistant-Skript starten</label><select id="script"><option value="">Skript auswählen</option>${scripts.map(id=>`<option value="${esc(id)}">${esc(this._entityName(id))}</option>`).join('')}</select><p class="hint">Im Skript können Licht-, Medien- und weitere HA-Aktionen kombiniert werden.</p><details class="divider"><summary>Weitere eigene Aktionen</summary><p class="hint">Vorhandene HA-Aktionsfolgen bleiben erhalten. Hier kann die vollständige Aktionsliste als JSON bearbeitet werden.</p><label for="action-json">Aktionsliste</label><textarea id="action-json" spellcheck="false" rows="6">${esc(JSON.stringify(selected.actions||[],null,2))}</textarea><p id="action-error" class="hint" role="alert"></p></details></div>
      <div class="editor-group"><label>Szenen beim Halten</label><p class="hint">Ausgewählte Szenen werden in dieser Reihenfolge durchgeschaltet. Weitere Raumszenen folgen automatisch; das Label „MoSwitch ignorieren“ schließt Szenen aus.</p><div id="scene-list">${order.length?order.map(id=>`<div class="scene-row"><label class="check"><input type="checkbox" data-scene="${esc(id)}" ${(selected.scene_order||[]).includes(id)?'checked':''}><span>${esc(this._entityName(id))}</span></label><button class="icon-button" ${(selected.scene_order||[]).includes(id)?'':'disabled'} data-up="${esc(id)}" aria-label="${esc(this._entityName(id))} nach oben">↑</button><button class="icon-button" ${(selected.scene_order||[]).includes(id)?'':'disabled'} data-down="${esc(id)}" aria-label="${esc(this._entityName(id))} nach unten">↓</button></div>`).join(''):'<p class="hint">In diesem Raum sind noch keine Szenen zugeordnet.</p>'}</div></div>`;
    form.querySelector('#double').value=selected.double_action||'media';
    form.querySelector('#script').value=script;
    const dirty=()=>{this._dirty=true;this._setDirty();};
    form.querySelector('#double').onchange=event=>{this._options.double_action=event.target.value;dirty();this._configureForm();};
    for(const input of form.querySelectorAll('[data-player]')) input.onchange=()=>{this._options.players=Array.from(form.querySelectorAll('[data-player]:checked'),node=>node.dataset.player);dirty();};
    for(const input of form.querySelectorAll('[data-scene]')) input.onchange=()=>{this._options.scene_order=Array.from(form.querySelectorAll('[data-scene]:checked'),node=>node.dataset.scene);dirty();};
    for(const button of form.querySelectorAll('[data-up],[data-down]')) button.onclick=()=>{
      const id=button.dataset.up||button.dataset.down,index=order.indexOf(id),next=index+(button.dataset.up?-1:1);
      if(next<0||next>=order.length)return;
      [order[index],order[next]]=[order[next],order[index]];
      this._options.scene_order=order.filter(item=>(this._options.scene_order||[]).includes(item));dirty();this._configureForm();
    };
    form.querySelector('#script').onchange=event=>{if(event.target.value){this._options.actions=[{action:'script.turn_on',target:{entity_id:event.target.value}}];dirty();this._configureForm();}};
    form.querySelector('#action-json').oninput=event=>{
      try {const actions=JSON.parse(event.target.value);if(!Array.isArray(actions))throw Error();this._options.actions=actions;this._actionsInvalid=false;form.querySelector('#action-error').textContent='';dirty();}
      catch{this._actionsInvalid=true;form.querySelector('#action-error').textContent='Bitte eine gültige JSON-Liste eingeben.';this._setDirty();}
    };
  }
  _renderLights(data) {
    const node = this.shadowRoot.getElementById('lights');
    node.innerHTML = `<div class="hint">${data.lights.length ? 'Leuchten in diesem Raum' : 'Noch keine Leuchten in diesem Raum zugeordnet.'}</div>` + data.lights.map(light => `<div class="light"><ha-icon icon="mdi:lightbulb-outline"></ha-icon><span>${esc(light.name)}${light.direct?'':' · keine direkte ZHA-Leuchte'}</span></div>`).join('');
  }
  async _changeArea() {
    const generation=this._generation;
    const value = this.shadowRoot.getElementById('area').value;
    this._actionsInvalid=false;
    this._area = value;
    this._options = {...this._options,players:[],scene_order:[]};
    this._dirty = true;
    this._setDirty();
    this.shadowRoot.getElementById('area-change').hidden = value === this._data.area_id;
    try {
      const data = await this._hass.callWS({type:'moswitch/device/preview',entry_id:this._entry,area_id:value});
      if (generation !== this._generation || value !== this._area) return;
      this._schema = data.schema;
      this._renderLights(data);
      this._configureForm();
    } catch (error) { this._error(error); }
  }
  _setDirty() {
    this.shadowRoot.getElementById('save').disabled = !this._dirty || this._busy || !this._area || this._actionsInvalid;
    this.shadowRoot.getElementById('unsaved').textContent = this._dirty ? 'Noch nicht gespeichert' : '';
  }
  _updateTouch() {
    if (!this._data || !this._initialized) return;
    const q = id => this.shadowRoot.getElementById(id);
    const d = this._data, s = d.touch, state = s.state;
    const fresh = d.fresh && Date.now()-this._updatedAt < 120000;
    const active = ACTIVE.includes(state);
    const manual=Boolean(d.manual);
    const showing = this._learning || active || manual;
    const preparing=this._busy&&this._pendingAction==='prepare'&&!active;
    q('mode').className = 'pill'+(!fresh||d.quiet?' quiet':'');
    q('mode').innerHTML = `<i></i>${!fresh?'Keine aktuellen Messdaten':d.quiet?'Schaltaktionen pausiert':'Schaltbetrieb aktiv'}`;
    q('touch-summary').textContent = fresh && [1,3,4].includes(s.startup_check) ? 'Startprüfung · Fläche freigeben; gespeicherte Kalibrierung bleibt erhalten' : !fresh ? 'Verbindung prüfen oder Messwerte aktualisieren.' : d.quiet && s.actions_locked ? 'Firmware sperrt den Schaltbetrieb' : d.quiet ? 'Touch-Rückmeldung aktiv · Schaltaktionen gesperrt' : s.calibrated ? 'Eingelernt und zum Schalten freigegeben' : 'Touchfläche noch nicht eingelernt';
    q('touch-home').hidden = showing;
    q('wizard').hidden = !showing || manual;
    q('manual-panel').hidden=!manual;
    q('advanced-section').classList.toggle('manual',manual);
    q('manual-save').hidden=q('manual-save-hint').hidden=!manual;
    q('parameter-heading').textContent=manual?'Touch-Parameter':'Erweiterte Touch-Einstellungen';
    q('parameter-intro').textContent=manual?'Ändere die gewünschten Werte und übernimm sie gemeinsam. Der Schalter bestätigt die gespeicherten Einstellungen.':'Normalerweise genügt das Einlernen. Bei Bedarf kannst du die Empfindlichkeit und Filter hier anpassen. Änderungen werden erst nach Bestätigung des Schalters angezeigt.';
    if(manual)q('touch-parameters').open=true;
    q('manual-raw').textContent=s.raw_us==null?'–':`${s.raw_us} µs`;
    q('manual-filtered').textContent=s.filtered_us==null?'–':`${s.filtered_us} µs`;
    q('manual-contact').textContent=!s.calibrated?'Werte übernehmen':s.pressed?'Berührt':'Frei';
    q('manual-threshold').textContent=s.calibrated?`${s.baseline_us+s.press_delta_us} µs`:'–';
    q('manual-release').textContent=s.calibrated?`${s.baseline_us+s.release_delta_us} µs`:'–';
    q('manual-state').textContent=s.calibrated?'Gespeicherte Werte aktiv · Schaltaktionen pausiert':'Die angezeigten Startwerte zuerst übernehmen. Danach Berühren und Loslassen prüfen.';
    q('manual-finish').disabled=this._busy||!fresh||!s.calibrated||s.actions_locked||this._parameterDirty.size>0;
    q('manual-close').disabled=this._busy;
    q('manual-apply').disabled=this._busy||!fresh;
    for(const id of ['manual','manual-from-wizard'])q(id).disabled=this._busy||!fresh||!s.frontend_active;
    q('learn').disabled = this._busy;
    q('reconnect').hidden=fresh;
    q('reconnect').disabled=this._busy;
    q('activate-home').hidden = !(d.quiet && s.calibrated && !s.actions_locked && !active);
    q('activate-home').textContent=state===3?'Zum Schalten freigeben':'Mit bisherigen Werten freigeben';
    const step = [1,2,9].includes(state)?0:state===7?1:[11,12,13].includes(state)?2:3;
    q('wizard').querySelectorAll('.steps li').forEach((li,index) => {li.className=index===step?'current':index<step?'done':''; if(index===step)li.setAttribute('aria-current','step');else li.removeAttribute('aria-current');});
    let title = '', instruction = '', feedback = '';
    if (state === 9) {title='Fläche vollständig loslassen';instruction='Lass die montierte Touchfläche frei und lege die Platine stabil ab. Starte dann die Ruhemessung.';}
    else if ([1,2].includes(state)) {title='Ruhewert wird gemessen';instruction='Die Fläche etwa vier Sekunden unberührt lassen.';}
    else if (state === 7) {title='Zehnmal kurz tippen';instruction='Tippe wie im Alltag. Zwischen den Tipps den Finger vollständig wegnehmen.';feedback={1:'Bitte vollständig loslassen',2:'Bereit für den nächsten Tipp',3:'Kontakt erkannt · jetzt loslassen',4:'Tipp gezählt',5:'Kontakt zu kurz · bitte erneut tippen',6:'Kontakt zu lang · bitte kurz tippen',7:'Signal zu schwach · bitte erneut tippen',8:'Messaussetzer · bitte erneut tippen'}[s.feedback] || 'Die LED pulsiert, wenn der Schalter bereit ist.';}
    else if (state===11 && s.feedback===8) {title='Halten bitte wiederholen';instruction='Fläche vollständig loslassen. Dann erneut berühren und bis zur Aufforderung halten.';feedback='Die zehn Tipps bleiben erhalten. Der Kontakt wurde unterbrochen oder es gab zu wenig gültige Messungen.';}
    else if (state===11) {title='Jetzt berühren und halten';instruction='Erst kurz loslassen. Dann die Fläche berühren und den Finger liegen lassen, bis hier „Jetzt loslassen“ erscheint.';}
    else if (state===12) {title='Weiter halten';instruction='Finger liegen lassen. Der Schalter prüft den durchgehenden Kontakt.';feedback=`Noch ${(s.hold_remaining_ms/1000).toFixed(1)} Sekunden`;}
    else if (state===13) {title='Jetzt loslassen';instruction='Die Fläche vollständig freigeben. Anschließend speichert der Schalter die ermittelten Werte.';}
    else if (state===3 && s.actions_locked) {title='Touchfläche erfolgreich eingelernt';instruction='Die Kalibrierung ist gespeichert. Die installierte Firmware sperrt den Schaltbetrieb.';feedback='Eine Firmware mit freigebbarem Schaltbetrieb ist erforderlich.';}
    else if (state===3) {title='Touchfläche erfolgreich eingelernt';instruction='Alle Schritte sind abgeschlossen. Die Werte sind gespeichert.';feedback=d.quiet?'Mit „Fertig“ kehrst du zum Schaltbetrieb zurück.':'Der Schalter ist zum Schalten freigegeben.';}
    else {title=state===8?'Einlernen abgebrochen':'Einlernen nicht abgeschlossen';instruction=(d.message||'Bitte Verbindung und Messdaten prüfen.').replace(/\*\*/g,'');feedback=(instruction.includes('Die bisherigen Werte bleiben erhalten.')?'':'Die bisherigen Werte bleiben erhalten. ')+'Schaltaktionen bleiben im stillen Modus gesperrt.';}
    if(preparing){title='Einlernen wird vorbereitet …';instruction='Warte auf die Bestätigung vom Schalter.';feedback='';}
    if (!fresh) feedback='Keine aktuellen Gerätedaten. Bitte die Verbindung prüfen.';
    q('stage-title').textContent=title;
    q('instruction').textContent=instruction;
    q('feedback').textContent=feedback;
    q('count').hidden=state!==7;
    q('count').innerHTML=`${Number(s.learning_taps||0)} <small>/ 10</small>`;
    q('orb').className='orb'+(state===7&&!s.pressed?' pulse':'')+(s.pressed?' touch':'');
    q('baseline').hidden=state!==9;
    q('activate').hidden=preparing||!(s.calibrated&&!s.actions_locked&&d.quiet&&!active);
    q('activate').textContent=state===3?'Fertig':'Mit bisherigen Werten freigeben';
    q('keep').hidden=preparing||state!==3||d.quiet;
    q('retry').hidden=active||state===3;
    q('cancel').hidden=!active;
    for(const id of ['baseline','activate','activate-home','cancel','retry']) q(id).disabled=this._busy||(!fresh&&id!=='reconnect');
    for(const parameter of d.parameters) {
      const input=q('p-'+parameter.key);
      if (!input) continue;
      input.min=parameter.min;input.max=parameter.max;
      input.disabled=this._busy||!fresh||(manual?!s.frontend_active:(active||!(parameter.key.endsWith('_us')?s.parameters_writable:s.tuning_writable)));
      if(!this._parameterDirty.has(input.id) && this.shadowRoot.activeElement!==input) input.value=(manual?d.manual_values?.[parameter.key]:s[parameter.key])??'';
      input.parentElement.querySelector('button').disabled=input.disabled;
    }
    if(manual){
      const baseline=Number(q('p-baseline_us').value),press=Number(q('p-press_delta_us').value),release=Number(q('p-release_delta_us').value);
      q('p-baseline_us').max=Math.min(1000,1999-press);
      q('p-press_delta_us').min=release+1;q('p-press_delta_us').max=1999-baseline;
      q('p-release_delta_us').max=Math.max(1,press-1);
    }
    this.dispatchEvent(new CustomEvent('moswitch-status' ,{bubbles:true,composed:true,detail:{busy:this._busy,active}}));
  }
  async _command(action, extra={}) {
    if (this._busy) return;
    const generation=this._generation;
    this._busy=true;
    this._pendingAction=action;
    this.shadowRoot.getElementById('saved').hidden=true;
    this.shadowRoot.getElementById('error').hidden=true;
    if(action==='prepare') this._learning=true;
    this._updateTouch();this._setDirty();
    try {
      const data=await this._hass.callWS({type:'moswitch/device/action',entry_id:this._entry,session:this._session,action,...extra});
      if(generation!==this._generation)return;
      this._data=data;this._updatedAt=Date.now();
      if(action==='manual'||action==='manual_apply'){this._learning=false;this._parameterDirty.clear();}
      if(action==='manual_apply')this._success('Werte vom Schalter bestätigt und gespeichert. Bitte einmal loslassen und anschließend prüfen.');
      if(action==='manual_close'){this._learning=false;this._finished();}
      if(action==='activate'){this._learning=false;this._finished();}
      if(action==='prepare')this.shadowRoot.getElementById('wizard').scrollIntoView({behavior:'smooth',block:'start'});
      if(action==='parameter') {this._parameterDirty.delete('p-'+extra.key);this._success('Parameter vom Schalter bestätigt und gespeichert.');}
    } catch(error) { if(generation===this._generation){if(action==='prepare'&&!ACTIVE.includes(this._data.touch.state))this._learning=false;this._error(error);} }
    finally {if(generation===this._generation){this._busy=false;this._updateTouch();this._setDirty();}}
  }
  async _save() {
    if(this._busy)return;
    const generation=this._generation;
    this._busy=true;this._setDirty();
    try {
      const data=await this._hass.callWS({type:'moswitch/device/save',entry_id:this._entry,area_id:this._area,options:this._options});
      if(generation!==this._generation)return;
      this._data=data;this._dirty=false;this._options=structuredClone(data.options);this._schema=data.schema;
      this._configureForm();this._renderLights(data);
      this.shadowRoot.getElementById('area-name').textContent=data.areas.find(a=>a.value===data.area_id)?.label||'';
      this.shadowRoot.getElementById('area-change').hidden=true;
      this._success('Raum und Aktionen gespeichert.');
    } catch(error) {if(generation===this._generation)this._error(error);}
    finally {if(generation===this._generation){this._busy=false;this._setDirty();this._updateTouch();}}
  }
  _finished() {
    if(this.hasAttribute('dialog-view'))this.dispatchEvent(new CustomEvent('moswitch-finished',{bubbles:true,composed:true}));
  }
  _error(error) {
    const node=this.shadowRoot.getElementById('error');
    node.textContent=error.message||String(error);node.hidden=false;
    node.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  _success(message) {
    const node=this.shadowRoot.getElementById('saved');node.textContent=message;node.hidden=false;
    this.shadowRoot.getElementById('error').hidden=true;
  }
}
customElements.define('moswitch-device-settings',MoSwitchDeviceSettings);
