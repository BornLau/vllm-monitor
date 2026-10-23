const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../dashboard/index.html'),'utf8');
const js=html.split('<script>')[1].split('</script>')[0];
const context=vm.createContext({MonitorCharts:require('../dashboard/charts.js')});
vm.runInContext(js.slice(js.indexOf('    const esc'),js.indexOf('    function renderModels()')),context);
function row(message,findings=[['ok','指标正常','']]) {
 context.p={id:'p',name:'<script>bad</script>',nodes:[{}]};
 context.n={id:'n',api_base_url:'http://localhost/v1',api:{state:'error',message},values:{up:1,output_tps:10,kv:null},findings};
 return vm.runInContext('modelRow(p,n)',context);
}
test('404 discovery is quiet and metrics still have four chart panels',()=>{
 const markup=row('HTTP 404');
 assert.match(markup,/pill muted/);assert.match(markup,/\/v1\/models 探测受限/);
 assert.equal((markup.match(/class="trend-panel"/g)||[]).length,4);
 assert.equal(vm.runInContext('needsAttention(n)',context),false);
 assert.ok(!markup.includes('<script>bad</script>'));assert.ok(markup.includes('&lt;script&gt;'));
});
test('auth failures and critical performance retain severity',()=>{
 assert.match(row('HTTP 401'),/pill critical/);
 assert.match(row('HTTP 404',[['critical','拥堵','等待过多']]),/pill critical/);
});
test('decode speed identifies request TPOT P95 and excludes TTFT',()=>{
 const markup=row('HTTP 404');
 assert.match(markup,/解码速度/);
 assert.match(markup,/class="input-speed-row"/);
 assert.match(markup,/输入速度/);
 assert.match(markup,/请求级 TPOT P95/);
 assert.match(markup,/请求总耗时 − 首 Token 时间/);
 assert.doesNotMatch(markup,/Token 间隔 P50|当前输出吞吐/);
});

test('snapshot patch preserves the chart-owned plot and hover overlay',()=>{
 context.oldPlot={nodeType:1,tagName:'DIV',hasAttribute:name=>name==='data-chart-plot'};
 context.freshPlot={nodeType:1,tagName:'DIV'};
 // Accessing attributes/children would patch away the dynamically created crosshair.
 Object.defineProperty(context.oldPlot,'attributes',{get(){throw Error('chart plot must not be patched');}});
 vm.runInContext('patchNode(oldPlot,freshPlot)',context);
});

test('group tabs include all and ungrouped, combine filters, and recover after group removal',()=>{
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{value:id==='status-filter'?'all':id==='sort'?'name':'',innerHTML:'',setAttribute(){},querySelector:()=>({focus(){}})});return elements.get(id);};
 const ctx=vm.createContext({MonitorCharts:{...require('../dashboard/charts.js'),observe(){}},document:{getElementById:get}});
 vm.runInContext("const $=id=>document.getElementById(id);"+js.slice(js.indexOf('    const esc'),js.indexOf('    function render()')),ctx);
 ctx.fixture={projects:[{id:'a',name:'A',group:'生产',nodes:[{id:'n',api:{state:'ok'}}]},{id:'b',name:'B',group:'测试',nodes:[{id:'n',api:{state:'error'}}]},{id:'c',name:'C',nodes:[{id:'n',api:{state:'ok'}}]}]};
 vm.runInContext('data=fixture;showRows=rows=>globalThis.shown=rows;renderModels()',ctx);
 assert.equal(ctx.shown.length,3);
 assert.match(get('model-groups').innerHTML,/全部/);
 assert.match(get('model-groups').innerHTML,/未分组/);
 vm.runInContext("selectedGroup='生产';renderModels()",ctx);
 assert.equal(ctx.shown.length,1);assert.equal(ctx.shown[0][0].id,'a');
 get('search').value='B';vm.runInContext('renderModels()',ctx);
 assert.match(get('models').innerHTML,/没有匹配/);
 get('search').value='';vm.runInContext("selectedGroup=null;renderModels()",ctx);
 get('status-filter').value='online';vm.runInContext('renderModels()',ctx);
 assert.equal(ctx.shown.length,2);
 vm.runInContext("selectedGroup='生产';data.projects=data.projects.filter(p=>p.id!=='a');renderModels()",ctx);
 assert.equal(vm.runInContext('selectedGroup',ctx),null);
 assert.equal(ctx.shown[0][0].id,'c');
});
