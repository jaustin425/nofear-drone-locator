import {ReplayState} from './state.mjs';
import {LiveState} from './live-state.mjs';
import {DetectionTone} from './detection-tone.mjs';
import {validPosition, freshPosition, fixedLocation, usableOrigin, drivingURL} from './location.mjs';

const $ = id => document.getElementById(id);
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
};
const duration = seconds => `${Math.floor(seconds/60).toString().padStart(2,'0')}:${Math.floor(seconds%60).toString().padStart(2,'0')}`;
const coordinate = p => p ? `${p.lat.toFixed(7)}, ${p.lon.toFixed(7)}` : 'Not reported';
const numeric = (n, unit='') => n === undefined || n === null ? 'Not reported' : `${Number(n).toFixed(1)}${unit}`;
let replay, map, selected, running=false, sound=false, audio, lastTick, fitted=false, fittedTrackCount=0;
let streaming=false, simulation=false, eventSource=null, controlBusy=false, soundRequested=true;
let pendingStartSession=null;
const detectionTone=new DetectionTone();
const layers = new Map();
let tiles=null;
let myLocation=null, myMarker=null, accuracyCircle=null, geoWatch=null, locationError='', addressEnabled=false, addressProvider='configured geocoder';
const addresses=new Map();
const manualLocationKey='aetherlink-scout.manual-location.v1';
let historySelection=null, historyPage={items:[],total:0,offset:0,next_offset:null}, historyBusy=false;
let historyLayers=[];
const savedClock=timestamp=>new Date(timestamp*1000).toISOString().replace('T',' ').slice(0,19)+' UTC';

function leaveHistory() {
  for (const layer of historyLayers) map.removeLayer(layer);
  historyLayers=[]; historySelection=null;
  $('return-live').hidden=true;
}

function renderHistory() {
  $('history-list').replaceChildren();
  for (const item of historyPage.items) {
    const button=node('button',undefined,`history-card ${historySelection?.id===item.id?'selected':''}`);
    button.append(node('strong',item.serial),node('small',savedClock(item.observed_at)),
      node('small',`${item.mode==='live_stream'?'Saved live report':'Saved simulation'} · ${item.operator_reported?'operator position':'operator not reported'} · ${item.aircraft_reported?'aircraft position':'aircraft not reported'}`));
    button.addEventListener('click',async()=>{
      try {
        const response=await fetch(`/api/history/${encodeURIComponent(item.id)}`);
        if (!response.ok) throw new Error();
        const entry=await response.json();
        leaveHistory(); historySelection=entry; selected=null;
        $('return-live').hidden=false;
        for (const kind of ['aircraft','operator']) {
          const position=entry.track.fields[kind];
          if (!position) continue;
          const icon=L.divIcon({className:`marker ${kind} history`,html:kind==='aircraft'?'<span>✦</span>':'<span>▣</span>',iconSize:[26,26],iconAnchor:[13,13]});
          const box=node('div');
          box.append(node('strong',`Saved ${kind} · historical snapshot`),node('div',coordinate(position)),node('div',savedClock(entry.observed_at)));
          historyLayers.push(L.marker([position.lat,position.lon],{icon}).addTo(map).bindPopup(box));
        }
        const position=entry.track.fields.operator || entry.track.fields.aircraft;
        if (position) map.setView([position.lat,position.lon],16);
        renderHistory(); render();
      } catch { $('history-status').textContent='Saved report unavailable; refresh History.'; }
    });
    $('history-list').append(button);
  }
  $('history-newer').disabled=historyBusy || historyPage.offset===0;
  $('history-older').disabled=historyBusy || historyPage.next_offset===null;
}

async function refreshHistory(offset=historyPage.offset) {
  if (historyBusy) return;
  historyBusy=true;
  try {
    const response=await fetch(`/api/history?offset=${offset}`);
    if (!response.ok) throw new Error();
    historyPage=await response.json();
    $('history-status').textContent=historyPage.total?`Showing ${historyPage.offset+1}–${historyPage.offset+historyPage.items.length} of ${historyPage.total} saved tracks/sessions`:'No saved reports yet.';
  } catch { $('history-status').textContent='Local history unavailable; detection remains usable.'; }
  finally {historyBusy=false; renderHistory();}
}

