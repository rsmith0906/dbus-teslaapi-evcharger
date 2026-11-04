const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = JSON.parse(fs.readFileSync('node-red/sunny-unplugged-alert.json'));
const by = Object.fromEntries(nodes.map(n => [n.id, n]));
assert.equal(new Set(nodes.map(n => n.id)).size, nodes.length);
for (const n of nodes) {
    if (n.outputs !== undefined) assert.equal(n.wires.length, n.outputs);
    for (const port of n.wires || []) for (const id of port) assert.ok(by[id]);
    if (n.type === 'function') new Function('msg', 'flow', 'node', 'context', n.func);
}
// The Victron input node reads pathObj.enum for enum paths; without it Node-RED crashes on the first value.
for (const n of nodes) if (n.pathObj?.type === 'enum') assert.equal(n.pathObj.enum['0'], 'Disconnected');
assert.equal(by.sunny_tick.repeat, '300');
assert.deepEqual(by.sunny_read_cache.wires, [['sunny_keep_cache']]);
assert.deepEqual(by.sunny_keep_cache.wires, [['sunny_read_home']]);
assert.deepEqual(by.sunny_read_home.wires, [['sunny_rules']]);
assert.deepEqual(by.sunny_rules.wires, [['sunny_push']]);
assert.deepEqual(by.sunny_catch.scope.sort(), ['sunny_read_cache', 'sunny_read_home']);
assert.deepEqual(by.sunny_file_error.wires, [['sunny_rules']]);
assert.equal(by.sunny_push.config, '7c3e0c966c39354b');

const remember = new Function('msg', 'flow', by.sunny_remember.func);
const store = {};
assert.equal(remember({topic: 'PVPower', payload: 900}, {set: (k, v) => store[k] = v}), null);
assert.equal(store.PVPower, 900);

const decide = new Function('msg', 'flow', 'node', 'context', 'Date', by.sunny_rules.func);
const HOME = {latitude: 40.0, longitude: -100.0, radius_m: 200};
// 2026-10-31 is still daylight time in Chicago: UTC-5.
const central = (h, m = 0) => Date.UTC(2026, 9, 31, h + 5, m);
function run({solar = 1000, status = 0, at = central(12), car = {latitude: 40.0005, longitude: -100.0005},
              home = HOME, cache, error, memory = {}} = {}) {
    const statuses = [];
    const msg = {cache: cache ?? JSON.stringify({response: {drive_state: car}}),
                 payload: typeof home === 'string' ? home : JSON.stringify(home), error};
    const out = decide(msg, {get: k => ({PVPower: solar, EVChargerStatus: status})[k]},
        {status: s => statuses.push(s)},
        {get: k => memory[k], set: (k, v) => memory[k] = v}, {now: () => at});
    return {out, status: statuses.at(-1), memory};
}

const ok = run();
assert.equal(ok.out.topic, 'Car not plugged in');
assert.match(ok.out.payload, /1000 W/);
assert.equal(ok.memory.lastAlertAt, central(12));

for (const [options, reason] of [
    [{status: 1}, /Plugged in/], [{status: 2}, /Plugged in/], [{solar: 700}, /Not sunny/],
    [{at: central(8, 59)}, /Outside/], [{at: central(16, 0)}, /Outside/],
    [{car: {latitude: 40.1, longitude: -100.1}}, /Car away/],
    [{solar: null}, /Waiting for Victron/], [{status: null}, /Waiting for Victron/],
]) {
    const r = run(options);
    assert.equal(r.out, null, JSON.stringify(options));
    assert.match(r.status.text, reason);
}
for (const at of [central(9, 0), central(15, 59)]) assert.ok(run({at}).out);

// Problems with the files show red and never alert.
for (const options of [{car: {}}, {cache: 'not json'}, {home: '{}'}, {home: 'broken'},
                       {error: {message: 'ENOENT'}}]) {
    const r = run(options);
    assert.equal(r.out, null, JSON.stringify(options));
    assert.equal(r.status.fill, 'red');
}

// At most one alert per 30 minutes.
const memory = {};
assert.ok(run({memory, at: central(12)}).out);
assert.equal(run({memory, at: central(12, 29)}).out, null);
assert.ok(run({memory, at: central(12, 30)}).out);
console.log('Sunny unplugged alert: wiring, rules, hours, away check, file errors and 30-minute repeat passed');
