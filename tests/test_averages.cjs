const test = require('node:test'), assert = require('node:assert/strict'), vm = require('node:vm'), fs = require('node:fs');
const source = fs.readFileSync(__dirname + '/../dashboard/averages.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function page(fetch) {
  const nodes = new Map();
  const get = id => {if (!nodes.has(id)) nodes.set(id,{value:id==='average-window'?'24h':'',innerHTML:'',textContent:'',hidden:true});return nodes.get(id);};
  vm.runInNewContext(fs.readFileSync(__dirname + '/../dashboard/statistics-bars.js', 'utf8') + '\n' + source,{document:{getElementById:get},fetch,Date,encodeURIComponent,window:{innerWidth:1200}}); return get;
}
test('renders two latency charts, milliseconds and dashed means', async () => {
  const row = {model:'<model>',project:'service',node:'engine',tpot:{average:.02,samples:[[1,.01],[2,null],[3,.03]]},ttft:{average:.25,samples:[[1,.2],[2,.3]]}};
  const get = page(async()=>({ok:true,json:async()=>({start:1,end:3,duration_seconds:2,rows:[row]})})); await tick();
  const html=get('average-rows').innerHTML;
  assert.match(html, /TPOT/); assert.match(html, /TTFT/);
  assert.match(html, /平均 20 ms/); assert.match(html, /平均 250 ms/);
  assert.equal((html.match(/class="average-reference"/g)||[]).length,2);
  assert.match(html, /stroke-dasharray="6 5"/);
  assert.match(html, /&lt;model&gt;/);
  assert.match(html, /class="average-axis-label" x="73"/);
  assert.match(html, /class="latency-area"/);
  assert.doesNotMatch(html, /P95|P50/);
  assert.match(html, /d="M82.00,[^" ]+ M510.00,/);
});
test('failed query does not display a zero average', async () => {
  const get = page(async()=>({ok:false})); await tick();
  assert.match(get('average-rows').innerHTML,/暂无可用平均值/);
  assert.equal(get('average-refresh').disabled,false);
});
test('missing latency does not draw a zero mean', async () => {
  const get = page(async()=>({ok:true,json:async()=>({start:1,end:10,rows:[{model:'m',project:'p',node:'n',tpot:{samples:[]},ttft:{samples:[]}}]})}));
  await tick();
  assert.match(get('average-rows').innerHTML,/暂无有效采样/);
  assert.doesNotMatch(get('average-rows').innerHTML,/class="average-reference"/);
  assert.equal(get('average-error').hidden,false);
});
test('custom range sends timestamps and rejects reversed dates', async () => {
  const urls=[];
  const get = page(async url=>{urls.push(url);return {ok:true,json:async()=>({end:200000,rows:[]})};});
  await tick();
  get('average-window').value='custom';
  get('average-start').value='2025-01-01T00:00';
  get('average-end').value='2025-01-02T00:00';
  get('average-window').onchange(); await tick();
  assert.equal(get('average-custom').hidden,false);
  assert.match(urls.at(-1),/window=custom&start=\d+&end=\d+/);
  const count=urls.length;
  get('average-end').value='2024-12-31T00:00';
  get('average-refresh').onclick(); await tick();
  assert.equal(urls.length,count);
  assert.match(get('average-error').textContent,/请选择有效/);
});

