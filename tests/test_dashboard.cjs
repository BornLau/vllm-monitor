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
