# Lokalt webbgränssnitt (Microdot) för att se och redigera bevattningsschemat.
#
# Minnesdisciplin: EN liten statisk HTML-sida (ingen mall-motor, ingen
# Chart.js på enheten), JSON-API för data, gc.collect() efter varje request.

import gc

from microdot import Microdot

app = Microdot()

# Sätts av main.init_webui() — modulen har inga egna beroenden vid import.
_deps = {}


def init(get_schedule, apply_entries, publish_schedule, get_cloud, set_cloud,
         get_irrigation, set_irrigation, publish_irrigation, get_sensor):
    """apply_entries(vid, list) validerar+sparar; publish_schedule ekar /status;
    get_cloud() -> {"enabled","connected"}; set_cloud(bool) togglar molnsynk;
    get_irrigation() -> bool; set_irrigation(bool) sparar huvudbrytaren;
    publish_irrigation() ekar den till molnet; get_sensor() -> bool (våt)."""
    _deps["get_schedule"] = get_schedule
    _deps["apply_entries"] = apply_entries
    _deps["publish_schedule"] = publish_schedule
    _deps["get_cloud"] = get_cloud
    _deps["set_cloud"] = set_cloud
    _deps["get_irrigation"] = get_irrigation
    _deps["set_irrigation"] = set_irrigation
    _deps["publish_irrigation"] = publish_irrigation
    _deps["get_sensor"] = get_sensor


