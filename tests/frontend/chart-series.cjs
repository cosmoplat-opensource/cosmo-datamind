'use strict';
// 结果图表的序列整理(ui/modules/chart-series.js):长表(月份 × 区域 × 数值)要按分组列拆成多条序列。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

function load() {
  const win = {};
  vm.runInContext(fs.readFileSync(path.join(root, 'ui/modules/chart-series.js'), 'utf8'), vm.createContext({window: win}));
  return (d, ch) => JSON.parse(JSON.stringify(win.chartSeries(d, ch)));   // 换成本上下文的普通对象再比较
}
const data = (columns, rows) => ({columns, rows: rows.map(r => Object.fromEntries(columns.map((c, i) => [c, r[i]])))});

const cases = {
  a_long_table_is_split_into_one_series_per_group() {
    const d = data(['month', 'region', 'pct'], [['2026-04', '华东', 15], ['2026-04', '华北', 14], ['2026-05', '华东', 16], ['2026-05', '华北', 13]]);
    const out = load()(d, {type: 'line', x: 'month', y: ['pct']});
    assert.deepEqual(out.cats, ['2026-04', '2026-05']);
    assert.deepEqual(out.series, [{name: '华东', data: [15, 16]}, {name: '华北', data: [14, 13]}]);
  },
  the_group_column_is_found_even_when_the_plan_lists_it_as_a_value() {
    const d = data(['month', 'region', 'pct'], [['2026-04', '华东', 15], ['2026-04', '华北', 14]]);
    const out = load()(d, {});
    assert.deepEqual(out.series.map(s => s.name), ['华东', '华北']);
  },
  a_missing_combination_is_left_empty_not_shifted() {
    const d = data(['month', 'region', 'pct'], [['2026-04', '华东', 15], ['2026-05', '华东', 16], ['2026-05', '华北', 13]]);
    assert.deepEqual(load()(d, {x: 'month', y: ['pct']}).series[1], {name: '华北', data: [null, 13]});
  },
  a_wide_table_keeps_one_series_per_numeric_column() {
    const d = data(['month', 'sales', 'cost', 'note'], [['2026-04', 10, 6, 'a'], ['2026-05', 12, 7, 'b']]);
    const out = load()(d, {});
    assert.deepEqual(out.cats, ['2026-04', '2026-05']);
    assert.deepEqual(out.series, [{name: 'sales', data: [10, 12]}, {name: 'cost', data: [6, 7]}]);
  },
  two_measures_in_a_long_table_are_named_by_group_and_measure() {
    const d = data(['month', 'region', 'sales', 'cost'], [['2026-04', '华东', 10, 6], ['2026-04', '华北', 8, 5]]);
    assert.deepEqual(load()(d, {x: 'month'}).series.map(s => s.name), ['华东 · sales', '华东 · cost', '华北 · sales', '华北 · cost']);
  },
  repeated_group_and_x_pairs_are_plotted_as_they_are() {
    const d = data(['day', 'shift', 'qty'], [['11-01', '早', 5], ['11-01', '早', 7], ['11-01', '晚', 3], ['11-02', '早', 4]]);
    const out = load()(d, {x: 'day', y: ['qty']});
    assert.equal(out.series.length, 1); assert.deepEqual(out.series[0].data, [5, 7, 3, 4]);
  },
  numbers_stored_as_text_are_still_plotted() {
    const d = data(['month', 'sales', 'pct'], [['2026-04', '10.5', 3], ['2026-05', '12', 4]]);
    assert.deepEqual(load()(d, {}).series.map(s => s.name), ['sales', 'pct']);
  },
  too_many_groups_are_not_split() {
    const rows = []; for (let i = 0; i < 20; i++) rows.push(['2026-04', 'g' + i, i], ['2026-05', 'g' + i, i]);
    const out = load()(data(['month', 'g', 'v'], rows), {x: 'month', y: ['v']});
    assert.equal(out.series.length, 1); assert.equal(out.cats.length, 40);
  },
};
const name = process.argv[2];
if (!cases[name]) { console.error('unknown case', name, '; cases:', Object.keys(cases).join(', ')); process.exit(2); }
cases[name]();
