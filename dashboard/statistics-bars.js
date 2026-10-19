function usageBars(rows, key, title, unit, color, format, escape, detail) {
  rows = [...rows].sort((a,b) => (b[key] ?? -1) - (a[key] ?? -1));
  const valid = rows.map(r => r[key]).filter(v => v != null && Number.isFinite(v));
  const max = Math.max(1, ...valid);
  return `<section class="chart-card"><div class="chart-title"><h3>${title}</h3><span>${unit}</span></div><div class="bar-axis"><span>0</span><span>${format(max / 2)}</span><span>${format(max)}</span></div>${rows.map(r => {
    const value = r[key], present = value != null && Number.isFinite(value);
    return `<div class="bar-row" tabindex="0" title="${escape(r.project + ' · ' + r.node + (detail ? ' · ' + detail(r) : ''))}"><div class="bar-label"><strong>${escape((r.display_model||r.model))}</strong><span>${escape(r.project)} · ${escape(r.node)}</span></div><div class="bar-track" role="img" aria-label="${escape((r.display_model||r.model))} ${title} ${present ? format(value) + ' ' + unit : '暂无数据'}"><i style="width:${present ? Math.max(0, value / max * 100) : 0}%;background:${color}"></i></div><div class="bar-value">${present ? format(value) : '—'}</div>${detail ? `<small class="bar-detail">${detail(r)}</small>` : ''}</div>`;
  }).join('')}</section>`;
}

function usageTokenBars(rows, format, escape) {
  rows = [...rows].sort((a,b) => (b.total_tokens ?? -1) - (a.total_tokens ?? -1));
  const max = Math.max(1, ...rows.map(r => r.total_tokens).filter(v => v != null && Number.isFinite(v)));
  return `<section class="chart-card"><div class="chart-title"><h3>Token 用量排行</h3><span>tokens</span></div><div class="legend"><span><i style="background:#527be9"></i>输入 Token</span><span><i style="background:#16a6a1"></i>输出 Token</span></div><div class="bar-axis"><span>0</span><span>${format(max/2)}</span><span>${format(max)}</span></div>${rows.map(r => {
    const valid = [r.input_tokens,r.output_tokens,r.total_tokens].every(v => v != null && Number.isFinite(v));
    return `<div class="bar-row" tabindex="0" title="${escape(r.project + ' · ' + r.node)} · 输入 ${format(r.input_tokens)} · 输出 ${format(r.output_tokens)}"><div class="bar-label"><strong>${escape((r.display_model||r.model))}</strong><span>${escape(r.project)} · ${escape(r.node)}</span></div><div class="bar-track" role="img" aria-label="${escape((r.display_model||r.model))} 输入 ${format(r.input_tokens)} 输出 ${format(r.output_tokens)} tokens">${valid ? `<i style="width:${r.input_tokens/max*100}%;background:#527be9"></i><i style="width:${r.output_tokens/max*100}%;background:#16a6a1"></i>` : ''}</div><div class="bar-value">${valid ? format(r.total_tokens) : '—'}</div><small class="bar-detail">输入 ${format(r.input_tokens)} · 输出 ${format(r.output_tokens)}</small></div>`;
  }).join('')}</section>`;
}

