# DR-007 · URL hash 子页寻址

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `ui/index.html` `goPage`/`routeHash`/`hashchange`/nav.onclick

## 上下文 / Context
单文件 SPA 原本切页不改 URL,子页无法寻址/收藏/前进后退。需每个子页有对应 URL,且不改后端路由(Flask 静态托管)。

## 决定 / Decision
- **hash 路由**(纯前端,无需后端改路由):`goPage(p)` 统一切 nav/page + `load(p)`;nav 点击既切页又写 `location.hash=<page>`。
- `routeHash()` + `hashchange` 监听:hash 变化(地址栏输入/前进后退/收藏打开)驱动切页;支持普通页 `#<page>`(26 页)与深链 `#graph=<key>`、`#obj=<key>:<id>`;未知 hash 回首页并 `replaceState` 纠正 URL。
- 初始加载带 hash 直达对应页,否则默认首页;既有 `.click()` 内部跳转经 `goPage` 同步切页,`hashchange` 二次 `goPage` 幂等无环(`load` 由 `loaded[p]` 守卫)。

## 后果 / Consequences
- (+) 26 子页均可 `#<page>` 直接寻址;刷新/前进后退/收藏保持当前页;保留 `#graph=`/`#obj=` 深链。
- (−) `.click()` 驱动的导航有一次幂等的二次 `goPage`(无副作用)。
