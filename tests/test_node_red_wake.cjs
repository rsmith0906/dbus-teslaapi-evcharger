// Checks node-red/hourly-wake.json (docs/11-plug-detection-and-auto-start.md).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = JSON.parse(fs.readFileSync('node-red/hourly-wake.json'));
const by = Object.fromEntries(nodes.map(n => [n.id, n]));
assert.equal(new Set(nodes.map(n => n.id)).size, nodes.length);
for (const n of nodes) {
    if (n.outputs !== undefined) assert.equal(n.wires.length, n.outputs);
    for (const port of n.wires || []) for (const id of port) assert.ok(by[id], `${n.id} -> ${id}`);
    if (n.type === 'function') new Function('msg', 'flow', 'node', 'context', n.func);
}
// The Victron input node reads pathObj.enum for enum paths; without it Node-RED crashes on the first value.
for (const n of nodes) if (n.pathObj?.type === 'enum') assert.equal(n.pathObj.enum['0'], 'Disconnected');
const inputs = Object.fromEntries(nodes.filter(n => n.type.startsWith('victron-input')).map(n => [n.name, n]));
assert.deepEqual(Object.keys(inputs).sort(), ['BatterySOC', 'EVChargerStatus', 'InverterPower', 'PVPower']);
assert.equal(inputs.InverterPower.service, 'com.victronenergy.inverter/41');
assert.equal(inputs.InverterPower.path, '/Ac/Out/L1/P');
for (const n of Object.values(inputs)) assert.deepEqual(n.wires, [['wake_remember']]);

assert.equal(by.wake_tick.repeat, '300');
assert.deepEqual(by.wake_read_cache.wires, [['wake_keep_cache']]);
assert.deepEqual(by.wake_keep_cache.wires, [['wake_read_home']]);
assert.deepEqual(by.wake_read_home.wires, [['wake_rules']]);
assert.deepEqual(by.wake_rules.wires, [['wake_exec']]);
assert.equal(by.wake_exec.command, 'python /data/tesla/node-red-tesla.py wake');
assert.ok(by.wake_exec.wires[1].includes('wake_log_line'), 'stderr is logged');
assert.equal(by.wake_log.filename, '/data/tesla/auto-start-errors.log');
assert.equal(by.wake_log.overwriteFile, 'false');
assert.deepEqual(by.wake_catch.scope.sort(), ['wake_read_cache', 'wake_read_home']);
assert.deepEqual(by.wake_file_error.wires, [['wake_rules']]);

// Battery and solar thresholds must equal the auto-start tab's start rules.
const startRules = JSON.parse(fs.readFileSync('node-red/car-charging-auto-start.json'))
    .find(n => n.name && n.name.startsWith('Start rules')).func;
const constant = (code, name) => code.match(new RegExp(`const ${name} = ([0-9]+)`))[1];
for (const name of ['BATTERY_ONLY_SOC', 'SOLAR_MIN_SOC', 'SOLAR_WATTS'])
    assert.equal(constant(by.wake_rules.func, name), constant(startRules, name), name);

const logLine = new Function('msg', by.wake_log_line.func);
assert.equal(logLine({payload: '  '}), null);
assert.match(logLine({payload: 'Node-RED Tesla wake failed:\n token'}).payload, / Hourly wake failed: Node-RED Tesla wake failed: token$/);

const decide = new Function('msg', 'flow', 'node', 'context', 'Date', by.wake_rules.func);
const HOME = {latitude: 40.0, longitude: -100.0, radius_m: 200};
// 2026-11-12 is standard time in Chicago: UTC-6.
const central = (h, m = 0) => Date.UTC(2026, 10, 12, h + 6, m);
function run({soc = 60, solar = 0, status = 0, inverter = 0, at = central(12),
              car = {latitude: 40.0005, longitude: -100.0005}, home = HOME, cache, error, memory = {}} = {}) {
    const statuses = [];
    const msg = {cache: cache ?? JSON.stringify({response: {drive_state: car}}),
                 payload: typeof home === 'string' ? home : JSON.stringify(home), error};
    const readings = {BatterySOC: soc, PVPower: solar, EVChargerStatus: status, InverterPower: inverter};
    const out = decide(msg, {get: k => readings[k]}, {status: s => statuses.push(s)},
        {get: k => memory[k], set: (k, v) => memory[k] = v},
        Object.assign(function (t) { return new globalThis.Date(t); }, {now: () => at}));
    return {out, status: statuses.at(-1), memory};
}

const ok = run();
assert.ok(ok.out);
assert.equal(ok.memory.lastWakeAt, central(12));
assert.ok(run({status: 2}).out, 'stuck "Charging" with no inverter draw wakes');
assert.ok(run({soc: 20, solar: 701}).out);
assert.ok(run({inverter: 299}).out);
for (const at of [central(8, 0), central(16, 59)]) assert.ok(run({at}).out);

for (const [options, reason] of [
    [{at: central(7, 59)}, /Outside/], [{at: central(17, 0)}, /Outside/],
    [{inverter: 300}, /Car charging/], [{inverter: 1495}, /Car charging/],
    [{soc: 50}, /Not enough/], [{soc: 20, solar: 700}, /Not enough/], [{soc: 19, solar: 2000}, /Not enough/],
    [{status: 1}, /auto-start handles it/], [{status: 3}, /auto-start handles it/], [{status: 10}, /auto-start/],
    [{car: {latitude: 40.1, longitude: -100.1}}, /Car away/],
    [{soc: null}, /Waiting for Victron/], [{inverter: null}, /Waiting for Victron/], [{status: null}, /Waiting for Victron/],
]) {
    const r = run(options);
    assert.equal(r.out, null, JSON.stringify(options));
    assert.match(r.status.text, reason, JSON.stringify(options));
}

// Problems with the files show red and never wake.
for (const options of [{car: {}}, {cache: 'not json'}, {home: '{}'}, {home: 'broken'},
                       {error: {message: 'ENOENT'}}]) {
    const r = run(options);
    assert.equal(r.out, null, JSON.stringify(options));
    assert.equal(r.status.fill, 'red', JSON.stringify(options));
}

// At most one wake per 60 minutes.
const memory = {};
assert.ok(run({memory, at: central(12)}).out);
assert.equal(run({memory, at: central(12, 59)}).out, null);
assert.ok(run({memory, at: central(13, 0)}).out);
console.log('Hourly wake: wiring, thresholds match auto-start, hours, inverter, status, home, file errors and 60-minute repeat passed');
