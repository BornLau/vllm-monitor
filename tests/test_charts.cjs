const {test}=require('node:test');
const assert=require('node:assert/strict');
const {History,geometry,keyOf}=require('../dashboard/charts.js');
const node=(values={},url='http://model/v1')=>({id:'n',api_base_url:url,values:{up:1,...values}});
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
test('time axis moves monotonically across a newly appended sample',()=>{
 const a=[0,3000,6000].map(time=>({time,values:{kv:50}}));
 const before=geometry(a,'kv',-6000,6000,100,7500);
 const after=geometry([...a,{time:9000,values:{kv:60}}],'kv',-6000,6000,100,7500);
 assert.ok(after.line.startsWith(before.line));
 const moved=geometry(a,'kv',-5990,6010,100,7500);
 assert.ok(Number(moved.line.match(/^M([\d.]+)/)[1])<Number(before.line.match(/^M([\d.]+)/)[1]));
});
test('history is bounded by the six-hour retention window',()=>{
 const history=new History(),n=node({kv:50});
 for(let i=0;i<7300;i++)history.record(snapshot(n),i*3000);
 assert.ok(history.entries.get(keyOf({id:'p'},n)).samples.length<=7201);
});
