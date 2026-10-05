import assert from 'node:assert/strict';
import {ReplayState} from '../aetherlink_scout/static/state.mjs';
const data = {duration:10,tail_seconds:35,stale_seconds:30,records:[
  {key:'a',t:0,serial:null,aircraft:{lat:35,lon:-115,altitude:100},operator:null,speed:5},
  {key:'a',t:2,serial:'A',aircraft:null,operator:{lat:35.001,lon:-115.001,altitude:null},speed:null},
  {key:'b',t:5,serial:'B',aircraft:{lat:36,lon:-116,altitude:200},operator:null},
  {key:'a',t:10,serial:'A',aircraft:{lat:35.01,lon:-115.01,altitude:null},operator:null}
]};
const state=new ReplayState(data);
assert.equal(state.advance(0),1);
assert.equal(state.advance(2),0);
assert.equal(state.tracks.size,1);
assert.equal(state.tracks.get('a').fields.serial,'A');
assert.equal(state.tracks.get('a').fields.speed,5);
assert.equal(state.advance(10),1);
assert.equal(state.alerts.length,2);
assert.equal(state.tracks.get('a').fields.aircraft.altitude,100);
assert.equal(state.tracks.get('a').fieldTimes.aircraft_altitude,0);
assert.equal(state.tracks.get('a').trail.length,2);
state.advance(33);
assert.equal(state.stale(state.tracks.get('a')),false);
assert.equal(state.stale(state.tracks.get('a'),'operator'),true);
state.advance(45);
assert.equal(state.stale(state.tracks.get('a')),true);
state.seek(1);
assert.equal(state.tracks.size,1);
assert.equal(state.alerts.length,0);
assert.equal(state.advance(2),0);
assert.equal(state.advance(5),1);
state.reset();
assert.equal(state.advance(0),1);
console.log('PASS: per-track alerts, delayed identity, partial-field ages, trail, expiry, silent seek, restart');
