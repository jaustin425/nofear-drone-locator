import assert from 'node:assert/strict';
import {LiveState} from '../aetherlink_scout/static/live-state.mjs';
let clock=0;
const metadata={mode:'stream_simulation',records:[],start_timestamp:0,stale_seconds:30};
const status=(elapsed=0,session_id='test')=>({session_id,elapsed,state:'running'});
const report=(sequence,t=0)=>({key:'a',sequence,t,timestamp:1,source_timestamp:1,serial:'SYNTHETIC',aircraft:{lat:35,lon:-115,altitude:100},operator:{lat:35.01,lon:-115.01,altitude:null}});
const state=new LiveState(metadata,()=>clock);
state.apply({kind:'snapshot',metadata,status:status(),sequence:0,tracks:[]});
assert.equal(state.apply({kind:'update',status:status(1),sequence:1,records:[report(1,1)]}),1);
assert.equal(state.apply({kind:'update',status:status(2),sequence:1,records:[report(1,1)]}),0);
assert.equal(state.alerts.length,1);
const retained=structuredClone([...state.tracks.values()]);
assert.equal(state.apply({kind:'snapshot',metadata,status:status(2),sequence:1,tracks:retained}),0);
assert.equal(state.alerts.length,1);
state.connected=false;clock=40;state.tick();
assert.equal(state.stale(state.tracks.get('a'),'operator'),true);
const refreshed=new LiveState(metadata,()=>clock);
assert.equal(refreshed.apply({kind:'snapshot',metadata,status:status(42),sequence:1,tracks:retained}),0);
assert.equal(refreshed.alerts.length,0);
assert.equal(refreshed.stale(refreshed.tracks.get('a')),true);
assert.equal(state.apply({kind:'snapshot',metadata,status:status(1,'new-session'),sequence:1,tracks:retained}),1);
assert.equal(state.alerts.length,1);
assert.equal(state.apply({kind:'update',status:status(2,'new-session'),sequence:2,records:[{...report(2,2),aircraft:null,operator:null}]}),0);
assert.equal(state.tracks.get('a').fieldTimes.operator,1);
assert.equal(state.apply({kind:'update',status:{...status(3,'new-session'),continuous_capture:true,capture_segments:2},sequence:3,records:[report(3,3)]}),0);
assert.equal(state.alerts.length,1);
assert.equal(state.apply({kind:'snapshot',metadata,status:{...status(4,'new-session'),map_revision:1},sequence:4,tracks:[]}),0);
assert.equal(state.tracks.size,0);
assert.equal(state.apply({kind:'update',status:{...status(5,'new-session'),map_revision:1},sequence:4,records:[]}),0);
assert.equal(state.apply({kind:'update',status:{...status(6,'new-session'),map_revision:1},sequence:5,records:[report(5,6)]}),0);
assert.equal(state.tracks.size,1);
assert.equal(state.alerts.length,1);
console.log('PASS: alerts, duplicate updates, silent reconnect/refresh, stale aging, session restart, field freshness, renewal, clear/reappearance without repeat alert');

// Late identity correlation removes provisional duplicates without another alert.
const merged=new LiveState(metadata,()=>0);
merged.apply({kind:'snapshot',metadata,status:status(),sequence:0,tracks:[]});
assert.equal(merged.apply({kind:'update',status:status(),sequence:2,records:[{...report(1),key:'p'},{...report(2),key:'q'}]}),2);
const survivor=structuredClone(merged.tracks.get('p'));
survivor.fields.sources=['ble','wifi6'];
assert.equal(merged.apply({kind:'update',status:{...status(),merge_aliases:{q:'p'}},sequence:4,records:[{key:'p',sequence:3,merge_from:['q'],merged_track:survivor},{...report(4),key:'p'}]}),0);
assert.equal(merged.tracks.size,1);
assert.equal(merged.alerts.length,1);
assert.equal(merged.alerted.has('p'),true);
assert.equal(merged.alerted.has('q'),false);
console.log('PASS: provisional correlation merges markers/alerts without another detection');

// A fresh report can land in the same-session START response snapshot before
// the first incremental event. Explicit START alerts it once; refresh is silent.
const quick=new LiveState(metadata,()=>0);
quick.apply({kind:'snapshot',metadata,status:status(0,'before'),sequence:0,tracks:[]});
quick.apply({kind:'snapshot',metadata,status:status(0,'after'),sequence:0,tracks:[]});
const fastTrack={key:'fast',firstSeen:0,lastSeen:0,fields:{serial:'AetherLinkScoutFast'},fieldTimes:{},trail:[]};
const fastFrame={kind:'snapshot',metadata,status:status(0,'after'),sequence:1,tracks:[fastTrack]};
assert.equal(quick.apply(fastFrame,{notifySnapshot:true}),1);
assert.equal(quick.apply(fastFrame,{notifySnapshot:true}),0);
assert.equal(quick.apply(fastFrame),0);
assert.equal(quick.alerts.length,1);
console.log('PASS: immediate START snapshot alerts once, duplicate snapshots/refresh stay silent');

const transition=new LiveState(metadata,()=>0);
transition.apply({kind:'snapshot',metadata,status:status(0,'old'),sequence:0,tracks:[]});
transition.apply({kind:'snapshot',metadata,status:status(0,'new'),sequence:0,tracks:[]});
const transitionFrame={...fastFrame,status:status(0,'new'),session_started:true};
assert.equal(transition.apply(transitionFrame),1);
assert.equal(transition.apply(transitionFrame),0);
const reconnect=new LiveState(metadata,()=>0);
assert.equal(reconnect.apply({...transitionFrame,session_started:false}),0);
console.log('PASS: server marks live START transition; initial/reconnected snapshots remain silent');