function setupHistoryActions() {
  $('refresh-history').addEventListener('click',()=>refreshHistory());
  $('history-newer').addEventListener('click',()=>refreshHistory(Math.max(0,historyPage.offset-100)));
  $('history-older').addEventListener('click',()=>refreshHistory(historyPage.next_offset));
  $('return-live').textContent=streaming?'Return to live view':'Return to replay';
  $('return-live').addEventListener('click',()=>{leaveHistory();renderHistory();render();fitTracks();});
  $('clear-map').addEventListener('click',async()=>{
    if (!streaming || !replay.connected || controlBusy) return;
    controlBusy=true; renderReceiver();
    try {
      const response=await fetch('/api/map/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({only_stale:true})});
      if (!response.ok) throw new Error();
      const frame=await response.json();
      leaveHistory(); applyFrame(frame);
      $('clear-map-status').textContent=`${frame.cleared_tracks} tracks cleared · history retained · fresh reports may reappear`;
      await refreshHistory(0);
    } catch { $('clear-map-status').textContent='Map could not be cleared; check local history/connection.'; }
    finally {controlBusy=false; renderReceiver();}
  });
  refreshHistory(0);
  setInterval(()=>{if (!historySelection && historyPage.offset===0) refreshHistory(0);},5000);
}

function saveManualLocation(position) {
  try {
    localStorage.setItem(manualLocationKey,JSON.stringify(position?{lat:position.lat,lon:position.lon}:null));
  } catch {
    locationError='Browser storage unavailable; this change cannot be remembered after refresh.';
    renderMyLocation();
  }
}

async function restoreMyLocation() {
  try {
    const saved=localStorage.getItem(manualLocationKey);
    if (saved!==null) {
      const value=JSON.parse(saved);
      if (value===null) return; // Clear also suppresses the testing preset on refresh.
      const position=fixedLocation(value);
      if (position) {setMyLocation(position); return;}
    }
  } catch { /* Optional browser storage must not prevent detection. */ }
  try {
    const response=await fetch('/static/local-location.json',{cache:'no-store'});
    if (!response.ok) return;
    const position=fixedLocation(await response.json(),'Testing preset');
    if (position) setMyLocation(position);
  } catch { /* An absent/invalid local preset leaves location unset. */ }
}

function stopLocationWatch() {
  if (geoWatch!==null) navigator.geolocation?.clearWatch(geoWatch);
  geoWatch=null; $('locate').textContent='Use browser location';
}

function renderMyLocation() {
  $('center-me').disabled=!myLocation;
  $('my-location-kind').textContent=myLocation?.fixed?'FIXED':myLocation?'CURRENT':'NOT SET';
  if (!myLocation) {
    $('my-location-status').textContent=locationError || (streaming?'Not set · separate from reported positions':'Not set · separate from replay');
    return;
  }
  const age=Math.max(0,Math.floor((Date.now()-myLocation.updated)/1000));
  const stale=!myLocation.fixed && !freshPosition(myLocation);
  const accuracy=myLocation.accuracy===null?'accuracy unknown':`reported accuracy ±${Math.round(myLocation.accuracy)} m`;
  const freshness=myLocation.fixed?'fixed manual location · not GPS':`${age}s old${stale?' (stale)':''}`;
  $('my-location-status').textContent=`${coordinate(myLocation)} · ${myLocation.source} · ${accuracy} · ${freshness}${locationError?' · '+locationError:''}`;
  if (myMarker) myMarker.setOpacity(stale ? .4 : 1);
}

function setMyLocation(position) {
  if (!validPosition(position.lat,position.lon)) { locationError='Invalid location coordinates.'; renderMyLocation(); return; }
  myLocation=position; locationError='';
  $('my-latitude').value=position.lat;
  $('my-longitude').value=position.lon;
  const icon=L.divIcon({className:'marker me',html:'<span>●</span>',iconSize:[26,26],iconAnchor:[13,13]});
  if (!myMarker) myMarker=L.marker([position.lat,position.lon],{icon}).addTo(map);
  else myMarker.setLatLng([position.lat,position.lon]);
  const box=node('div');
  box.append(node('strong',position.fixed?'My location · fixed manual point · not GPS':'My location · current'),node('div',coordinate(position)),node('div',position.source));
  if (position.accuracy!==null) box.append(node('div',`Reported accuracy: ±${Math.round(position.accuracy)} m`));
  myMarker.bindPopup(box);
  if (accuracyCircle) {map.removeLayer(accuracyCircle); accuracyCircle=null;}
  if (position.accuracy!==null) accuracyCircle=L.circle([position.lat,position.lon],{radius:position.accuracy,color:'#78a9ff',fillOpacity:.08,weight:1}).addTo(map);
  renderMyLocation();
}

function operatorTarget() {
  const track=historySelection?.track || replay.tracks.get(selected), p=track?.fields.operator;
  if (!p) return null;
  return {track,p,key:`${p.lat},${p.lon}`};
}

function renderOperatorActions() {
  const target=operatorTarget();
  $('lookup-address').disabled=!target || !addressEnabled;
  $('directions').disabled=!target;
  $('address-provider-status').textContent=addressEnabled?`Lookup sends this position only when requested to ${addressProvider}.`:'Address lookup is disabled until a local or approved provider is configured.';
  const cached=target ? addresses.get(target.key) : null;
  $('operator-address').textContent=!target?'Select a reported operator position.':cached?.loading?'Looking up this reported position…':cached?.error || cached?.address || (cached?'No mapped address found.':'Nearby address not looked up.');
  $('address-attribution').replaceChildren();
  if (cached?.provider==='Geoapify') {
    const provider=node('a','Powered by Geoapify'); provider.href='https://www.geoapify.com/'; provider.target='_blank'; provider.rel='noopener noreferrer';
    const osm=node('a','© OpenStreetMap contributors'); osm.href='https://www.openstreetmap.org/copyright'; osm.target='_blank'; osm.rel='noopener noreferrer';
    $('address-attribution').append(provider,node('span',' · '),osm);
  } else $('address-attribution').textContent=cached?.attribution || '';
  if (cached?.loading) $('lookup-address').disabled=true;
}

function setupLocationActions() {
  $('locate').addEventListener('click',()=>{
    if (geoWatch!==null) {stopLocationWatch(); return;}
    if (!navigator.geolocation) {locationError='Browser location is unavailable; enter coordinates manually.'; renderMyLocation(); return;}
    locationError='Requesting location permission…'; renderMyLocation();
    geoWatch=navigator.geolocation.watchPosition(position=>{
      const c=position.coords;
      setMyLocation({lat:c.latitude,lon:c.longitude,accuracy:Number.isFinite(c.accuracy)&&c.accuracy>=0?c.accuracy:null,
        updated:Number.isFinite(position.timestamp)?position.timestamp:Date.now(),source:'Browser estimate'});
    },error=>{
      stopLocationWatch();
      locationError=error.code===1?'Location permission denied; use manual coordinates.':'Location fix unavailable; use manual coordinates.';
      renderMyLocation();
    },{enableHighAccuracy:true,maximumAge:5000,timeout:15000});
    $('locate').textContent='Stop browser location';
  });
  $('manual-location').addEventListener('submit',event=>{
    event.preventDefault();
    const lat=Number($('my-latitude').value),lon=Number($('my-longitude').value);
    if (!$('my-latitude').value.trim() || !$('my-longitude').value.trim() || !validPosition(lat,lon)) {
      locationError='Enter valid latitude and longitude.'; renderMyLocation(); return;
    }
    stopLocationWatch(); const position=fixedLocation({lat,lon}); setMyLocation(position); saveManualLocation(position);
  });
  $('clear-location').addEventListener('click',()=>{
    stopLocationWatch(); if (myMarker) map.removeLayer(myMarker); if (accuracyCircle) map.removeLayer(accuracyCircle);
    myMarker=null; accuracyCircle=null; myLocation=null; locationError=''; renderMyLocation();
    $('my-latitude').value=''; $('my-longitude').value=''; saveManualLocation(null);
  });
  $('center-me').addEventListener('click',()=>{if (myLocation) map.setView([myLocation.lat,myLocation.lon],16);});
  $('lookup-address').addEventListener('click',async()=>{
    const target=operatorTarget(); if (!target || !addressEnabled) return;
    running=false; render();
    if (!window.confirm(`Look up the nearest mapped address for this ${streaming && !simulation && !historySelection?'reported':'HISTORICAL reported'} position? Coordinates will be sent to ${addressProvider}.`)) return;
    addresses.set(target.key,{loading:true}); renderOperatorActions();
    try {
      const response=await fetch('/api/address',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:target.p.lat,longitude:target.p.lon})});
      if (!response.ok) throw new Error();
      const result=await response.json(); addresses.set(target.key,result);
      if (addresses.size>128) addresses.delete(addresses.keys().next().value);
    } catch { addresses.set(target.key,{error:'Address provider unavailable; coordinates remain usable.'}); }
    renderOperatorActions();
  });
  $('directions').addEventListener('click',()=>{
    const target=operatorTarget(); if (!target) return;
    running=false; render();
    const origin=usableOrigin(myLocation)?myLocation:null;
    const date=historySelection?savedClock(historySelection.start_timestamp+target.track.fieldTimes.operator):captureClock(target.track.fieldTimes.operator);
    if (!window.confirm(`This reported destination is ${streaming && !simulation && !historySelection?'an unverified reported position':'HISTORICAL'} (reported ${date}), not a verified controller position. Open driving directions in Google Maps? This shares the destination${origin?(origin.fixed?' and your fixed manual origin':' and your current origin'):'; Google Maps will ask for a starting location'}.`)) return;
    window.open(drivingURL(target.p,origin),'_blank','noopener,noreferrer');
  });
  renderMyLocation();
  setInterval(renderMyLocation,1000);
}

