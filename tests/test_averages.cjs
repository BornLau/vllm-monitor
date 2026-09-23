const test = require('node:test'), assert = require('node:assert/strict'), vm = require('node:vm'), fs = require('node:fs');
const source = fs.readFileSync(__dirname + '/../dashboard/averages.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function page(fetch) {
  const nodes = new Map();
  const get = id => {if (!nodes.has(id)) nodes.set(id,{value:id==='average-window'?'12h':'',innerHTML:'',textContent:'',hidden:true});return nodes.get(id);};
  vm.runInNewContext(fs.readFileSync(__dirname + '/../dashboard/statistics-bars.js', 'utf8') + '\n' + source,{document:{getElementById:get},fetch,Date,encodeURIComponent}); return get;
}
test('renders 60 tok/s, active duration and coverage without treating missing as zero', async () => {
  const row = {model:'<model>',project:'service',node:'engine',output_average:60,output_active_seconds:10800,output_observed_seconds:43200,input_average:null,input_active_seconds:null,input_observed_seconds:null};
  const get = page(async()=>({ok:true,json:async()=>({end:1,rows:[row]})})); await tick();
  assert.match(get('average-rows').innerHTML, /<td>60<\/td>/);
  assert.match(get('average-rows').innerHTML, /3 小时/);
  assert.match(get('average-rows').innerHTML, /100%/);
  assert.match(get('average-rows').innerHTML, /&lt;model&gt;/);
  assert.equal(get('average-error').hidden,false);
});
test('failed query does not display a zero average', async () => {
  const get = page(async()=>({ok:false})); await tick();
  assert.match(get('average-rows').innerHTML,/暂无可用平均值/);
  assert.equal(get('average-refresh').disabled,false);
});
