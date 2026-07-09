// ws.js — reconnecting WebSocket helper shared by the player and host pages.
// No build step: plain script include, exposes window.DoodleSocket.
//
// Usage:
//   const sock = DoodleSocket({
//     onOpen:   (api) => api.send('join', {...}),   // called on every (re)connect
//     onMessage:(msg, api) => { ... },
//     onStatus: (state) => { ... },                 // 'online' | 'offline'
//   });
//   sock.send('start_game');

(function () {
  function DoodleSocket(opts) {
    const { onOpen, onMessage, onStatus } = opts || {};
    let ws = null;
    let closedByUser = false;
    let retry = 0;

    function url() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      return proto + '://' + location.host + '/ws';
    }

    function connect() {
      ws = new WebSocket(url());
      ws.onopen = () => {
        retry = 0;
        if (onStatus) onStatus('online');
        if (onOpen) onOpen(api);
      };
      ws.onclose = () => {
        if (onStatus) onStatus('offline');
        if (!closedByUser) {
          retry += 1;
          setTimeout(connect, Math.min(5000, 400 * retry)); // backoff, capped 5s
        }
      };
      ws.onerror = () => { /* surfaced via onclose */ };
      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (onMessage) onMessage(msg, api);
      };
    }

    const api = {
      send(type, payload) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ v: 1, type: type, payload: payload || {} }));
        }
      },
      close() { closedByUser = true; if (ws) ws.close(); },
    };

    connect();
    return api;
  }

  window.DoodleSocket = DoodleSocket;
})();
