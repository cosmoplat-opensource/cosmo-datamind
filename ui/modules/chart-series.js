/* 结果图表的序列整理(DR-131)。
   宽表(月份、销售额、成本):每个数值列一条序列。
   长表(月份、区域、毛利率):横轴取值有重复,且另有一个文字列时,按这个文字列拆成多条序列,
   否则各区域的点会被连成一条来回折返的线。分组过多(超过 12 个)时不拆,按原样画。 */
(function () {
  const isNum = v => (typeof v === 'number' && Number.isFinite(v)) || (typeof v === 'string' && v.trim() !== '' && Number.isFinite(+v));
  window.chartSeries = function (data, chart) {
    const rows = (data && data.rows) || [], cols = (data && data.columns) || [];
    const ch = chart || {};
    const x = ch.x || cols[0];
    const listed = (ch.y && ch.y.length ? ch.y : cols.slice(1)).filter(c => c !== x);
    const numeric = c => rows.some(r => isNum(r[c])) && rows.every(r => r[c] == null || isNum(r[c]));
    const ys = listed.filter(numeric);
    const flat = () => ({cats: rows.map(r => r[x]), series: (ys.length ? ys : listed).map(c => ({name: c, data: rows.map(r => r[c])}))});
    const cats = [...new Set(rows.map(r => r[x]))];
    const groupCols = cols.filter(c => c !== x && !numeric(c));
    if (!ys.length || cats.length === rows.length || groupCols.length !== 1) return flat();
    const g = groupCols[0], groups = [...new Set(rows.map(r => String(r[g])))];
    if (groups.length < 2 || groups.length > 12) return flat();
    const at = new Map(rows.map(r => [String(r[g]) + '\u0000' + r[x], r]));
    if (at.size !== rows.length) return flat();      // 同一组、同一横轴取值有多行(未汇总的明细):按原样画,不丢行
    const series = [];
    for (const name of groups) {
      for (const y of ys) {
        series.push({name: ys.length > 1 ? name + ' · ' + y : name,
                     data: cats.map(c => { const r = at.get(name + '\u0000' + c); return r ? r[y] : null; })});
      }
    }
    return {cats, series};
  };
})();
