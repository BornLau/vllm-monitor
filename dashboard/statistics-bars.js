function usageBars(rows, key, title, unit, color, format, escape, detail) {
  rows = [...rows].sort((a,b) => (b[key] ?? -1) - (a[key] ?? -1));
  const valid = rows.map(r => r[key]).filter(v => v != null && Number.isFinite(v));
  const max = Math.max(1, ...valid);
  return `<section class="chart-card"><div class="chart-title"><h3>${title}</h3><span>${unit}</span></div><div class="bar-axis"><span>0</span><span>${format(max / 2)}</span><span>${format(max)}</span></div>${rows.map(r => {
    const value = r[key], present = value != null && Number.isFinite(value);
    return `<div class="bar-row" tabindex="0" title="${escape(r.project + ' · ' + r.node + (detail ? ' · ' + detail(r) : ''))}"><div class="bar-label"><strong>${escape(r.model)}</strong><span>${escape(r.project)} · ${escape(r.node)}</span></div><div class="bar-track" role="img" aria-label="${escape(r.model)} ${title} ${present ? format(value) + ' ' + unit : '暂无数据'}"><i style="width:${present ? Math.max(0, value / max * 100) : 0}%;background:${color}"></i></div><div class="bar-value">${present ? format(value) : '—'}</div>${detail ? `<small class="bar-detail">${detail(r)}</small>` : ''}</div>`;
  }).join('')}</section>`;
}

function usageTokenBars(rows, format, escape) {
  rows = [...rows].sort((a,b) => (b.total_tokens ?? -1) - (a.total_tokens ?? -1));
  const max = Math.max(1, ...rows.map(r => r.total_tokens).filter(v => v != null && Number.isFinite(v)));
  return `<section class="chart-card"><div class="chart-title"><h3>Token 用量排行</h3><span>tokens</span></div><div class="legend"><span><i style="background:#527be9"></i>输入 Token</span><span><i style="background:#16a6a1"></i>输出 Token</span></div><div class="bar-axis"><span>0</span><span>${format(max/2)}</span><span>${format(max)}</span></div>${rows.map(r => {
    const valid = [r.input_tokens,r.output_tokens,r.total_tokens].every(v => v != null && Number.isFinite(v));
    return `<div class="bar-row" tabindex="0" title="${escape(r.project + ' · ' + r.node)} · 输入 ${format(r.input_tokens)} · 输出 ${format(r.output_tokens)}"><div class="bar-label"><strong>${escape(r.model)}</strong><span>${escape(r.project)} · ${escape(r.node)}</span></div><div class="bar-track" role="img" aria-label="${escape(r.model)} 输入 ${format(r.input_tokens)} 输出 ${format(r.output_tokens)} tokens">${valid ? `<i style="width:${r.input_tokens/max*100}%;background:#527be9"></i><i style="width:${r.output_tokens/max*100}%;background:#16a6a1"></i>` : ''}</div><div class="bar-value">${valid ? format(r.total_tokens) : '—'}</div><small class="bar-detail">输入 ${format(r.input_tokens)} · 输出 ${format(r.output_tokens)}</small></div>`;
  }).join('')}</section>`;
}