_PAGE = b"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bevattning</title>
<style>
body{font-family:sans-serif;margin:1em;max-width:30em}
fieldset{border:1px solid #ccc;border-radius:6px;margin:0 0 1em;padding:.75em}
.row{display:flex;gap:.5em;align-items:center;margin:.4em 0}
input[type=time],input[type=number]{padding:.3em;font:inherit}
input[type=number]{width:4em}
button{padding:.4em 1em;font:inherit}
.x{border:none;background:none;color:#b00;font-size:1.1em}
.add{display:block;margin:.5em 0}
#status{min-height:1.2em;color:#060}
#status.err{color:#b00}
.cloud{border:1px solid #ccc;border-radius:6px;padding:.6em .75em;margin:0 0 1em}
#cstat{color:#666;font-size:.9em}
</style></head><body>
<h1>Bevattningsschema</h1>
<div class="cloud">
<label><input type="checkbox" id="irr"> <b>Bevattning</b></label>
<span id="istat"></span>
</div>
<div class="cloud">
Vattensensor: <span id="sstat">...</span>
</div>
<div class="cloud">
<label><input type="checkbox" id="cloud"> Molnsynk (MQTT)</label>
<span id="cstat"></span>
</div>
<form id="f">
<fieldset><legend>Ventil 1</legend><div id="v1"></div>
<button type="button" class="add" onclick="add('1')">+ L&auml;gg till</button></fieldset>
<fieldset><legend>Ventil 2</legend><div id="v2"></div>
<button type="button" class="add" onclick="add('2')">+ L&auml;gg till</button></fieldset>
<button type="submit">Spara</button> <span id="status"></span>
</form>
<script>
var MAX=6,st=document.getElementById('status');
function row(e){var d=document.createElement('div');d.className='row';
 d.innerHTML='<input type="time" required><input type="number" min="1" max="180" required>'+
  ' min <input type="checkbox"><button type="button" class="x">&#10005;</button>';
 var i=d.querySelectorAll('input');
 i[0].value=e.start;i[1].value=e.duration_min;i[2].checked=e.enabled;
 d.querySelector('.x').onclick=function(){d.remove()};return d}
function add(v){var b=document.getElementById('v'+v);
 if(b.children.length>=MAX){st.className='err';st.textContent='Max '+MAX+' per dygn';return}
 b.appendChild(row({start:'06:00',duration_min:15,enabled:true}))}
fetch('/api/schedule').then(function(r){return r.json()}).then(function(s){
 for(var v=1;v<=2;v++){var b=document.getElementById('v'+v);
  (s[String(v)]||[]).forEach(function(e){b.appendChild(row(e))})}});
function toggle(id,statId,url,show){
 var box=document.getElementById(id),stat=document.getElementById(statId);
 function render(s){show(box,stat,s)}
 function poll(){fetch(url).then(function(r){return r.json()})
  .then(render).catch(function(){stat.textContent=''})}
 box.onchange=function(){stat.textContent='...';
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({enabled:box.checked})})
  .then(function(r){return r.json()}).then(render)
  .catch(function(){stat.textContent='fel'})};
 poll();setInterval(poll,10000)}
toggle('irr','istat','/api/irrigation',function(box,stat,s){
 box.checked=s.enabled;stat.textContent=s.enabled?'p\\u00e5':'AVST\\u00c4NGD';
 stat.style.color=s.enabled?'#666':'#b00'});
toggle('cloud','cstat','/api/cloud',function(box,stat,s){
 box.checked=s.enabled;
 stat.textContent=s.enabled?(s.connected?'ansluten':'ansluter...'):'lokal drift'});
function pollSensor(){fetch('/api/sensor').then(function(r){return r.json()})
 .then(function(s){var el=document.getElementById('sstat');
  el.textContent=s.wet?'V\\u00c5T \\u2014 bevattning stoppad':'torr';
  el.style.color=s.wet?'#b00':'#666';
  el.style.fontWeight=s.wet?'bold':'normal'})
 .catch(function(){})}
pollSensor();setInterval(pollSensor,10000);
document.getElementById('f').onsubmit=function(ev){ev.preventDefault();
 var body={};
 for(var v=1;v<=2;v++){body[String(v)]=[].map.call(
  document.getElementById('v'+v).children,function(d){var i=d.querySelectorAll('input');
   return {start:i[0].value,duration_min:parseInt(i[1].value),enabled:i[2].checked}})}
 st.className='';st.textContent='Sparar...';
 fetch('/api/schedule',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 .then(function(r){if(!r.ok)throw new Error(r.status);st.textContent='Sparat'})
 .catch(function(e){st.className='err';st.textContent='Fel: '+e.message})};
</script></body></html>
"""


@app.after_request
async def _collect(request, response):
    gc.collect()
    return response


@app.get("/")
async def index(request):
    return _PAGE, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/api/schedule")
async def get_schedule(request):
    return _deps["get_schedule"]()


@app.get("/api/cloud")
async def get_cloud(request):
    return _deps["get_cloud"]()


@app.get("/api/irrigation")
async def get_irrigation(request):
    return {"enabled": _deps["get_irrigation"]()}


@app.get("/api/sensor")
async def get_sensor(request):
    return {"wet": _deps["get_sensor"]()}


@app.post("/api/irrigation")
async def set_irrigation(request):
    data = request.json
    if not isinstance(data, dict) or "enabled" not in data:
        return {"error": "förväntar {\"enabled\": true/false}"}, 400
    _deps["set_irrigation"](bool(data["enabled"]))
    # Lokal ändring -> eka nya läget till molnet (no-op i lokal drift).
    await _deps["publish_irrigation"]()
    return {"enabled": _deps["get_irrigation"]()}


@app.post("/api/cloud")
async def set_cloud(request):
    data = request.json
    if not isinstance(data, dict) or "enabled" not in data:
        return {"error": "förväntar {\"enabled\": true/false}"}, 400
    _deps["set_cloud"](bool(data["enabled"]))
    return _deps["get_cloud"]()


@app.post("/api/schedule")
async def set_schedule(request):
    data = request.json
    if not isinstance(data, dict):
        return {"error": "ogiltig JSON"}, 400
    changed = []
    for valve_id in ("1", "2"):
        if valve_id in data:
            try:
                _deps["apply_entries"](valve_id, data[valve_id])
            except (ValueError, TypeError) as e:
                return {"error": "ventil %s: %s" % (valve_id, e)}, 400
            changed.append(valve_id)
    # Lokal ändring -> publicera nya statusen till molnet (kravet i punkt 2).
    for valve_id in changed:
        await _deps["publish_schedule"](valve_id)
    return _deps["get_schedule"]()
