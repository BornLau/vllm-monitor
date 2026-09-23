const {test}=require('node:test');
const assert=require('node:assert/strict');
const {History,geometry,gapLimit,keyOf}=require('../dashboard/charts.js');
const node=(values={},url='http://model/v1')=>({id:'n',activity:'active',api_base_url:url,values:{up:1,...values}});
const snapshot=n=>({projects:[{id:'p',nodes:[n]}]});
test('real values only, cached snapshots deduplicate, URLs isolate history',()=>{
 const history=new History(),n=node({output_tps:10,ttft:null});
 history.record(snapshot(n),1000);history.record(snapshot(n),1000);
 const entry=history.entries.get(keyOf({id:'p'},n));
 assert.equal(entry.samples.length,1);assert.equal(entry.samples[0].values.ttft,null);
 const changed=node({output_tps:20},'http://other/v1');history.record(snapshot(changed),4000);
 assert.equal(history.entries.size,1);assert.equal(history.entries.get(keyOf({id:'p'},changed)).samples.length,1);
 history.record({projects:[]},7000);assert.equal(history.entries.size,0);
});
test('scrape failure records missing values, not stale metrics',()=>{
 const history=new History(),n=node({up:0,output_tps:100});history.record(snapshot(n),1000);
 assert.equal(history.entries.get(keyOf({id:'p'},n)).samples[0].values.output_tps,null);
});
test('missing values and long polling gaps break lines and filled regions',()=>{
 const samples=[0,3000,6000,9000,12000,24000].map(time=>({time,values:{kv:time===6000?null:50}}));
 const result=geometry(samples,'kv',0,25000,100,7500);
 assert.equal((result.line.match(/M/g)||[]).length,3);
 assert.equal((result.area.match(/Z/g)||[]).length,2);
});
test('gap threshold allows normal collection latency without hiding a stalled poller',()=>{
 assert.equal(gapLimit(3000),12000);
 const normal=[0,3000,11000].map(time=>({time,values:{kv:50}}));
 const stalled=[0,3000,16000].map(time=>({time,values:{kv:50}}));
 assert.equal((geometry(normal,'kv',0,20000,100,gapLimit(3000)).line.match(/M/g)||[]).length,1);
 assert.equal((geometry(stalled,'kv',0,20000,100,gapLimit(3000)).line.match(/M/g)||[]).length,2);
});
test('time axis moves monotonically across a newly appended sample',()=>{
 const a=[0,3000,6000].map(time=>({time,values:{kv:50}}));
 const before=geometry(a,'kv',-6000,6000,100,7500);
 const after=geometry([...a,{time:9000,values:{kv:60}}],'kv',-6000,6000,100,7500);
 assert.ok(after.line.startsWith(before.line));
 const moved=geometry(a,'kv',-5990,6010,100,7500);
 assert.ok(Number(moved.line.match(/^M([\d.]+)/)[1])<Number(before.line.match(/^M([\d.]+)/)[1]));
});
test('history is bounded by the 24-hour retention window',()=>{
 const history=new History(),n=node({kv:50});
 for(let i=0;i<28900;i++)history.record(snapshot(n),i*3000);
 assert.ok(history.entries.get(keyOf({id:'p'},n)).samples.length<=28801);
});

test('persisted history merges in time order without overwriting live samples or bridging missing data',()=>{
 const history=new History(),n=node({output_tps:30});
 history.record(snapshot(n),9000);
 const item={project_id:'p',node:n,samples:[{time:3000,values:{output_tps:10}},{time:6000,values:{output_tps:null}},{time:9000,values:{output_tps:20}}]};
 history.merge({step_ms:3000,entries:[item]});
 history.merge({step_ms:3000,entries:[item]});
 const points=history.entries.get(keyOf({id:'p'},n)).samples;
 assert.deepEqual(points.map(p=>p.time),[3000,6000,9000]);
 assert.equal(points.at(-1).values.output_tps,30);
 assert.equal((geometry(points,'output_tps',0,10000,40,12000).line.match(/M/g)||[]).length,2);
});

test('idle curves stay continuous, gauges survive unknown activity, failures stay missing',()=>{
 const history=new History(),n=node({output_tps:12,ttft:2,waiting:0,kv:30});
 history.record(snapshot(n),3000);
 n.activity='idle';n.values.output_tps=null;n.values.ttft=null;
 history.record(snapshot(n),6000);
 n.activity='active';n.values.output_tps=15;n.values.ttft=3;
 history.record(snapshot(n),9000);
 const samples=history.entries.get(keyOf({id:'p'},n)).samples;
 assert.deepEqual(samples[1].values,{output_tps:0,ttft:0,waiting:0,kv:30});
 for(const metric of ['output_tps','ttft','waiting','kv'])
   assert.equal((geometry(samples,metric,0,10000,100,12000).line.match(/M/g)||[]).length,1);
 n.activity='unknown';history.record(snapshot(n),12000);
 assert.deepEqual(samples[3].values,{output_tps:null,ttft:null,waiting:0,kv:30});
 n.activity='idle';n.values.up=0;history.record(snapshot(n),15000);
 assert.ok(Object.values(samples[4].values).every(v=>v===null));
 n.values.up=1;n.values.kv=null;history.record(snapshot(n),18000);
 assert.equal(samples[5].values.kv,null);
});

test('offset historical nulls cannot split healthy live samples',()=>{
 const history=new History(),n=node({output_tps:20,ttft:1,waiting:0,kv:30});
 history.record(snapshot(n),4100);history.record(snapshot(n),7100);history.record(snapshot(n),10100);
 history.merge({step_ms:3000,entries:[{project_id:'p',node:n,samples:[1000,4000,7000,10000].map(time=>({time,values:{output_tps:time===1000?10:null}}))}]});
 const points=history.entries.get(keyOf({id:'p'},n)).samples;
 assert.deepEqual(points.map(p=>p.time),[1000,4000,4100,7100,10100]);
 assert.equal(points.filter(p=>p.time>4100&&p.values.output_tps===null).length,0);
 const result=geometry(points,'output_tps',4100,10100,30,12000);
 assert.equal((result.line.match(/M/g)||[]).length,1);
});

test('missing observations have dashed references without fabricated solid samples or fills',()=>{
 const points=[{time:0,values:{ttft:2}},{time:3000,values:{ttft:null}},{time:6000,values:{ttft:4}},{time:9000,values:{ttft:null}}];
 const result=geometry(points,'ttft',0,12000,10,12000);
 assert.equal((result.line.match(/M/g)||[]).length,2);
 assert.equal(result.area,'');
 assert.equal(result.reference,'M0.000,74.000 L100.000,58.000 M100.000,58.000 L200.000,58.000');
 assert.equal(points[1].values.ttft,null);
 assert.equal(geometry([{time:0,values:{ttft:null}}],'ttft',0,12000,10,12000).reference,'');
});