function dailyUsageChart(rows, dates, key, title, unit, format, escape, chartWidth = 800) {
  const colors = ['#527be9','#16a6a1','#a084dc','#d4a148','#d77689','#70a1c4'];
  const width = chartWidth, height = 260;
  const left=64, top=16, bottom=230, plot=width-left-16, step=plot/Math.max(1,dates.length);
  const points=dates.map(date=>rows.map(row=>row.daily?.find(day=>day.date===date)?.[key] ?? null));
  const max=Math.max(1,...points.map(values=>values.reduce((sum,v)=>sum+(v??0),0)));
  const compact=v=>v>=1e6?(v/1e6).toFixed(1)+'M':v>=1e3?(v/1e3).toFixed(1)+'k':format(v);
  let svg='';
  for(let i=0;i<=4;i++){const y=bottom-(bottom-top)*i/4;svg+=`<line x1="${left}" y1="${y}" x2="${width-16}" y2="${y}" stroke="#e9eef5"/><text x="${left-9}" y="${y+4}" text-anchor="end" fill="#8a96a8" font-size="10">${compact(max*i/4)}</text>`;}
  points.forEach((values,i)=>{
    const x=left+step*(i+.5), bw=Math.min(32,step*.64);
    const missing=values.map((v,j)=>v==null?j:-1).filter(j=>j>=0);
    let sum=0;
    values.forEach((v,j)=>{
      if(v==null)return;
      const h=v/max*(bottom-top), y=bottom-(sum+v)/max*(bottom-top);sum+=v;
      const row=rows[j], model=row.display_model||row.model;
      const day = row.daily?.find(day => day.date === dates[i]) || {};
      const heading = dates[i] + ' · ' + model;
      const metadata = row.project + (row.display_model && row.display_model !== row.model ? ' · 模型：' + row.model : '');
      const requests = format(day.requests), tokens = format(day.total_tokens);
      const tooltip = heading + '\n' + metadata + '\n请求数：' + requests + '\nTokens：' + tokens;
      svg+=`<rect tabindex="0" data-bar-tooltip="${escape(tooltip)}" data-tooltip-heading="${escape(heading)}" data-tooltip-meta="${escape(metadata)}" data-tooltip-requests="${escape(requests)}" data-tooltip-tokens="${escape(tokens)}" aria-label="${escape(tooltip)}" x="${x-bw/2}" y="${y}" width="${bw}" height="${h}" fill="${colors[j%colors.length]}"/>`;
    });
    if(missing.length===values.length){
      svg+=`<text x="${x}" y="${bottom-8}" text-anchor="middle" font-size="11" fill="#8a96a8"><title>${escape(dates[i]+'：数据缺失，不按零统计')}</title>—</text>`;
    }else if(!missing.length&&sum===0)svg+=`<text x="${x}" y="${bottom-6}" text-anchor="middle" font-size="10" fill="#8a96a8">0</text>`;
    if(i % Math.max(1,Math.ceil(dates.length/Math.max(1,(width-80)/90)))===0) svg+=`<text x="${x}" y="${bottom+21}" text-anchor="middle" font-size="10" fill="#78869a">${dates[i].slice(5)}</text>`;
  });
  return `<section class="chart-card daily-card"><div class="chart-title"><h3>${title}</h3><span>${unit} / 日</span></div><div class="legend">${rows.map((r,i)=>`<span title="${escape(r.project+' · '+r.node)}"><i style="background:${colors[i%colors.length]}"></i>${escape((r.display_model||r.model))}</span>`).join('')}</div><div class="daily-scroll"><svg role="img" aria-label="${title}，横轴日期，纵轴${unit}" viewBox="0 0 ${width} ${height}" style="width:100%;display:block">${svg}</svg></div></section>`;
}

function dailyQueueChart(rows, dates, format, escape, chartWidth = 800) {
  const colors=['#527be9','#16a6a1','#a084dc','#d4a148','#d77689','#70a1c4'];
  const width=chartWidth,height=260,left=64,top=16,bottom=230;
  const step=(width-left-32)/Math.max(1,dates.length-1);
  const points=rows.map(row=>dates.map(date=>row.daily?.find(day=>day.date===date)?.waiting ?? null));
  const max=Math.max(1,...points.flat().filter(v=>v!=null));
  let svg='';
  for(let i=0;i<=4;i++){const y=bottom-(bottom-top)*i/4;svg+=`<line x1="${left}" y1="${y}" x2="${width-16}" y2="${y}" stroke="#e9eef5"/><text x="${left-9}" y="${y+4}" text-anchor="end" fill="#8a96a8" font-size="10">${Number((max*i/4).toFixed(2))}</text>`;}
  const x=i=>dates.length===1?(width+left)/2:left+step*i;
  dates.forEach((date,i)=>{if(i % Math.max(1,Math.ceil(dates.length/Math.max(1,(width-80)/90)))===0)svg+=`<text x="${x(i)}" y="${bottom+21}" text-anchor="middle" font-size="10" fill="#78869a">${date.slice(5)}</text>`;});
  points.forEach((values,j)=>{
    let path='',connected=false,circles='';
    values.forEach((v,i)=>{if(v==null){connected=false;return;}const y=bottom-v/max*(bottom-top);path+=`${connected?'L':'M'}${x(i)} ${y} `;connected=true;circles+=`<circle tabindex="0" data-date="${escape(dates[i])}" data-model="${escape(rows[j].display_model||rows[j].model)}" data-value="${Number(v.toFixed(2))}" cx="${x(i)}" cy="${y}" r="3" fill="${colors[j%colors.length]}"><title>${escape(dates[i]+' · '+(rows[j].display_model||rows[j].model)+'：'+Number(v.toFixed(2))+' 个排队请求（日均）')}</title></circle>`;});
    svg+=`<path d="${path}" fill="none" stroke="${colors[j%colors.length]}" stroke-width="2"/>${circles}`;
  });
  return `<section class="chart-card daily-card"><div class="chart-title"><h3>各模型排队趋势</h3><span>平均排队请求数 / 日</span></div><div class="legend">${rows.map((row,i)=>`<span title="${escape(row.project+' · '+row.node)}"><i style="background:${colors[i%colors.length]}"></i>${escape((row.display_model||row.model))}</span>`).join('')}</div><div class="daily-scroll"><svg data-queue-chart data-right="${width-16}" data-step="${step}" role="img" aria-label="各模型每日平均排队请求数，缺失数据断开" viewBox="0 0 ${width} ${height}" style="width:100%;display:block">${svg}</svg></div></section>`;
}

