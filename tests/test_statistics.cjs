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
const payload = {dates:['2026-09-22'],end: 1000, totals: {requests: 12, total_tokens: 300, input_tokens: 100, output_tokens: 200}, rows: [
  {daily:[{date:'2026-09-22',requests:12,total_tokens:300,waiting:2}],model: '<model>', project: 'Service', node: 'engine', requests: 12, total_tokens: 300, input_tokens: 100, output_tokens: 200}]};
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
test('daily charts preserve date order and queue gaps', () => {
  const ctx = {};
  vm.runInNewContext(fs.readFileSync(__dirname + '/../dashboard/statistics-bars.js','utf8'), ctx);
  const rows=[{model:'A',project:'P',node:'N',daily:[{date:'2026-09-20',requests:2,waiting:1},{date:'2026-09-21',requests:null,waiting:null},{date:'2026-09-22',requests:4,waiting:3}]}];
  const dates=['2026-09-20','2026-09-21','2026-09-22'];
  const bars=ctx.dailyUsageChart(rows,dates,'requests','请求','次',String,String);
  assert.ok(bars.indexOf('09-20')<bars.indexOf('09-22'));
  assert.match(bars,/数据缺失，不按零统计/);
  const line=ctx.dailyQueueChart(rows,dates,String,String);
  assert.match(line,/d="M[^L"]+M[^L"]+"/);
});
test('date handles filter all daily charts and recompute totals', async () => {
  const dates=['2026-09-20','2026-09-21','2026-09-22'];
  const row={model:'A',project:'P',node:'N',requests:60,input_tokens:600,output_tokens:300,total_tokens:900,daily:dates.map((date,i)=>({date,requests:10*(i+1),input_tokens:100*(i+1),output_tokens:50*(i+1),total_tokens:150*(i+1),waiting:i}))};
  const get=page(async()=>({ok:true,json:async()=>({dates,end:1,rows:[row],totals:row})}));await tick();
  get('rows').onkeydown({target:{closest:()=>({dataset:{brush:'start'}})},key:'ArrowRight',preventDefault(){}});
  assert.equal(get('requests').textContent,'50');
  assert.equal(get('total_tokens').textContent,'750');
  assert.match(get('rows').innerHTML,/date-brush/);
  get('rows').onkeydown({target:{closest:()=>({dataset:{brush:'end'}})},key:'ArrowLeft',preventDefault(){}});
  assert.equal(get('requests').textContent,'20');
  assert.doesNotMatch(get('rows').innerHTML,/重置区间/);
});

test('brush panning preserves span and clamps both boundaries',()=>{
  const ctx={};vm.runInNewContext(fs.readFileSync(__dirname+'/../dashboard/statistics-bars.js','utf8'),ctx);
  const range=(...args)=>Array.from(ctx.brushRange(...args));
  assert.deepEqual(range(3,8,100,'pan',30),[24,29]);
  assert.deepEqual(range(3,8,-100,'pan',30),[0,5]);
  assert.deepEqual(range(3,8,100,'start',30),[8,8]);
  assert.deepEqual(range(0,0,5,'pan',1),[0,0]);
  const chart=ctx.dailyQueueChart([],Array.from({length:30},(_,i)=>'2026-09-'+String(i+1).padStart(2,'0')),String,String,600);
  assert.match(chart,/viewBox="0 0 600 260"/);
  assert.doesNotMatch(chart,/min-width/);
});
