const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = JSON.parse(fs.readFileSync('node-red/car-charging-auto-start.json'));
const by = Object.fromEntries(nodes.map(n => [n.id, n]));
assert.equal(new Set(nodes.map(n => n.id)).size, nodes.length);
for (const n of nodes) {
    if (n.outputs !== undefined) assert.equal(n.wires.length, n.outputs);
    for (const port of n.wires || []) for (const id of port) assert.ok(by[id]);
    if (n.type === 'function') new Function('msg', 'flow', 'node', n.func);
}
const rate = nodes.find(n => n.type === 'delay');
assert.equal(rate.nbRateUnits, '5'); assert.equal(rate.rateUnits, 'minute');
assert.equal(rate.rate, '1'); assert.equal(rate.drop, true);
assert.equal(by.tesla_save_session.overwriteFile, 'true');
assert.deepEqual(by.tesla_start_policy.wires, [['tesla_save_session']]);
assert.deepEqual(by.tesla_save_session.wires, [['tesla_verify_saved']]);
assert.deepEqual(by.tesla_verify_saved.wires, [['tesla_verify_state']]);
const verify = new Function('msg', 'node', by.tesla_verify_state.func);
assert.equal(verify({payload:'broken', expectedState:'saved'}, {error(){}}), null);
assert.ok(verify({payload:'saved', expectedState:'saved'}, {error(){}}));
const decide = new Function('msg', 'flow', 'node', by.tesla_start_policy.func);
function run({soc=51, solar=0, handled=false, status='Stopped', latch='Engaged', age=0, timestamp, session}={}) {
    const payload = {cacheMtime: Date.now()/1000-age,
        charge: {charging_state: status, charge_port_latch: latch, timestamp},
        session: session === undefined ? {handled} : session};
    return decide({payload}, {get: () => ({BatterySOC:soc, PVPower:solar})}, {status(){}, error(){}});
}
assert.equal(run().startRequested, true);
assert.equal(run({soc:50}), null);
assert.equal(run({soc:20, solar:1601}).startRequested, true);
for (const options of [{soc:19,solar:2000},{soc:20,solar:1600},{soc:null},{soc:NaN},{solar:-1},
    {handled:true},{status:'NoPower'},{latch:'Disengaged'},{age:901},{timestamp:1},{session:{}},{session:{handled:'false'}}]) assert.equal(run(options),null);
for (const status of ['Charging','Complete']) {
    const result = run({status}); assert.equal(result.startRequested,false);
    assert.equal(JSON.parse(result.payload).handled,true);
}
const reset=run({status:'Disconnected',handled:true});
assert.equal(reset.startRequested,false); assert.equal(JSON.parse(reset.payload).handled,false);
const attempt=JSON.parse(run().payload);
assert.equal(attempt.handled,true);
assert.equal(run({session:attempt}),null); // restart/failure: disk state prevents retry
assert.equal(run({session:null}).startRequested,true);
console.log('Node-RED policy, persistence ordering, wiring and 5-minute limiter checks passed');
