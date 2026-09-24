/* Persisted Prometheus history followed by live samples. */
(function (root) {
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const keyOf = (project, node) => JSON.stringify([project.id, node.id, node.api_base_url, node.metrics_url]);
  const metrics = [
    ['output_tps', '解码速度', 'tok/s', '#527be9'],
    ['ttft', '首字延迟 P50', 's', '#16a6a1'],
    ['waiting', '等待队列', 'req', '#d4a148'],
    ['kv', 'KV Cache', '%', '#527be9']
  ];
  // Polling is scheduled after the previous request completes. The backend may
  // legitimately spend up to five seconds querying an upstream, so a healthy
  // three-second refresh can produce samples more than 7.5 seconds apart.
  const gapLimit = interval => Math.max(interval * 4, interval + 6000);
  class History {
    constructor() { this.entries = new Map(); }
    merge(payload) {
      for (const item of payload.entries) {
        const key = keyOf({id:item.project_id}, item.node);
        const entry = this.entries.get(key) || {samples:[], maxima:{}};
        // Range queries use a different timestamp grid. Once live collection
        // begins, don't interleave historical nulls between healthy live points.
        const live = entry.samples.filter(sample => sample.live);
        const liveStart = live[0]?.time ?? Infinity;
        const points = new Map(item.samples.filter(sample => sample.time < liveStart).map(sample => [sample.time, sample]));
        for (const sample of live) points.set(sample.time, sample);
        entry.samples = [...points.values()].sort((a,b) => a.time-b.time);
        const cutoff = (entry.samples.at(-1)?.time || 0) - 86400000;
        entry.samples = entry.samples.filter(sample => sample.time >= cutoff).slice(-28801);
        entry.historyStep = payload.step_ms;
        entry.maxima = {};
        for (const sample of entry.samples) for (const [name] of metrics)
          if (finite(sample.values[name])) entry.maxima[name] = Math.max(entry.maxima[name] || 1, sample.values[name]*1.2);
        this.entries.set(key, entry);
      }
    }
    record(snapshot, time) {
      const active = new Set();
      for (const project of snapshot.projects) for (const node of project.nodes) {
        const key = keyOf(project, node); active.add(key);
        const entry = this.entries.get(key) || {samples: [], maxima: {}};
        if (!entry.samples.length || time > entry.samples.at(-1).time) {
          const values = Object.fromEntries(metrics.map(([name]) => {
            if (node.values.up !== 1) return [name, null];
            if (name === 'waiting' || name === 'kv')
              return [name, finite(node.values[name]) ? node.values[name] : null];
            // Idle latency uses the baseline as a visual convention, not a
            // measured zero-second observation. Unknown intervals stay missing.
            if (node.activity === 'idle') return [name, 0];
            return [name, node.activity === 'active' && finite(node.values[name]) ? node.values[name] : null];
          }));
          entry.samples.push({time, values, live: true});
          while (entry.samples.length > 28801 || (entry.samples.length && entry.samples[0].time < time - 86400000)) entry.samples.shift();
          for (const [name] of metrics) if (finite(values[name])) entry.maxima[name] = Math.max(entry.maxima[name] || 1, values[name] * 1.2);
        }
        this.entries.set(key, entry);
      }
      for (const key of this.entries.keys()) if (!active.has(key)) this.entries.delete(key);
    }
  }
  function smoothPath(points) {
    const position = ([x,y]) => `${x.toFixed(3)},${y.toFixed(3)}`;
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
  function geometry(samples, metric, start, end, maximum, gap) {
    // Keep a neighbor on each side so clipping does not move the filled edges.
    let first = samples.findIndex(point => point.time >= start);
    if (first < 0) return {line: '', area: '', reference: '', count: 0};
    first = Math.max(0, first - 1);
    const relevant = [];
    for (let i = first; i < samples.length; i++) {
      relevant.push(samples[i]);
      if (samples[i].time > end) break;
    }
    const segments = []; let segment = [], previous = null;
    for (const sample of relevant) {
      if (!finite(sample.values[metric]) || (previous !== null && sample.time - previous > gap)) {
        if (segment.length) segments.push(segment);
        segment = [];
      }
      if (finite(sample.values[metric])) segment.push(sample);
      previous = sample.time;
    }
    if (segment.length) segments.push(segment);
    let line = '', area = '', count = 0;
    for (const raw of segments) {
      // Preserve extrema within each time bucket instead of dropping brief spikes.
      const stride = Math.max(1, Math.ceil(raw.length / 160)), points = [];
      for (let i = 0; i < raw.length; i += stride) {
        const chunk = raw.slice(i, i + stride);
        const min = chunk.reduce((a,b) => a.values[metric] < b.values[metric] ? a : b);
        const max = chunk.reduce((a,b) => a.values[metric] > b.values[metric] ? a : b);
        points.push(...[...new Set([chunk[0], min, max, chunk.at(-1)])].sort((a,b) => a.time - b.time));
      }
      const xy = points.map(point => [(point.time-start)/(end-start)*200, 90-Math.max(0,point.values[metric])/maximum*80]);
      count += points.filter(point => point.time >= start && point.time <= end).length;
      const path = smoothPath(xy);
      line += path + (xy.length === 1 ? ` L${xy[0][0].toFixed(3)},${xy[0][1].toFixed(3)}` : '');
      if (xy.length > 1) area += `${path} L${xy.at(-1)[0].toFixed(3)},90 L${xy[0][0].toFixed(3)},90 Z `;
    }
    // Keep absent observations out of the measured line and filled area.
    // A separate dashed layer supplies visual continuity, including a last-value
    // reference at the right edge while waiting for the next observation.
    const position = point => `${((point.time-start)/(end-start)*200).toFixed(3)},${(90-Math.max(0,point.values[metric])/maximum*80).toFixed(3)}`;
    let reference = '', lastValid = null, missing = false;
    for (const sample of relevant) {
      if (!finite(sample.values[metric])) {missing = true; continue;}
      if (lastValid && (missing || sample.time-lastValid.time > gap))
        reference += `M${position(lastValid)} L${position(sample)} `;
      lastValid = sample; missing = false;
    }
    if (lastValid && lastValid.time >= start && lastValid.time < end)
      reference += `M${position(lastValid)} L${position({...lastValid,time:end})}`;
    return {line, area, reference, count};
  }
  const api = {History, geometry, gapLimit, keyOf, metrics};
  if (typeof module !== 'undefined') module.exports = api;
  if (!root.document) return;
  const history = new History(); let windowMs = 900000, interval = 3000, paused = false, frozen = null, latest = null, frame = null, lastDraw = 0;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const visible = new Set();
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    entry.target.dataset.visible = String(entry.isIntersecting);
    if (entry.isIntersecting) visible.add(entry.target); else visible.delete(entry.target);
  }));
  const observed = new Set();
  function observe() {
    for (const panel of observed) if (!panel.isConnected) {observer.unobserve(panel); observed.delete(panel); visible.delete(panel);}
    document.querySelectorAll('.trend-panel').forEach(panel => {if (!observed.has(panel)) {observer.observe(panel); observed.add(panel);}});
    draw(true);
  }
  function draw(force = false) {
    const now = performance.now();
    if (!force && now-lastDraw < 100) return;
    lastDraw = now;
    const end = frozen ?? (reduced.matches ? latest : Date.now() - interval);
    if (!end) return;
    for (const panel of visible) {
      const entry = history.entries.get(panel.dataset.key), metric = panel.dataset.metric;
      const max = metric === 'kv' ? Math.max(100,entry?.maxima.kv || 100) : entry?.maxima[metric] || 1;
      const paths = geometry(entry?.samples || [], metric, end-windowMs, end, max, Math.max(gapLimit(interval), (entry?.historyStep || 0) * 1.5));
      panel.querySelector('.chart-line').setAttribute('d', paths.line);
      panel.querySelector('.chart-area').setAttribute('d', paths.area);
      panel.querySelector('.chart-reference').setAttribute('d', paths.reference);
      panel.querySelector('.chart-empty').hidden = paths.count >= 1;
      panel.querySelector('.chart-empty').textContent = paths.count ? '正在积累采样…' : '暂无有效采样';
      panel.querySelector('.axis-max').textContent = max.toLocaleString('zh-CN',{maximumFractionDigits:1});
      const format = time => new Date(time).toLocaleTimeString('zh-CN',{hour12:false});
      panel.querySelector('.axis-start').textContent = format(end-windowMs);
      panel.querySelector('.axis-end').textContent = format(end);
    }
  }
  function animate() {frame = null; if (paused || document.hidden) return; draw(); frame=requestAnimationFrame(animate);}
  function motion() {
    document.body.classList.toggle('motion-paused',paused || document.hidden);
    if (paused || document.hidden) {if(frame!==null)cancelAnimationFrame(frame);frame=null;}
    else if(frame===null)frame=requestAnimationFrame(animate);
  }
  document.addEventListener('visibilitychange',motion);
  root.MonitorCharts = {...api, observe,
    record(snapshot) {interval=Math.max(3000,snapshot.refresh_seconds*1000); const time=snapshot.collected_at_ms; if(!finite(time))return; history.record(snapshot,time);latest=time;motion();},
    merge(payload) {history.merge(payload);draw(true);},
    setWindow(minutes) {windowMs=minutes*60000;draw(true);},
    pause(value) {paused=value;frozen=value?Date.now()-interval:null;motion();draw(true);},
  };
})(typeof window === 'undefined' ? globalThis : window);