function popup(track, kind) {
  const box = node('div');
  box.append(node('strong',kind === 'aircraft' ? `Aircraft · ${observationLabel()}` : `Reported operator · ${observationLabel()}`));
  box.append(node('div',track.fields.serial || 'Identity not yet reported'));
  box.append(node('div',coordinate(track.fields[kind])));
  box.append(node('div',`Position age: ${Math.floor(replay.age(track,kind))}s ${streaming?'since received':'in replay'}`));
  if (kind === 'operator') box.append(node('div','Reported location; controller/person location unverified.'));
  return box;
}

function createMap(data) {
  map = L.map('map', {zoomControl:true, maxZoom:19});
  const first = data.records.find(r => r.aircraft || r.operator);
  const pos = first?.operator || first?.aircraft;
  map.setView(pos ? [pos.lat,pos.lon] : [0,0], pos ? 16 : 2);
  const Grid = L.GridLayer.extend({createTile(coords) {
    const tile = node('div',undefined,'coordinate-grid');
    const center = map.unproject(L.point(coords.x*256+128,coords.y*256+128),coords.z);
    tile.append(node('span', `${center.lat.toFixed(4)}°, ${center.lng.toFixed(4)}°`));
    return tile;
  }});
  new Grid({attribution:'Offline coordinate grid · reported positions unverified'}).addTo(map);
  $('tiles').addEventListener('click', () => {
    if (tiles) {
      map.removeLayer(tiles); tiles=null;
      $('tiles').textContent='Enable street map'; $('tiles').setAttribute('aria-pressed','false');
      $('map-status').textContent='Offline coordinate map';
    } else {
      tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(map);
      tiles.on('tileerror', () => { $('map-status').textContent='Street tiles unavailable · coordinates still work'; });
      $('tiles').textContent='Disable street map'; $('tiles').setAttribute('aria-pressed','true');
      $('map-status').textContent='Online street map · visible area requested from OSM';
    }
  });
}

