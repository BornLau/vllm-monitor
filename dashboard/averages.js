(() => {
  const el = id => document.getElementById(id);
  const number = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 2});
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const duration = seconds => seconds == null ? '—' : seconds < 60 ? number(seconds) + ' 秒' : seconds < 3600 ? number(seconds / 60) + ' 分钟' : number(seconds / 3600) + ' 小时';
  let serial = 0;
  async function load() {
    const id = ++serial, window = el('average-window').value;
    el('average-refresh').disabled = true;
    el('average-error').hidden = true;
    el('average-rows').innerHTML = '<div class="chart-empty">正在加载平均值…</div>';
    el('average-status').textContent = '正在读取历史采样…';
    try {
      const response = await fetch('/api/averages?window=' + encodeURIComponent(window));
      if (!response.ok) throw new Error('平均值加载失败，请检查 Prometheus 连接后重试。');
      const data = await response.json();
      if (id !== serial) return;
      const seconds = window === '12h' ? 43200 : 86400;
      const coverage = v => v == null ? '—' : number(Math.min(100, v / seconds * 100)) + '%';
      el('average-rows').innerHTML = data.rows.length ? `<div class="average-table-wrap"><table class="average-table"><thead><tr><th>模型</th><th>输入平均 tok/s</th><th>输出平均 tok/s</th><th>有效时长（输入 / 输出）</th></tr></thead><tbody>${data.rows.map(row=>`<tr><td title="${escape(row.project+' · '+row.node)}">${escape(row.model)}</td><td>${number(row.input_average)}</td><td>${number(row.output_average)}</td><td>${duration(row.input_active_seconds)} / ${duration(row.output_active_seconds)}<small>数据覆盖 ${coverage(row.input_observed_seconds)} / ${coverage(row.output_observed_seconds)}</small></td></tr>`).join('')}</tbody></table></div>` : '<div class="chart-empty">当前没有可统计的服务入口</div>';
      el('average-status').textContent = '统计截至：' + new Date(data.end * 1000).toLocaleString('zh-CN') + ' · 不含零吞吐采样段';
      if (data.rows.some(row => ['input', 'output'].some(d => row[d + '_observed_seconds'] == null || row[d + '_observed_seconds'] < seconds * .9))) {
        el('average-error').textContent = '部分模型数据覆盖不足 90% 或缺失，平均值仅代表已有有效采样。';
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
  el('average-window').onchange = load;
  el('average-refresh').onclick = load;
  load();
})();
