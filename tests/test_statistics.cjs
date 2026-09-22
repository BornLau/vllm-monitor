const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/../dashboard/statistics.html', 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const tick = () => new Promise(resolve => setImmediate(resolve));
function page(fetch) {
  const nodes = new Map();
  const get = id => {if (!nodes.has(id)) nodes.set(id, {value: id === 'window' ? '30d' : '', textContent: '', innerHTML: '', hidden: true}); return nodes.get(id);};
  vm.runInNewContext(fs.readFileSync(__dirname + '/../dashboard/statistics-bars.js', 'utf8') + '\n' + source, {document: {getElementById: get, querySelectorAll: () => []}, fetch, Date, encodeURIComponent});
  return get;
}
const payload = {end: 1000, totals: {requests: 12, total_tokens: 300, input_tokens: 100, output_tokens: 200}, rows: [
  {model: '<model>', project: 'Service', node: 'engine', requests: 12, total_tokens: 300, input_tokens: 100, output_tokens: 200}]};
test('statistics renders totals, escapes names and filters only table', async () => {
  const get = page(async () => ({ok: true, json: async () => payload}));
  await tick();
  assert.equal(get('requests').textContent, '12');
  assert.equal(get('total_tokens').textContent, '300');
  assert.match(get('rows').innerHTML, /&lt;model&gt;/);
  get('search').value = 'missing'; get('search').oninput();
  assert.match(get('rows').innerHTML, /没有匹配/);
  assert.equal(get('requests').textContent, '12');
});
test('failed refresh removes old totals and reports error', async () => {
  let ok = true;
  const get = page(async () => ({ok, json: async () => payload}));
  await tick(); ok = false;
  await get('refresh').onclick();
  assert.equal(get('requests').textContent, '—');
  assert.equal(get('error').hidden, false);
  assert.equal(get('refresh').disabled, false);
});
test('range changes ignore responses from previous range', async () => {
  const pending = [];
  const get = page(url => new Promise(resolve => pending.push({url, resolve})));
  get('window').value = '7d'; const second = get('window').onchange();
  assert.match(pending[1].url, /7d$/);
  pending[1].resolve({ok:true,json:async()=>payload}); await second;
  pending[0].resolve({ok:true,json:async()=>({...payload, totals:{requests:999}})}); await tick();
  assert.equal(get('requests').textContent, '12');
});