function selectTrack(key, pan=true) {
  leaveHistory(); renderHistory();
  selected=key;
  const track=replay.tracks.get(key);
  const p=track?.fields.operator || track?.fields.aircraft;
  if (pan && p) map.panTo([p.lat,p.lon]);
  render();
}

function renderMap() {
  for (const [key, group] of layers) {
    if (!replay.tracks.has(key)) { for (const layer of Object.values(group)) map.removeLayer(layer); layers.delete(key); }
  }
  for (const track of replay.tracks.values()) {
    let group=layers.get(track.key);
    if (!group) { group={}; layers.set(track.key,group); }
    for (const kind of ['aircraft','operator']) {
      const p=track.fields[kind];
      if (!p) continue;
      const stale=replay.stale(track,kind);
      const icon = L.divIcon({className:`marker ${kind} ${stale?'is-stale':''}`, html:kind==='aircraft'?'<span>✦</span>':'<span>▣</span>',iconSize:[26,26],iconAnchor:[13,13]});
      if (!group[kind]) {
        group[kind]=L.marker([p.lat,p.lon],{icon}).addTo(map);
        group[kind].on('click', () => selectTrack(track.key,false));
      } else { group[kind].setLatLng([p.lat,p.lon]); group[kind].setIcon(icon); }
      group[kind].bindPopup(popup(track,kind));
    }
    if (track.trail.length > 1) {
      if (!group.trail) group.trail=L.polyline(track.trail,{color:'#5edacd',weight:2,opacity:.7}).addTo(map);
      else group.trail.setLatLngs(track.trail);
      group.trail.setStyle({opacity:replay.stale(track,'aircraft') ? .25 : .7});
    }
  }
}

function fitTracks() {
  const points=[];
  for (const track of replay.tracks.values()) for (const kind of ['aircraft','operator']) {
    const p=track.fields[kind]; if (p) points.push([p.lat,p.lon]);
  }
  if (points.length) map.fitBounds(points,{paddingTopLeft:[55,145],paddingBottomRight:[55,80],maxZoom:17});
}

