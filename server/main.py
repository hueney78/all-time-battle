"""FastAPI application entry point.

Run with: uvicorn server.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.config import load_game_rules
from server.room import RoomManager, SocketDisconnect

app = FastAPI(title="Doodle Brawl", version="0.1.0")

_WEB_DIR = Path(__file__).parent.parent / "web"
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

# One in-memory room manager for the process (LAN party, single host machine).
room_manager = RoomManager(load_game_rules())


class _StarletteSocket:
    """Adapts a starlette WebSocket to the room layer's Socket protocol,
    surfacing disconnects as SocketDisconnect."""

    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def receive_text(self) -> str:
        try:
            return await self._ws.receive_text()
        except WebSocketDisconnect as exc:
            raise SocketDisconnect from exc

    async def close(self, code: int = 1000) -> None:
        try:
            await self._ws.close(code)
        except Exception:
            pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    await room_manager.handle_socket(_StarletteSocket(ws))


@app.get("/", response_class=HTMLResponse)
async def root():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Doodle Brawl</title>
  <style>
    body { font-family: sans-serif; max-width: 600px; margin: 4rem auto; padding: 0 1rem; }
    h1   { font-size: 2.5rem; margin-bottom: 0.5rem; }
    nav  { display: flex; gap: 1rem; margin-top: 1.5rem; }
    a    { padding: 0.6rem 1.2rem; background: #4f46e5; color: #fff;
           border-radius: 6px; text-decoration: none; font-weight: 600; }
    a:hover { background: #3730a3; }
    .sub { color: #6b7280; margin-top: 0.25rem; }
  </style>
</head>
<body>
  <h1>Doodle Brawl</h1>
  <p class="sub">Server is running. Open the host screen on your TV,
     then scan the QR code with your phone.</p>
  <nav>
    <a href="/host">Host Screen</a>
    <a href="/play">Join Game</a>
    <a href="/health">Health</a>
    <a href="/docs">API Docs</a>
  </nav>
</body>
</html>"""


@app.get("/health")
async def health():
    rules = load_game_rules()
    return {
        "status": "ok",
        "zones": [z.id for z in rules.zones.zones],
        "conditions": sorted(rules.conditions.conditions.keys()),
        "moves": sorted(rules.moves.moves.keys()),
        "ai": {
            "classify_model": rules.settings.ai.classify_model,
            "narrate_model": rules.settings.ai.narrate_model,
        },
    }


@app.get("/host", response_class=HTMLResponse)
async def host_page():
    # Phase 3 placeholder host: no arena rendering yet (that's Phase 4) — just
    # enough to create a lobby, start the game, and watch phases advance.
    return _HOST_HTML


@app.get("/play", response_class=HTMLResponse)
async def player_page():
    # Phase 3 placeholder player: join + auto-submit a doodle each draw phase so
    # the couch can see the pipeline run end to end. The canvas arrives in Phase 4.
    return _PLAYER_HTML