test('time ticks are whole hours and observed zero values are retained', async () => {
  const start = new Date('2026-09-24T03:17:00').getTime()/1000, end=start+86400;
  const row={model:'m',tpot:{average:0,samples:[[start,0],[end,0]]},ttft:{samples:[]}};
  const get=page(async()=>({ok:true,json:async()=>({start,end,rows:[row],duration_seconds:86400})})); await tick();
  const html=get('average-rows').innerHTML;
  const ticks=[...html.matchAll(/class="hour-tick".*?<text[^>]*>([^<]*)<\/text>/g)].map(m=>m[1]);
  assert.ok(ticks.length>=4);
  assert.ok(ticks.every(t=>/^\d{2}:00$/.test(t)));
  assert.match(html,/平均 0 ms/);
});
test('hover shows timestamp and decimal sample rather than integer axis value', async () => {
  const get=page(async()=>({ok:true,json:async()=>({start:1,end:2,rows:[]})})); await tick();
  const sample={getAttribute:()=> '120',dataset:{time:'1700000000',value:'20.876'}};
  const svg={createSVGPoint:()=>({matrixTransform:()=>({x:120,y:80})}),getScreenCTM:()=>({inverse:()=>({})}),querySelectorAll:()=>[sample],dataset:{metric:'TPOT'}};
  get('average-tooltip').style={};
  get('average-tooltip').offsetWidth=200; get('average-tooltip').offsetHeight=30;
  get('average-rows').onpointermove({target:{closest:()=>svg},clientX:100,clientY:100});
  assert.equal(get('average-tooltip').hidden,false);
  assert.match(get('average-tooltip').textContent,/TPOT 20.88 ms/);
  get('average-rows').onpointerleave();
  assert.equal(get('average-tooltip').hidden,true);
});

test('adjacent bucket means draw a smooth curve without visible scatter markers', async () => {
  const row={model:'m',tpot:{average:.02,samples:[[300,.01],[600,.03],[900,.02]]},ttft:{samples:[]}};
  const get=page(async()=>({ok:true,json:async()=>({start:0,end:900,step_seconds:300,rows:[row]})})); await tick();
  const html=get('average-rows').innerHTML;
  assert.match(html, /class="latency-line" d="M[^" ]+ C[^" ]+ [^" ]+ [^" ]+ C[^" ]+ [^" ]+ [^" ]+"/);
  assert.equal((html.match(/r="0" fill=/g)||[]).length,3);
  assert.match(get('average-status').textContent, /每 300 秒/);
});

test('only short confirmed idle gaps get interpolation and grey bands; means stay on top', async () => {
  for (const [state,end,expected] of [['idle',900,1],['missing',900,0],['idle',2400,0]]) {
    const row={model:'m',tpot:{average:.02,samples:[[300,.01],[600,null],[end,.03]],states:[[600,state]]},ttft:{samples:[]}};
    const get=page(async()=>({ok:true,json:async()=>({start:0,end,step_seconds:300,rows:[row]})})); await tick();
    const html=get('average-rows').innerHTML;
    assert.equal((html.match(/class="latency-interpolation"/g)||[]).length,expected);
    assert.equal((html.match(/class="latency-idle"/g)||[]).length,expected);
    assert.match(html,/class="average-reference"[^>]+stroke="#d04a16"/);
    assert.ok(html.indexOf('class="average-reference"')>html.indexOf('class="latency-line"'));
    assert.match(html,/平均 20 ms/);
  }
});

test('latency axis fits clustered samples while retaining mean, zeros and constant values', async () => {
  for (const [values,mean] of [[[.1,.101,.102],.101],[[.1,.101],.15],[[.1,.1],.1],[[0,0],0],[[.0001,.00011],.000105]]) {
    const row={model:'m',tpot:{average:mean,samples:values.map((v,i)=>[i+1,v])},ttft:{samples:[]}};
    const get=page(async()=>({ok:true,json:async()=>({start:0,end:4,step_seconds:1,rows:[row]})})); await tick();
    const html=get('average-rows').innerHTML;
    const [,lo,hi]=html.match(/data-metric="TPOT" data-y-min="([^"]+)" data-y-max="([^"]+)"/);
    const min=Number(lo),max=Number(hi),extent=[...values,mean].map(v=>v*1000);
    assert.ok(min>=0 && max>min);
    assert.ok(min<=Math.min(...extent) && max>=Math.max(...extent));
    if(Math.min(...extent)>=100) assert.ok(min>90);
    if(mean===.101) assert.ok(max-min<5);
    assert.doesNotMatch(html,/NaN|Infinity/);
  }
});