function renderTracks() {
  $('tracks').replaceChildren();
  $('track-count').textContent=replay.tracks.size;
  for (const track of [...replay.tracks.values()].sort((a,b)=>b.lastSeen-a.lastSeen)) {
    const stale=replay.stale(track);
    const card=node('button',undefined,`track-card ${selected===track.key?'selected':''}`);
    card.dataset.key=track.key;
    const top=node('div',undefined,'card-top');
    top.append(node('span',track.fields.serial || 'Unidentified transmitter','card-title'));
    top.append(node('span',stale?'STALE':'OBSERVED',stale?'stale-status':'live-status'));
    card.append(top,node('p',`${numeric(track.fields.speed,' m/s')} · ${Math.floor(replay.age(track))}s since report`));
    const locations=[track.fields.aircraft?'Aircraft position':'Aircraft position unknown',track.fields.operator?'operator reported':'operator unknown'];
    card.append(node('p',locations.join(' · ')));
    card.addEventListener('click',()=>selectTrack(track.key));
    $('tracks').append(card);
  }
}

function renderDetails() {
  const track=historySelection?.track || replay.tracks.get(selected);
  document.querySelector('.right-panel .badge').textContent=historySelection?'SAVED':streaming?(simulation?'SIMULATION':'LIVE'):'REPLAY';
  if (!track) {
    $('details').replaceChildren(node('p','Select a report to inspect its reported operator position and aircraft details.','empty'));
    $('aircraft-details').replaceChildren(); return;
  }
  const fields=track.fields;
  let fragment=document.createDocumentFragment();
  fragment.append(node('h3',fields.serial || 'Unidentified transmitter','detail-name'));
  if (Array.isArray(fields.sources)) {
    const names={wifi6:'Panda · Wi-Fi 6',wifi149:'Techkey · Wi-Fi 149',ble:'Legacy Bluetooth'};
    fragment.append(node('p',`Observed by: ${fields.sources.map(id=>names[id] || id).join(' / ')}`,'detail-description'));
  }
  if (fields.identity_conflict) fragment.append(node('p','Reported identity or position conflict · observations kept separate','error'));
  fragment.append(node('p',`${historySelection?'Saved historical observation · '+savedClock(historySelection.observed_at):observationLabel()} · source fields unverified`,'detail-description'));
  const trackClock=t=>historySelection?savedClock(historySelection.start_timestamp+t):captureClock(t);
  const section = (title, values) => {
    fragment.append(node('h3',title,'detail-section'));
    const dl=node('dl');
    for (const [label,value] of values) {
      const row=node('div',undefined,'detail-row'); row.append(node('dt',label),node('dd',value)); dl.append(row);
    }
    fragment.append(dl);
  };
  const aged = (field, value) => {
    if (!(field in track.fieldTimes)) return 'Not reported';
    if (historySelection) return `${value} · reported ${trackClock(track.fieldTimes[field])}`;
    return `${value} · ${Math.floor(replay.age(track,field))}s old${replay.stale(track,field)?' (stale)':''}`;
  };
  const operatorTypes={'0':'Takeoff location','1':'Dynamic location','2':'Fixed location'};
  section('Reported operator',[
    ['Coordinates',aged('operator',coordinate(fields.operator))],
    ['Altitude (HAE)',aged('operator_altitude',numeric(fields.operator?.altitude,' m'))],
    ['Location type',operatorTypes[fields.operator_location_type] ?? 'Unknown / not reported'],
    ['Operator ID',fields.operator_id || 'Not reported']
  ]);
  const operatorFragment=fragment;
  fragment=document.createDocumentFragment();
  section('Aircraft',[
    ['Coordinates',aged('aircraft',coordinate(fields.aircraft))],
    ['Altitude (HAE)',aged('aircraft_altitude',numeric(fields.aircraft?.altitude,' m'))],
    ['Speed',aged('speed',numeric(fields.speed,' m/s'))],
    ['Heading',aged('heading',numeric(fields.heading,'°'))],
    ['Height',aged('height',numeric(fields.height,' m'))],
    ['Height ref. code',fields.height_type ?? 'Not reported'],
    ['Horiz. accuracy code',fields.horizontal_accuracy_code ?? 'Not reported'],
    ['Vert. accuracy code',fields.vertical_accuracy_code ?? 'Not reported']
  ]);
  section('Reception & timing',[
    ['Source',fields.radio || 'Unknown'],['Channel',fields.channel ?? 'Not reported'],
    ['RSSI',aged('rssi',numeric(fields.rssi,' dBm'))], ['Transmitter MAC',fields.mac || 'Not reported'],
    ['First observed',trackClock(track.firstSeen)],['Last observed',trackClock(track.lastSeen)],
    ['Track age',historySelection?'Saved snapshot; not a current detection':`${Math.floor(replay.age(track))}s ${streaming?'since received':'in replay'}`],
    ...(streaming && fields.source_timestamp ? [['Source packet time', new Date(fields.source_timestamp*1000).toISOString()]] : []),['TAK output',replay.status?.tak_output?.message || 'Disabled']
  ]);
  fragment.append(node('p','HAE is reported geodetic altitude, not height above ground. Accuracy codes are source enumerations, not metres. RSSI is signal strength, not distance.','muted small'));
  $('details').replaceChildren(operatorFragment);
  $('aircraft-details').replaceChildren(fragment);
}

