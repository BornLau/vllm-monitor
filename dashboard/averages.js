(() => {
  const el = id => document.getElementById(id);
  const number = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2});
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function smoothPath(points) {
    const position = ([x,y]) => `${x.toFixed(2)},${y.toFixed(2)}`;
    if (points.length < 3)
      return points.map((point,i) => `${i?'L':'M'}${position(point)}`).join(' ');
    const slopes = points.slice(1).map(([x,y],i) => (y-points[i][1])/(x-points[i][0]));
    const tangents = points.map((_,i) => {
      if (i === 0) return slopes[0];
      if (i === points.length-1) return slopes.at(-1);
      const before = slopes[i-1], after = slopes[i];
      // A shared, limited tangent gives C1 continuity. Flatten extrema and
      // plateaus; keep control points inside each sample interval so the
      // curve cannot invent peaks, negative values or extra oscillations.
      return before * after > 0 ? Math.sign(before)*Math.min(Math.abs(before),Math.abs(after)) : 0;
    });
    let path = `M${position(points[0])}`;
    for (let i=1; i<points.length; i++) {
      const [x0,y0] = points[i-1], [x1,y1] = points[i], dx = (x1-x0)/3;
      path += ` C${position([x0+dx,y0+dx*tangents[i-1]])} ${position([x1-dx,y1-dx*tangents[i]])} ${position(points[i])}`;
    }
    return path;
  }
  function chart(metric, title, color, start, end) {
    metric = metric || {};
    const valid = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
    const samples = metric.samples || [], mean = valid(metric.average) ? metric.average * 1000 : null;
    const values = samples.filter(point => valid(point[1])).map(point => point[1] * 1000);
    // Fit the observed range, including the reference mean, without forcing zero.
    // Flat series get a small minimum span so rounding/noise is not exaggerated.
    const extent = [...values, ...(mean == null ? [] : [mean])];
    const low = extent.length ? Math.min(...extent) : 0;
    const high = extent.length ? Math.max(...extent) : 1;
    const padding = Math.max((high - low) * .12, high * .01, .01);
    const unit = 10 ** Math.floor(Math.log10(Math.max(high - low, padding * 2))) / 10;
    const min = Math.max(0, Math.floor((low - padding) / unit) * unit);
    const max = Math.ceil((high + padding) / unit) * unit;
    const axisNumber = value => Number(value.toFixed(6)).toLocaleString('zh-CN', {maximumFractionDigits: 6});
    const levels = [min, (min + max) / 2, max];
    const left = 82, right = 510, baseline = 150;
    const x = t => left + (t - start) / Math.max(1, end - start) * (right - left);
    const y = value => baseline - (value - min) / (max - min) * 120;
    const segments = []; let segment = [];
    for (const [t, value] of samples) {
      if (!valid(value)) { if (segment.length) segments.push(segment); segment = []; continue; }
      segment.push([x(t),y(value * 1000)]);
    }
    if (segment.length) segments.push(segment);
    const states = new Map(metric.states || []), bridges = [];
    let previous = null, gap = [];
    for (const [t, value] of samples) {
      if (!valid(value)) { gap.push([t, states.get(t)]); continue; }
      if (previous && gap.length && t - previous[0] <= 1800 && gap.every(([,state]) => state === 'idle')) {
        bridges.push({start: previous[0], end: t, path: smoothPath([[x(previous[0]), y(previous[1] * 1000)], [x(t), y(value * 1000)]])});
      }
      previous = [t, value]; gap = [];
    }
    const idleBands = bridges.map(b => `<rect class="latency-idle" data-idle-start="${b.start}" data-idle-end="${b.end}" x="${x(b.start)}" y="30" width="${x(b.end)-x(b.start)}" height="120" fill="#e2e6ec" fill-opacity=".65"/>`).join('');
    const bridgePaths = bridges.map(b => `<path class="latency-interpolation" d="${b.path}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round"/>`).join('');
    const meanColor = '#d04a16';
    const line = smoothPath;
    const path = segments.map(line).join(' ');
    const area = segments.map(points=>`${line(points)} L${points.at(-1)[0].toFixed(2)},${baseline} L${points[0][0].toFixed(2)},${baseline} Z`).join(' ');
    const time = t => new Date(t * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
    const hours = Math.max(1, Math.ceil((end-start)/3600/4));
    const tick = new Date(start*1000);
    tick.setMinutes(0,0,0);
    if (tick.getTime()/1000 < start) tick.setHours(tick.getHours()+1);
    const ticks = [];
    while (tick.getTime()/1000 <= end) {
      ticks.push(tick.getTime()/1000);
      tick.setHours(tick.getHours()+hours);
    }
    const axis = ticks.map(t=>{
      const d=new Date(t*1000), label=String(d.getHours()).padStart(2,'0')+':00';
      const date=String(d.getMonth()+1).padStart(2,'0')+'/'+String(d.getDate()).padStart(2,'0');
      return `<g class="hour-tick"><line x1="${x(t)}" x2="${x(t)}" y1="${baseline}" y2="${baseline+5}" stroke="#a8b4c5"/><text x="${x(t)}" y="170" text-anchor="middle">${label}</text>${end-start>=86400?`<text x="${x(t)}" y="185" text-anchor="middle">${date}</text>`:''}</g>`;
    }).join('');
    return `<div class="latency-chart"><div class="latency-title"><span>${title} <small>ms</small></span></div><svg data-metric="${title}" data-y-min="${min}" data-y-max="${max}" viewBox="0 0 540 198" role="img" aria-label="${title}历史趋势，平均 ${number(mean)} 毫秒"><title>${title} · 所选范围平均 ${number(mean)} ms</title>${idleBands}${levels.map(level=>`<line x1="${left}" x2="${right}" y1="${y(level)}" y2="${y(level)}" stroke="#e8edf4"/>${mean != null && Math.abs(y(level)-y(mean))<19?'':`<text x="${left-9}" y="${y(level)+4}" text-anchor="end">${axisNumber(level)}</text>`}`).join('')}<path class="latency-area" d="${area}" fill="${color}" fill-opacity="0.06"/>${bridgePaths}<path class="latency-line" d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>${samples.filter(p=>valid(p[1])).map(([t,v])=>`<circle data-time="${t}" data-value="${v*1000}" cx="${x(t).toFixed(2)}" cy="${y(v*1000).toFixed(2)}" r="${segments.some(points => points.length === 1 && points[0][0] === x(t)) ? 2 : 0}" fill="${color}" opacity=".75"><title>${time(t)} · ${number(v*1000)} ms</title></circle>`).join('')}<line x1="${left}" x2="${left}" y1="30" y2="${baseline}" stroke="#ccd5e1"/>${mean == null ? '' : `<line class="average-halo" x1="${left}" x2="${right}" y1="${y(mean)}" y2="${y(mean)}" stroke="white" stroke-width="5" stroke-dasharray="6 5"/><line class="average-reference" x1="${left}" x2="${right}" y1="${y(mean)}" y2="${y(mean)}" stroke="${meanColor}" stroke-width="2.5" stroke-dasharray="6 5"/><line x1="${left-5}" x2="${left}" y1="${y(mean)}" y2="${y(mean)}" stroke="${meanColor}"/><text class="average-axis-label" x="${left-9}" y="${y(mean)-4}" text-anchor="end" style="fill:${meanColor};font-weight:700"><tspan x="${left-9}">平均</tspan><tspan x="${left-9}" dy="14">${axisNumber(mean)}</tspan></text>`}${values.length ? '' : '<text x="296" y="90" text-anchor="middle">暂无有效采样</text>'}${axis}</svg></div>`;
  }
  let serial = 0;
  async function load() {
    const id = ++serial, window = el('average-window').value;
    el('average-tooltip').hidden = true;
    el('average-refresh').disabled = true;
    el('average-error').hidden = true;
    el('average-rows').innerHTML = '<div class="chart-empty">正在加载平均值…</div>';
    el('average-status').textContent = '正在读取历史采样…';
    try {
      let query = '/api/latency-averages?window=' + encodeURIComponent(window);
      if (window === 'custom') {
        const start = new Date(el('average-start').value).getTime() / 1000;
        const end = new Date(el('average-end').value).getTime() / 1000;
        if (!Number.isFinite(start) || !Number.isFinite(end) || end - start < 3 || end - start > 30 * 86400 || end > Date.now() / 1000) {
          throw new Error('请选择有效的起止时间：范围为 3 秒至 30 天，结束时间不能晚于当前时间。');
        }
        query += '&start=' + Math.floor(start) + '&end=' + Math.floor(end);
      }
      const response = await fetch(query);
      if (!response.ok) throw new Error('平均值加载失败，请检查 Prometheus 连接后重试。');
      const data = await response.json();
      if (id !== serial) return;
      const seconds = data.duration_seconds ?? ({'6h':21600,'12h':43200,'24h':86400,'48h':172800,'7d':604800}[window]);
      const start = data.start ?? data.end - seconds;
      el('average-rows').innerHTML = data.rows.length ? data.rows.map(row => `<article class="latency-row"><div class="latency-model"><strong>${escape(row.display_model || row.model)}</strong><small>${escape(row.project)} · ${escape(row.node)}</small></div>${chart(row.tpot, 'TPOT', '#527be9', start, data.end)}${chart(row.ttft, 'TTFT', '#24a391', start, data.end)}</article>`).join('') : '<div class="chart-empty">当前没有可统计的服务入口</div>';
      el('average-status').textContent = '统计范围：' + new Date(start * 1000).toLocaleString('zh-CN') + ' — ' + new Date(data.end * 1000).toLocaleString('zh-CN') + ' · 曲线：每 ' + data.step_seconds + ' 秒有效采样均值 · 橙色虚线：所选范围均值 · 灰底实线：短暂无新增观测，连接仅供参考';
      if (data.rows.some(row => ['tpot', 'ttft'].some(key => row[key]?.average == null))) {
        el('average-error').textContent = '部分模型缺少有效延迟采样，对应均值显示为 —，不按零处理。';
        el('average-error').hidden = false;
      }
    } catch (error) {
      if (id !== serial) return;
      el('average-error').textContent = error.message;
      el('average-error').hidden = false;
      el('average-rows').innerHTML = '<div class="chart-empty">暂无可用平均值</div>';
      el('average-status').textContent = '数据不可用，不按零处理';
    } finally {
      if (id === serial) el('average-refresh').disabled = false;
    }
  }
  const tooltip = el('average-tooltip');
  el('average-rows').onpointermove = event => {
    const svg = event.target.closest('svg[data-metric]');
    if (!svg) { tooltip.hidden = true; return; }
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const position = point.matrixTransform(svg.getScreenCTM().inverse());
    if (position.x < 82 || position.x > 510 || position.y < 25 || position.y > 155) {
      tooltip.hidden = true; return;
    }
    const idle = [...svg.querySelectorAll('rect[data-idle-start]')].find(band => {
      const left = Number(band.getAttribute('x')), width = Number(band.getAttribute('width'));
      return band.dataset.idleStart && position.x > left && position.x < left + width;
    });
    if (idle) {
      tooltip.textContent = '此间隔无新增时延观测，实线为插值参考，不参与均值计算';
    } else {
      let closest = null, distance = Infinity;
      for (const sample of svg.querySelectorAll('circle[data-time]')) {
        const delta = Math.abs(Number(sample.getAttribute('cx')) - position.x);
        if (delta < distance) { distance = delta; closest = sample; }
      }
      if (!closest || distance > 8) { tooltip.hidden = true; return; }
      tooltip.textContent = new Date(Number(closest.dataset.time) * 1000).toLocaleString('zh-CN', {hour12:false})
        + ' · ' + svg.dataset.metric + ' ' + number(Number(closest.dataset.value)) + ' ms';
    }
    tooltip.hidden = false;
    tooltip.style.left = Math.max(8, Math.min(event.clientX + 12, window.innerWidth - tooltip.offsetWidth - 8)) + 'px';
    tooltip.style.top = Math.max(8, event.clientY - tooltip.offsetHeight - 12) + 'px';
  };
  el('average-rows').onpointerleave = () => { tooltip.hidden = true; };
  const localDate = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  el('average-end').value = localDate(new Date());
  el('average-start').value = localDate(new Date(Date.now() - 24 * 3600000));
  el('average-window').onchange = () => {
    el('average-custom').hidden = el('average-window').value !== 'custom';
    load();
  };
  el('average-refresh').onclick = load;
  load();
})();
