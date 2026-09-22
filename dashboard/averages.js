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
      const detail = direction => row => `有效时长 ${duration(row[direction + '_active_seconds'])} · 数据覆盖 ${coverage(row[direction + '_observed_seconds'])}`;
      el('average-rows').innerHTML = data.rows.length ?
        usageBars(data.rows, 'output_average', '输出平均', 'tok/s', '#527be9', number, escape, detail('output')) +
        usageBars(data.rows, 'input_average', '输入平均', 'tok/s', '#16a6a1', number, escape, detail('input'))
        : '<div class="chart-empty">当前没有可统计的服务入口</div>';
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