function observationLabel() { return streaming?(simulation?'Historical input simulation':'Live reported observation'):'Historical replay'; }

function captureClock(t) { return new Date((replay.data.start_timestamp+t)*1000).toISOString().replace('T',' ').slice(0,19)+' UTC'; }

async function setSound(enabled) {
  sound=enabled;
  if (!sound || audio?.state!=='running') detectionTone.stop();
  if (sound) {
    try { audio ||= new (window.AudioContext || window.webkitAudioContext)(); await audio.resume(); sound=audio.state==='running'; }
    catch { sound=false; }
  }
  $('sound').textContent=sound?'Sound on':streaming?'Enable sound':'Sound off';
  $('sound').setAttribute('aria-pressed',String(sound));
  if (streaming) $('audio-status').textContent=sound?'Audible alerts armed · 4-second tone sequence per new track':'Audio not armed · click Enable sound or START';
}

function renderReceiver() {
  const status=replay.status;
  if (!status) return;
  const active=status.detection_active || ['waiting','running'].includes(status.state);
  $('start-detection').disabled=controlBusy || active || !replay.connected;
  $('stop-detection').disabled=controlBusy || !active || !replay.connected;
  $('clear-map').disabled=controlBusy || !replay.connected || replay.tracks.size===0;
  $('receiver-status').dataset.state=replay.connected?status.state:'error';
  const age=status.last_report_age===null?'No Remote ID reports yet':`${Math.floor(replay.age({lastSeen:replay.serverTime-status.last_report_age}))}s since last Remote ID report`;
  $('receiver-status').textContent=replay.connected?`${status.message} · ${age}`:'Dashboard connection lost · receiver state unknown; positions continue aging';
  $('health-summary').textContent=status.health?.message || 'Receiver health unavailable';
  $('health-summary').dataset.state=status.health?.state || 'unknown';
  const tak=status.tak_output;
  $('tak-status').textContent=tak?`${tak.mode==='local'?'LOCAL CoT':'TAK TLS'}: ${tak.message} · ${tak.sent} writes · ${tak.errors} errors`:'TAK output disabled';
  $('mode-description').textContent=simulation?'Recorded observations · no radio capture · TAK disabled':`${status.combined_enabled?'Wi-Fi 6 + Wi-Fi 149 + legacy BLE':'Explicit START/STOP capture'} · ${tak?(tak.mode==='local'?'LOCAL CoT only':'Optional TAK TLS enabled'):'TAK disabled'}`;
  const inputs=simulation?`${status.counts.packets} simulated inputs`:status.combined_enabled?
    `${status.counts.packets} Wi-Fi packets / BLE RID candidates`:status.ble_enabled?
    `${status.ble_service_updates} BLE service updates · ${status.counts.packets} RID candidate advertisements`:
    `${status.counts.packets} packets`;
  $('receiver-counts').textContent=`${status.counts.decoded} decoded · ${status.counts.errors} decode errors · ${inputs}${status.continuous_capture?` · continuous · ${status.capture_renewals ?? Math.max(0,status.capture_segments-1)} capture renewals`:''}${status.evicted_tracks?` · ${status.evicted_tracks} older tracks removed`:''}`;
  const packetFrequency=status.last_frequency_mhz?`last packet ${status.last_frequency_mhz} MHz`:'packet frequency not verified yet';
  const radio=status.last_verified_radio;
  $('radio-status').textContent=simulation?'Recorded input; no live radio':status.combined_enabled?
    'Combined detector · fixed Wi-Fi 6 + Wi-Fi 149 + legacy BLE':status.ble_enabled?
    `${status.ble_adapter} · ${status.ble_coverage} · ${status.ble_service_updates} service-data updates`:
    status.scan_enabled?
    `Scan 6 (3s) / 149 (1s) · ${radio?`last verified channel ${radio.channel}, ${Math.floor(radio.age_seconds)}s ago`:'radio not verified yet'} · ${packetFrequency}`:
    status.expected_channel?`Expected channel ${status.expected_channel} (${status.expected_frequency_mhz} MHz) · ${packetFrequency}`:'Packet frequency unavailable';
  $('source-status').replaceChildren();
  for (const source of status.sources || []) {
    const input=source.ble_enabled?`${source.ble_service_updates} BLE service updates · ${source.counts.packets} RID candidates · legacy only`:
      `channel ${source.expected_channel} · ${source.counts.packets} packets · ${source.last_frequency_mhz?`last packet ${source.last_frequency_mhz} MHz`:'packet frequency unverified'}`;
    $('source-status').append(node('p',`${source.label}: ${source.state} · ${input} · ${source.counts.decoded} decoded · ${source.counts.errors} decode errors · ${Math.max(0,source.capture_segments-1-(source.capture_recoveries||0))} renewals${source.recovery_enabled?` · ${source.capture_recoveries} USB recoveries · ${source.recovery_attempts} attempts · ${source.message}`:''}`));
  }
  if (status.history_error) $('history-status').textContent='History could not be saved; live detection continues. Map clearing is blocked until saving works.';
  if (sound && audio?.state!=='running') $('audio-status').textContent='Browser audio suspended · click Sound on to re-arm';
}