// Inclusive day indices, with a fixed span when panning the selected window.
function brushRange(start, end, delta, part, count) {
  const clamp=(v,min,max)=>Math.max(min,Math.min(max,v));
  if(part==='start')return [clamp(start+delta,0,end),end];
  if(part==='end')return [start,clamp(end+delta,start,count-1)];
  const shift=clamp(delta,-start,count-1-end);
  return [start+shift,end+shift];
}

// Shared SVG crosshair for the statistics page's line charts.
let statisticsCrosshair = null;
function hideStatisticsCrosshair() {
  if (statisticsCrosshair) statisticsCrosshair.remove();
  statisticsCrosshair = null;
}
function showStatisticsCrosshair(svg, {x,y,left,right,top,bottom,time,value,series=''}) {
  hideStatisticsCrosshair();
  const escape = text => String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label = (text, px, py, width) => `<rect x="${px}" y="${py}" width="${width}" height="20" rx="2" fill="white" stroke="#cbd5e1"/><text x="${px+5}" y="${py+14}" style="fill:#425570;font-size:11px">${escape(text)}</text>`;
  const timeWidth = Math.min(right-left, time.length*7+10), valueWidth = Math.min(right-left,value.length*7+10);
  svg.insertAdjacentHTML('beforeend', `<g class="statistics-crosshair" aria-hidden="true" pointer-events="none"><path d="M${x} ${top}V${bottom} M${left} ${y}H${right}" fill="none" stroke="#8996aa" stroke-dasharray="3 3"/><circle cx="${x}" cy="${y}" r="3" fill="#527be9" stroke="white"/>${label(value,left,Math.max(top,Math.min(bottom-20,y-10)),valueWidth)}${label(time,Math.max(left,Math.min(right-timeWidth,x-timeWidth/2)),bottom+6,timeWidth)}${series?label(series,left,top,Math.min(right-left,Array.from(series).length*12+10)):''}</g>`);
  statisticsCrosshair = svg.querySelector('.statistics-crosshair');
}
function showQueueCrosshair(event) {
  const svg = event.target.closest('svg[data-queue-chart]');
  hideStatisticsCrosshair();
  if (!svg) return;
  const point = svg.createSVGPoint(); point.x=event.clientX; point.y=event.clientY;
  const matrix = svg.getScreenCTM(); if (!matrix) return;
  const pos=point.matrixTransform(matrix.inverse()), right=Number(svg.dataset.right);
  if(pos.x<64 || pos.x>right || pos.y<16 || pos.y>230)return;
  let closest=null, distance=Infinity;
  for(const sample of svg.querySelectorAll('circle[data-date]')) {
    const dx=Math.abs(Number(sample.getAttribute('cx'))-pos.x);
    // Pick a date first, then the nearest model at that date.
    const score=dx*1000+Math.abs(Number(sample.getAttribute('cy'))-pos.y);
    if(dx<=Number(svg.dataset.step)/2+1 && score<distance){closest=sample;distance=score;}
  }
  if(!closest)return;
  showStatisticsCrosshair(svg,{x:Number(closest.getAttribute('cx')),y:Number(closest.getAttribute('cy')),left:64,right,top:16,bottom:230,time:closest.dataset.date,value:closest.dataset.value+' req',series:closest.dataset.model});
}
