// Checks the auto-start tab exported from the Pi (docs/11-plug-detection-and-auto-start.md).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = JSON.parse(fs.readFileSync('node-red/car-charging-auto-start.json'));
const by = Object.fromEntries(nodes.map(n => [n.id, n]));
const named = name => { const n = nodes.find(x => x.name === name); assert.ok(n, name); return n; };

// Structure: unique ids, wires point at real nodes, function code parses.
assert.equal(new Set(nodes.map(n => n.id)).size, nodes.length);
for (const n of nodes) {
    if (n.outputs !== undefined) assert.equal(n.wires.length, n.outputs);
    for (const port of n.wires || []) for (const id of port) assert.ok(by[id], `${n.id} -> ${id}`);
    if (n.type === 'function') new Function('msg', 'flow', 'node', n.func);
}

const rate = nodes.find(n => n.type === 'delay');
assert.equal(rate.nbRateUnits, '5'); assert.equal(rate.rateUnits, 'minute');
assert.equal(rate.rate, '1'); assert.equal(rate.drop, true);

// The attempt is saved and read back before the start command runs.
const rules = named('Start rules: >50% OR >=20% + >700W');
const save = named('Save attempt BEFORE command');
const readBack = named('Read back saved session');
const verifyNode = named('Require matching saved state');
const startCmd = named('Wake, set 5 A, then start');
const failed = named('Rearm/pause after failed start');
assert.equal(save.overwriteFile, 'true');
assert.deepEqual(rules.wires, [[save.id]]);
assert.deepEqual(save.wires, [[readBack.id]]);
assert.deepEqual(readBack.wires, [[verifyNode.id]]);
assert.ok(startCmd.wires[1].includes(failed.id), 'stderr goes to the failed-start node');
const verify = new Function('msg', 'node', verifyNode.func);
assert.equal(verify({payload: 'broken', expectedState: 'saved'}, {error() {}}), null);
assert.ok(verify({payload: 'saved', expectedState: 'saved'}, {error() {}}));

// Start rules, driven by the Victron charger status.
const decide = new Function('msg', 'flow', 'node', rules.func);
const now = Date.now() / 1000;
function run({soc = 51, solar = 0, status = 1, session = {}, limit} = {}) {
    const payload = {session, charge_limit: limit};
    return decide({payload}, {get: () => ({BatterySOC: soc, PVPower: solar, EVChargerStatus: status})},
                  {status() {}, error() {}});
}
assert.equal(run().startRequested, true);                       // battery > 50%
assert.equal(run({soc: 20, solar: 701}).startRequested, true);  // battery >= 20% and solar > 700 W
for (const options of [{soc: 50}, {soc: 19, solar: 2000}, {soc: 20, solar: 700}, {soc: null}, {soc: NaN},
                       {solar: -1}, {status: 2}, {status: 3}, {status: 10}, {status: null},
                       {session: {retry_after: now + 60}}, {session: []}]) {
    assert.equal(run(options), null, JSON.stringify(options));
}
assert.equal(run({session: {retry_after: now - 1}}).startRequested, true);  // pause over
assert.equal(run({status: 3, limit: 60}).startRequested, true);            // full below 80%: start raises limit
const attempt = JSON.parse(run().payload);
assert.equal(attempt.reason, 'Started recently');
assert.equal(Math.round(attempt.retry_after - attempt.attempted_at), 30 * 60);
const cleared = run({status: 0, session: attempt});                         // unplug clears the pause
assert.equal(cleared.startRequested, false); assert.equal(cleared.payload, '{}');
assert.equal(run({status: 0, session: {}}), null);

// Failed start: pause length depends on the error.
const fail = new Function('msg', 'node', failed.func);
for (const [err, minutes, reason] of [
    ['car could not execute command: disconnected', 30, 'Car said unplugged'],
    ['vehicle is offline or asleep', 30, 'Car did not wake'],
    ['context deadline exceeded', 10, 'Car timed out'],
    ['car could not execute command: complete', 10, 'Car is full'],
    ['Resource temporarily unavailable', 30, 'Start failed']]) {
    const [pause, log] = fail({payload: err}, {status() {}});
    const saved = JSON.parse(pause.payload);
    assert.equal(saved.reason, reason);
    assert.equal(Math.round(saved.retry_after - saved.attempted_at), minutes * 60, err);
    assert.match(log.payload, new RegExp(reason));
}
assert.equal(fail({payload: '  '}, {status() {}}), null);
console.log('Node-RED auto-start rules, failed-start pauses, wiring and 5-minute limiter checks passed');