function applyFrame(frame) {
  selected=frame.status.merge_aliases?.[selected] || selected;
  const previous=replay.session;
  const count=replay.apply(frame,{notifySnapshot:pendingStartSession!==null && frame.status.session_id!==pendingStartSession});
  if (previous!==replay.session || ['stopped','error'].includes(replay.status?.state)) detectionTone.stop();
  if (previous!==replay.session) {selected=null; fitted=false;}
  if (count) beep();
  render();
}

function setupReceiver() {
  document.body.classList.add('stream-mode');
  $('receiver-panel').hidden=false;
  document.querySelector('#receiver-panel .detection-buttons').after($('sound'));
  $('mode-badge').textContent=simulation?'STREAM SIMULATION':'LIVE DETECTION';
  $('mode-description').textContent=simulation?'Recorded observations at timed arrival · no radio capture · TAK disabled':'Explicit START/STOP capture · TAK output disabled';
  if (replay.status?.combined_enabled) $('mode-description').textContent='One START/STOP · Wi-Fi 6 + Wi-Fi 149 + legacy BLE · TAK disabled';
  $('mode-title').textContent=simulation?'Timed simulation':'Live receiver';
  document.querySelector('.right-panel .badge').textContent=simulation?'SIMULATION':'LIVE';
  document.querySelector('#alert-toast strong').textContent=simulation?'Simulation detection':'Drone report detected';
  document.querySelector('.alerts-panel > p').textContent='One alert per new transmitter track per detection session. Refresh/reconnect restores tracks silently.';
  document.querySelector('.sidebar-footer strong').textContent=simulation?'Recording simulation':'Dedicated capture radio';
  if (replay.status?.combined_enabled) document.querySelector('.sidebar-footer strong').textContent='Three receiver inputs';
  document.querySelector('.sidebar-footer p').textContent=simulation?'Wi-Fi interfaces remain unchanged.':'Monitor setup and restoration stay in the launch terminal.';
  document.querySelector('.operator-actions > p').textContent=`${simulation?'Simulation destinations are historical. ':''}An address is a nearby mapped place, not a verified controller/person location.`;
  document.querySelector('.clock > span').textContent='RECEPTION SESSION · UTC';
  $('sound').textContent='Enable sound';
  $('audio-status').textContent='START arms audible alerts; refreshing requires a new click.';
  async function control(action) {
    if (controlBusy) return;
    controlBusy=true; $('control-error').textContent=''; renderReceiver();
    try {
      if (action==='stop') detectionTone.stop();
      if (action==='start' && soundRequested) await setSound(true);
      if (action==='start') pendingStartSession=replay.session;
      const response=await fetch(`/api/detection/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if (!response.ok) {
        const failure=await response.json();
        throw new Error(failure.detail || 'Detection control failed.');
      }
      applyFrame(await response.json());
    } catch (error) {
      $('control-error').textContent=error instanceof TypeError?'Dashboard connection unavailable; receiver state unknown.':error.message;
    } finally {pendingStartSession=null; controlBusy=false; renderReceiver();}
  }
  $('start-detection').addEventListener('click',()=>control('start'));
  $('stop-detection').addEventListener('click',()=>control('stop'));
  eventSource=new EventSource('/api/events');
  eventSource.addEventListener('receiver', event=>{
    try {applyFrame(JSON.parse(event.data));}
    catch {replay.connected=false; $('control-error').textContent='Observation stream could not be read.'; renderReceiver();}
  });
  eventSource.onerror=()=>{replay.connected=false; renderReceiver();};
  window.addEventListener('beforeunload',()=>eventSource.close());
}

function renderAlerts() {
  $('alerts').replaceChildren();
  for (const alert of replay.alerts.slice(0,10)) {
    const item=node('div',alert.label,'log-item');
    item.append(node('span',`${duration(alert.t)} · ${streaming?(simulation?'simulation detection':'detection'):'replay detection'}${alert.acknowledged?' · acknowledged':''}`));
    $('alerts').append(item);
  }
  if (!replay.alerts.length) $('alerts').append(node('p',streaming?'No detections in this session yet.':'No replay detections yet.','muted small'));
  const pending=replay.alerts.find(a=>!a.acknowledged);
  $('alert-toast').hidden=!pending;
  if (pending) $('alert-text').textContent=pending.label;
}

function beep() {
  if (!sound || !audio || audio.state !== 'running' || streaming && !['running','waiting','renewing','recovering'].includes(replay.status?.state)) return;
  detectionTone.play(audio);
}

function render() {
  if (!historySelection && (!selected || !replay.tracks.has(selected))) selected=replay.tracks.keys().next().value;
  renderTracks(); renderMap(); renderDetails(); renderAlerts();
  renderOperatorActions();
  if (streaming) {
    renderReceiver();
    $('clock').textContent=captureClock(replay.time);
    $('phase').textContent=simulation?'Timed historical input simulation · no radio capture':'Continuous local reception';
    $('elapsed').textContent=`Session ${duration(replay.time)}`;
  } else {
  $('seek').value=replay.time;
  $('elapsed').textContent=`${duration(replay.time)} / ${duration(replay.data.duration+replay.data.tail_seconds)}`;
  $('clock').textContent=captureClock(replay.time);
  $('phase').textContent=replay.time>replay.data.duration?'Post-capture · observing track expiry':running?'Playing recorded observations':replay.time?'Paused / complete':'Ready · historical capture';
  $('play').textContent=running?'Pause':'Play replay';
  }
  if ((!fitted || fittedTrackCount!==replay.tracks.size) && replay.tracks.size) {
    fitTracks(); fitted=true; fittedTrackCount=replay.tracks.size;
  }
}

async function start() {
  const health=await (await fetch('/api/health')).json();
  streaming=['stream_simulation','live_stream'].includes(health.mode);
  simulation=health.mode==='stream_simulation';
  const response=await fetch(streaming?'/api/receiver':'/api/replay');
  if (!response.ok) throw new Error('Observations could not be loaded.');
  const initial=await response.json();
  const data=streaming?initial.metadata:initial;
  replay=streaming?new LiveState(data):new ReplayState(data);
  if (streaming) replay.apply(initial);
  if (typeof L==='undefined') throw new Error('Local map assets are missing.');
  createMap(data);
  addressEnabled=Boolean(health.address_lookup_enabled);
  addressProvider=health.address_provider || 'configured geocoder';
  await restoreMyLocation();
  if (myLocation && !data.records.some(record=>record.operator || record.aircraft)) map.setView([myLocation.lat,myLocation.lon],16);
  setupLocationActions();
  setupHistoryActions();
  $('source').textContent=data.source;
  if (!streaming) $('source').title=`${data.counts.decoded} decoded reports · ${data.counts.errors} decode errors · ${data.counts.unidentified} unidentified`;
  $('loading').hidden=true;
  $('seek').max=data.duration+data.tail_seconds;
  $('play').addEventListener('click',()=>{
    if (replay.time>=data.duration+data.tail_seconds) { replay.reset(); fitted=false; }
    running=!running; lastTick=performance.now();
    if (!running) detectionTone.stop();
    if (running) { if (replay.advance(replay.time)) beep(); }
    render();
  });
  $('restart').addEventListener('click',()=>{detectionTone.stop(); running=false; replay.reset(); selected=null; fitted=false; render();});
  $('seek').addEventListener('input',()=>{detectionTone.stop(); running=false; replay.seek(Number($('seek').value)); selected=null; fitted=false; render();});
  $('fit').addEventListener('click',fitTracks);
  $('acknowledge').addEventListener('click',()=>{detectionTone.stop(); for (const alert of replay.alerts) alert.acknowledged=true; renderAlerts();});
  $('sound').addEventListener('click',async()=>{
    if (streaming && sound && audio?.state!=='running') {await setSound(true); return;}
    soundRequested=!sound;
    await setSound(!sound);
  });
  if (streaming) setupReceiver();
  render();
  setInterval(()=>{
    const now=performance.now();
    if (streaming) {replay.tick(); render(); return;}
    if (!running) {lastTick=now; return;}
    const delta=(now-lastTick)/1000*Number($('speed').value); lastTick=now;
    if (replay.advance(replay.time+delta)) beep();
    if (replay.time>=data.duration+data.tail_seconds) running=false;
    render();
  },100);
}
start().catch(()=>{
  $('loading').textContent='Unable to load observations. Check the local server and installed map assets.';
  $('loading').className='error';
  $('play').disabled=true;
});
