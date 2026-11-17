// Run:  node src/tests/test_admin_refresh_ordering.js
// Plain node, no runner and no deps - the rest of src/tests is pytest,
// and this checks a browser-side ordering pytest cannot reach.
// Checks the ordering the admin console's refresh() now relies on:
// the slow /admin/sources read must NOT hold up the first paint, and must
// still fill the cache and redraw when it lands.
const assert = require("assert");

function makeRefresh({ sourcesMs, fastMs }) {
  const state = {
    cache: { catalog: null, sources: [] },
    sourcesLoading: false,
    tok: "t",
    renders: 0,
    focused: false,
  };
  const sleep = (ms, v) => new Promise(r => setTimeout(() => r(v), ms));
  const render = () => { state.renders++; };

  state.refresh = async () => {
    state.sourcesLoading = true;
    sleep(sourcesMs, { sources: [{ source: "a.pdf" }] }).then(src => {
      state.cache.sources = src.sources;
      state.sourcesLoading = false;
      if (state.tok && state.cache.catalog !== null && !state.focused) render();
    });
    const [cat] = await Promise.all([sleep(fastMs, { categories: [{ key: "nv" }] })]);
    state.cache.catalog = cat.categories;
  };
  return { state, render };
}

(async () => {
  // 1. The normal case on the server: sources is far slower than the rest.
  {
    const { state } = makeRefresh({ sourcesMs: 60, fastMs: 5 });
    await state.refresh();
    assert.strictEqual(state.sourcesLoading, true, "still loading when refresh resolves");
    assert.deepStrictEqual(state.cache.sources, [], "sources not in yet");
    assert.notStrictEqual(state.cache.catalog, null, "catalog is in, so render() is safe");
    await new Promise(r => setTimeout(r, 90));
    assert.strictEqual(state.sourcesLoading, false, "flag cleared when sources lands");
    assert.strictEqual(state.cache.sources.length, 1, "sources filled in");
    assert.strictEqual(state.renders, 1, "exactly one redraw when it lands");
  }

  // 2. The edge case the guard exists for: a small index answers sources
  //    FIRST, while cache.catalog is still null. render() walks the
  //    catalog, so it must not be called -- the caller's own render will.
  {
    const { state } = makeRefresh({ sourcesMs: 1, fastMs: 50 });
    await state.refresh();
    assert.strictEqual(state.cache.sources.length, 1, "sources still landed");
    assert.strictEqual(state.renders, 0, "no redraw over a null catalog");
  }

  // 3. Signed out mid-flight: nothing is drawn over the sign-in gate.
  {
    const { state } = makeRefresh({ sourcesMs: 40, fastMs: 5 });
    await state.refresh();
    state.tok = "";
    await new Promise(r => setTimeout(r, 70));
    assert.strictEqual(state.renders, 0, "no redraw once signed out");
  }

  // 4. Mid-edit: a redraw would discard locally staged edits.
  {
    const { state } = makeRefresh({ sourcesMs: 40, fastMs: 5 });
    await state.refresh();
    state.focused = true;
    await new Promise(r => setTimeout(r, 70));
    assert.strictEqual(state.renders, 0, "no redraw while typing");
    assert.strictEqual(state.cache.sources.length, 1, "cache still updated though");
  }

  console.log("all 4 checks passed");
})();
