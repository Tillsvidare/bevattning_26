# WiFi update mode: access point + tiny HTTP file server.
#
# Started by boot.py when the button is held at power-on (before main.py
# runs). Brings up an open access point and serves a single-page file
# manager at http://192.168.4.1 where .py/.json files can be uploaded,
# downloaded and deleted from a phone browser. Runs forever; reset the
# device (without the button held) to resume normal operation.
#
# A DNS responder answers every query with the AP's own IP so any typed
# address reaches the device. The phone's connectivity probes get an
# "internet works" answer (probe_ok) so no sign-in view opens — its mini
# browser blocks the file chooser, so this page must be used in a real
# browser at http://192.168.4.1. wifi_setup.py shares serve_forever() and
# the socket helpers but redirects the probes instead (redirect_portal),
# so its form opens automatically in the sign-in view.

import machine
import network
import os
import select
import socket
import time

AP_SSID = "bevattning"  # open network (no password)
PORTAL_IP = "192.168.4.1"  # ESP32 default AP address
# Hostnames wifi_setup serves its form on; other hosts are treated as
# connectivity probes and redirected (which opens the sign-in view).
PORTAL_NAMES = (PORTAL_IP, AP_SSID, AP_SSID + ".local")
# Well-known connectivity-probe paths. Update mode answers these with
# "internet works" (probe_ok) and serves the file manager on EVERY other
# request regardless of hostname — the DNS responder resolves all names
# to us, so any http address typed in the browser reaches the page.
PROBE_PATHS = ("/generate_204", "/gen_204", "/hotspot-detect.html",
               "/library/test/success.html", "/connecttest.txt",
               "/ncsi.txt", "/success.txt")
HTTP_PORT = 80
DNS_PORT = 53
MAX_UPLOAD = 256 * 1024  # sanity cap for Content-Length
RECV_CHUNK = 512

# Files that must never be deleted via the web page (deleting main.py would
# brick the update mode bootstrap; config.json is harmless but protected
# uploads can still overwrite it intentionally).
PROTECTED = ("main.py", "boot.py", "wifi_update.py", "ota_update.py")

_PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>provpump - uppdatering</title>
<style>
body { font-family: sans-serif; margin: 1em; max-width: 40em; }
h1 { font-size: 1.3em; }
table { border-collapse: collapse; width: 100%%; }
td, th { text-align: left; padding: 0.3em 0.6em 0.3em 0; }
td.num { text-align: right; }
button { padding: 0.5em 1em; margin: 0.2em 0; }
#status li.err { color: #b00; }
.warn { color: #b00; font-weight: bold; }
footer { margin-top: 2em; color: #666; font-size: 0.85em; }
</style>
</head>
<body>
<h1>provpump &mdash; uppdateringsl&auml;ge</h1>
<p>Ledigt utrymme: %s</p>
<table>
<tr><th>Fil</th><th>Storlek</th><th></th></tr>
%s
</table>
<h2>Ladda upp filer</h2>
<p><input type="file" id="files" multiple></p>
<p><button onclick="upload()">Ladda upp</button></p>
<ul id="status"></ul>
<h2>Klart?</h2>
<p><button onclick="reboot()">Starta om i normall&auml;ge</button></p>
<footer>Uppladdade filer ers&auml;tter befintliga med samma namn.
Omstart st&auml;nger uppdateringsl&auml;get.</footer>
<script>
function log(msg, err) {
  var li = document.createElement('li');
  li.textContent = msg;
  if (err) li.className = 'err';
  document.getElementById('status').appendChild(li);
}
async function upload() {
  var files = document.getElementById('files').files;
  if (!files.length) { log('Inga filer valda', true); return; }
  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    try {
      var r = await fetch('/file/' + encodeURIComponent(f.name),
                          {method: 'PUT', body: await f.arrayBuffer()});
      log(f.name + ': ' + (r.ok ? 'OK' : 'FEL ' + r.status + ' ' + await r.text()), !r.ok);
    } catch (e) {
      log(f.name + ': FEL ' + e, true);
    }
  }
  setTimeout(function () { location.reload(); }, 1500);
}
async function del(name) {
  if (!confirm('Ta bort ' + name + '?')) return;
  var r = await fetch('/file/' + encodeURIComponent(name), {method: 'DELETE'});
  if (r.ok) location.reload(); else log(name + ': FEL ' + await r.text(), true);
}
async function reboot() {
  await fetch('/reset', {method: 'POST'});
  document.body.innerHTML = '<p>Startar om &mdash; du kan st&auml;nga sidan.</p>';
}
</script>
</body>
</html>
"""


class _Reader:
    """Minimal buffered reader on top of socket.recv (works on both
    MicroPython and CPython sockets)."""

    def __init__(self, conn):
        self._conn = conn
        self._buf = b""

    def readline(self):
        while b"\n" not in self._buf:
            data = self._conn.recv(RECV_CHUNK)
            if not data:
                break
            self._buf += data
        i = self._buf.find(b"\n")
        if i < 0:
            line, self._buf = self._buf, b""
        else:
            line, self._buf = self._buf[: i + 1], self._buf[i + 1 :]
        return line.rstrip(b"\r\n")

    def read(self, n):
        while len(self._buf) < n:
            data = self._conn.recv(RECV_CHUNK)
            if not data:
                break
            self._buf += data
        data, self._buf = self._buf[:n], self._buf[n:]
        return data


def _unquote(s):
    parts = s.split("%")
    out = parts[0]
    for p in parts[1:]:
        try:
            out += chr(int(p[:2], 16)) + p[2:]
        except ValueError:
            out += "%" + p
    return out


def _safe_name(path):
    """Extract and validate a filename from /file/<name>; None if invalid."""
    name = _unquote(path[len("/file/") :])
    if not name or len(name) > 64:
        return None
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    return name


def _send(conn, status, ctype, body):
    if isinstance(body, str):
        body = body.encode()
    conn.send(
        b"HTTP/1.0 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
        % (status, ctype, len(body))
    )
    conn.send(body)


def _send_text(conn, status, text):
    _send(conn, status, b"text/plain; charset=utf-8", text)


def _fmt_size(n):
    if n >= 1024:
        return "%d kB" % (n // 1024)
    return "%d B" % n


def _page():
    rows = []
    for name in sorted(os.listdir()):
        try:
            st = os.stat(name)
        except OSError:
            continue
        if st[0] & 0x4000:  # directory
            continue
        delete = ""
        if name not in PROTECTED:
            delete = '<button onclick="del(\'%s\')">Ta bort</button>' % name
        rows.append(
            '<tr><td><a href="/file/%s">%s</a></td><td class="num">%s</td><td>%s</td></tr>'
            % (name, name, _fmt_size(st[6]), delete)
        )
    st = os.statvfs("/")
    free = _fmt_size(st[0] * st[3])
    return _PAGE % (free, "\n".join(rows))


def _put_file(conn, reader, name, length):
    if length <= 0:
        _send_text(conn, b"411 Length Required", "Content-Length saknas")
        return
    if length > MAX_UPLOAD:
        _send_text(conn, b"413 Payload Too Large", "Filen ar for stor")
        return
    tmp = name + ".tmp"
    written = 0
    with open(tmp, "wb") as f:
        while written < length:
            chunk = reader.read(min(RECV_CHUNK, length - written))
            if not chunk:
                break
            f.write(chunk)
            written += len(chunk)
    if written != length:
        os.remove(tmp)
        _send_text(conn, b"400 Bad Request", "Avbruten overforing")
        return
    # Replace atomically so a failed transfer never leaves a truncated file.
    try:
        os.remove(name)
    except OSError:
        pass
    os.rename(tmp, name)
    _send_text(conn, b"200 OK", "OK")


def _get_file(conn, name):
    try:
        st = os.stat(name)
    except OSError:
        _send_text(conn, b"404 Not Found", "Filen finns inte")
        return
    conn.send(
        b"HTTP/1.0 200 OK\r\nContent-Type: application/octet-stream\r\n"
        b"Content-Length: %d\r\nConnection: close\r\n\r\n" % st[6]
    )
    with open(name, "rb") as f:
        while True:
            chunk = f.read(RECV_CHUNK)
            if not chunk:
                break
            conn.send(chunk)


def read_request(reader):
    """Parse request line + headers -> (method, path, content-length, host).
    host is the Host header value without port ("" if absent/unreadable)."""
    request = reader.readline()
    parts = request.split()
    if len(parts) < 2:
        return None
    length = 0
    host = ""
    while True:
        header = reader.readline()
        if not header:
            break
        low = header.lower()
        if low.startswith(b"content-length:"):
            length = int(header.split(b":", 1)[1])
        elif low.startswith(b"host:"):
            try:
                host = header.split(b":", 1)[1].strip().split(b":")[0].decode()
            except (UnicodeError, ValueError):
                pass
    return parts[0].decode(), parts[1].decode(), length, host


def redirect_portal(conn):
    """302 to the portal page. Sent for any foreign hostname, which is how
    the phone's connectivity probe learns there is a captive portal and
    pops up the sign-in view. Used by wifi_setup: its form works fine in
    the sign-in mini browser."""
    conn.send(
        b"HTTP/1.0 302 Found\r\nLocation: http://%s/\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n" % PORTAL_IP.encode()
    )


_PROBE_SUCCESS = ("<HTML><HEAD><TITLE>Success</TITLE></HEAD>"
                  "<BODY>Success</BODY></HTML>")


def probe_ok(conn, path):
    """Answer the phone's connectivity probe with 'internet works', so NO
    sign-in view opens. Used by update mode: the sign-in mini browser
    blocks the file chooser, so the page must be used in a real browser
    instead. Each OS expects its own exact success response."""
    if path in ("/generate_204", "/gen_204"):        # Android
        conn.send(b"HTTP/1.0 204 No Content\r\nConnection: close\r\n\r\n")
    elif path == "/connecttest.txt":                 # Windows
        _send_text(conn, b"200 OK", "Microsoft Connect Test")
    elif path == "/ncsi.txt":                        # Windows (äldre)
        _send_text(conn, b"200 OK", "Microsoft NCSI")
    elif path == "/success.txt":                     # Firefox
        _send_text(conn, b"200 OK", "success")
    else:                                            # iOS/macOS m.fl.
        _send(conn, b"200 OK", b"text/html", _PROBE_SUCCESS)


def _handle(conn):
    reader = _Reader(conn)
    req = read_request(reader)
    if not req:
        return
    method, path, length, host = req
    if path in PROBE_PATHS:
        probe_ok(conn, path)
        return

    if method == "GET" and path == "/":
        _send(conn, b"200 OK", b"text/html; charset=utf-8", _page())
    elif path.startswith("/file/"):
        name = _safe_name(path)
        if not name:
            _send_text(conn, b"400 Bad Request", "Ogiltigt filnamn")
        elif method == "PUT":
            _put_file(conn, reader, name, length)
        elif method == "GET":
            _get_file(conn, name)
        elif method == "DELETE":
            if name in PROTECTED:
                _send_text(conn, b"403 Forbidden", "Skyddad fil")
            else:
                try:
                    os.remove(name)
                    _send_text(conn, b"200 OK", "OK")
                except OSError:
                    _send_text(conn, b"404 Not Found", "Filen finns inte")
        else:
            _send_text(conn, b"405 Method Not Allowed", "Stods inte")
    elif method == "POST" and path == "/reset":
        _send_text(conn, b"200 OK", "Startar om")
        conn.close()
        time.sleep(0.3)
        machine.reset()
    else:
        _send_text(conn, b"404 Not Found", "Finns inte")


def _start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(ssid=AP_SSID, authmode=network.AUTH_OPEN)
    while not ap.active():
        time.sleep(0.1)
    return ap


def _dns_reply(query, ip):
    """Minimal DNS responder: answer any query with a single A record
    pointing at our own IP. Returns None for garbage queries."""
    if len(query) < 13:
        return None
    i = 12  # skip the fixed header, then walk the QNAME labels
    while i < len(query) and query[i]:
        i += 1 + query[i]
    i += 5  # terminating zero + QTYPE (2) + QCLASS (2)
    if i > len(query):
        return None
    return (
        query[:2]                    # transaction ID
        + b"\x81\x80"                # response, recursion available, no error
        + query[4:6] + query[4:6]    # QDCOUNT, ANCOUNT = same as query
        + b"\x00\x00\x00\x00"        # NSCOUNT, ARCOUNT
        + query[12:i]                # original question
        + b"\xc0\x0c"                # answer name: pointer to question
        + b"\x00\x01\x00\x01"        # type A, class IN
        + b"\x00\x00\x00\x3c"        # TTL 60 s
        + b"\x00\x04"                # 4 address bytes
        + bytes(int(x) for x in ip.split("."))
    )


def serve_forever(handle):
    """Bring up the AP and serve HTTP + captive-portal DNS forever.

    handle(conn) serves one connection; if it returns truthy the device is
    reset after the connection closes (used by wifi_setup after a saved
    config). Only returns via machine.reset()."""
    ap = _start_ap()
    ip = ap.ifconfig()[0]

    tcp = socket.socket()
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("0.0.0.0", HTTP_PORT))
    tcp.listen(2)

    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    dns.bind(("0.0.0.0", DNS_PORT))

    print('anslut till WiFi "%s", adress http://%s' % (AP_SSID, ip))
    while True:
        try:
            readable, _, _ = select.select([tcp, dns], [], [])
            if dns in readable:
                query, addr = dns.recvfrom(256)
                reply = _dns_reply(query, ip)
                if reply:
                    dns.sendto(reply, addr)
            if tcp in readable:
                conn, addr = tcp.accept()
                reset = False
                try:
                    reset = handle(conn)
                except Exception as e:
                    print("request error: %s" % e)
                finally:
                    conn.close()
                if reset:
                    time.sleep(0.3)
                    machine.reset()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print("serve error: %s" % e)


def serve():
    """Bring up the access point and serve update requests forever."""
    print("UPDATE MODE: oppna http://%s/ (eller http://%s) "
          "i en vanlig webblasare" % (AP_SSID, PORTAL_IP))
    serve_forever(_handle)