_HOST_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Doodle Brawl — Host</title>
<style>
 body{font-family:sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}
 #code{font-size:2.5rem;font-weight:800;letter-spacing:.2rem;color:#4f46e5}
 button{padding:.6rem 1.2rem;font-weight:700;border:0;border-radius:6px;
        background:#4f46e5;color:#fff;cursor:pointer}
 button:disabled{background:#9ca3af;cursor:default}
 #log{margin-top:1rem;font-family:monospace;font-size:.85rem;white-space:pre-wrap;
      background:#111;color:#0f0;padding:1rem;border-radius:6px;height:16rem;overflow:auto}
 .p{display:inline-block;margin:.2rem;padding:.2rem .5rem;border-radius:4px;color:#fff}
</style></head>
<body>
 <h1>Doodle Brawl — Host <small style="color:#9ca3af">(Phase 3 placeholder)</small></h1>
 <p>Room code: <span id="code">····</span></p>
 <p>Players join at <code id="joinurl"></code></p>
 <div id="players"></div>
 <p><button id="start" disabled>Start Game</button></p>
 <div id="log"></div>
<script>
const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws');
const log = (m)=>{
  const l=document.getElementById('log');
  l.textContent+=m+"\\n"; l.scrollTop=l.scrollHeight;
};
ws.onopen = ()=> ws.send(JSON.stringify({v:1,type:'join',payload:{role:'host'}}));
ws.onclose = ()=> log('[disconnected]');
document.getElementById('start').onclick = ()=>
  ws.send(JSON.stringify({v:1,type:'start_game',payload:{}}));
ws.onmessage = (e)=>{
  const m = JSON.parse(e.data);
  if(m.type==='joined'){
    const code=m.payload.room;
    document.getElementById('code').textContent=code;
    document.getElementById('joinurl').textContent=location.origin+'/play?room='+code;
  } else if(m.type==='lobby_state'){
    const d=document.getElementById('players');
    d.innerHTML=m.payload.players.map(p=>{
      const t=m.payload.teams.find(t=>t.id===p.team_id)||{color:'#666'};
      const away=p.connected?'':' (away)';
      return '<span class="p" style="background:'+t.color+'">'+p.name+away+'</span>';
    }).join('');
    document.getElementById('start').disabled = !m.payload.can_start;
  } else if(m.type==='phase_change'){
    log('PHASE → '+m.payload.phase+' (round '+m.payload.round+')');
  } else if(m.type==='reveal_step'){
    (m.payload.beats||[]).forEach(b=>log('  • '+b.text));
    ws.send(JSON.stringify({v:1,type:'next_beat',payload:{}}));
  } else if(m.type==='game_over'){
    log('GAME OVER — winner: '+m.payload.winner_team_id);
  }
};
</script></body></html>"""


_PLAYER_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Doodle Brawl — Play</title>
<style>
 body{font-family:sans-serif;max-width:520px;margin:2rem auto;padding:0 1rem}
 input,button{font-size:1rem;padding:.5rem;margin:.25rem 0}
 button{font-weight:700;border:0;border-radius:6px;background:#4f46e5;color:#fff;cursor:pointer}
 #hp{font-size:1.4rem;font-weight:800}
 #log{margin-top:1rem;font-family:monospace;font-size:.85rem;white-space:pre-wrap;
      background:#111;color:#0f0;padding:1rem;border-radius:6px;height:14rem;overflow:auto}
</style></head>
<body>
 <h1>Doodle Brawl <small style="color:#9ca3af">(Phase 3 placeholder)</small></h1>
 <div id="join">
   <p>Room: <input id="room" maxlength="4" style="text-transform:uppercase"></p>
   <p>Name: <input id="name" placeholder="your name"></p>
   <button id="go">Join</button>
 </div>
 <div id="game" style="display:none">
   <p>You are <b id="me"></b> — HP <span id="hp">?</span></p>
   <p><button id="draw">Submit a doodle</button>
      <label><input type="checkbox" id="auto" checked> auto-submit</label></p>
 </div>
 <div id="log"></div>
<script>
const qs=new URLSearchParams(location.search);
document.getElementById('room').value=(qs.get('room')||'').toUpperCase();
const log=(m)=>{
  const l=document.getElementById('log');
  l.textContent+=m+"\\n"; l.scrollTop=l.scrollHeight;
};
let ws, cur={phase:'',round:0};
document.getElementById('go').onclick=()=>{
  const room=document.getElementById('room').value.toUpperCase();
  const name=document.getElementById('name').value||'Player';
  ws=new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws');
  ws.onopen=()=> ws.send(JSON.stringify({v:1,type:'join',payload:{
     role:'player',name:name,room:room,player_id:localStorage.getItem('pid')}}));
  ws.onclose=()=> log('[disconnected — reload to reconnect]');
  ws.onmessage=onmsg;
};
function submit(){
  ws.send(JSON.stringify({v:1,type:'submit_drawing',payload:{
     phase:cur.phase,round:cur.round,png_base64:'placeholder-doodle'}}));
  log('  submitted '+cur.phase);
}
document.getElementById('draw').onclick=submit;
function onmsg(e){
  const m=JSON.parse(e.data);
  if(m.type==='joined'){
    localStorage.setItem('pid',m.payload.player_id);
    document.getElementById('join').style.display='none';
    document.getElementById('game').style.display='';
    log('joined room '+m.payload.room+' on '+m.payload.team_id);
  } else if(m.type==='phase_change'){
    cur={phase:m.payload.phase,round:m.payload.round};
    log('PHASE → '+m.payload.phase+' (round '+m.payload.round+')');
    if((m.payload.phase==='draw_characters'||m.payload.phase==='draw_action')
        && document.getElementById('auto').checked){ submit(); }
  } else if(m.type==='player_state'){
    document.getElementById('me').textContent=m.payload.name;
    document.getElementById('hp').textContent=m.payload.hp+'/'+m.payload.max_hp
        +(m.payload.is_ko?' (KO)':'');
  } else if(m.type==='game_over'){
    log('GAME OVER — winner: '+m.payload.winner_team_id);
  }
}
</script></body></html>"""
