/* みっふぃーAIに聞く — アプリの殻だけキャッシュする。
   蔵書データ (index.enc) は IndexedDB に入るのでここでは扱わない。
   Gemini API は必ずネットワークへ通す。 */

// 中身を変えたらここの番号を上げる。activate で古いキャッシュを捨てるので、
// 端末に残った旧版が返り続けるのを防げる。
const CACHE = 'kindle-oracle-v5';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './icon.svg', './icon-192.png', './icon-512.png',
               './apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())   // 1 つ落ちても導入は続ける
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;      // API は素通し
  if (url.pathname.endsWith('index.enc')) return;       // 巨大なので都度取得

  // 本体 HTML はネットワーク優先。キャッシュ優先にすると、更新したのに
  // 古い画面が出続けてしまう。落ちたときだけキャッシュに退避する。
  const isPage = e.request.mode === 'navigate' ||
                 url.pathname.endsWith('.html') ||
                 url.pathname === '/' || url.pathname.endsWith('/');
  if (isPage) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok)
          caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      }).catch(() => caches.match(e.request).then(
        hit => hit || caches.match('./index.html')))
    );
    return;
  }

  // アイコンなどはキャッシュ優先。裏で更新して次回に反映する
  e.respondWith(
    caches.match(e.request).then(hit => {
      const net = fetch(e.request).then(res => {
        if (res && res.ok)
          caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
