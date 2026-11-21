#!/usr/bin/env python3
"""
Downdetector Prometheus exporter.

Expõe métricas de status/reports de serviços do Downdetector (via API do app Speedtest)
em /metrics, para o Prometheus fazer scrape. Zero dependências (stdlib apenas).

Fonte dos dados (confirmado por engenharia reversa do Speedtest iOS v7.1.2):
  1. POST consumer-downdetector-api.speedtest.net/v1/dd/config  -> accessToken + siteID
  2. OPTIONS st.downdetectorapi.com/v2/sites/{siteID}/companies  -> cursores de página
  3. GET     .../companies?...&page={cursor}                     -> serviços (status/stats_24/baseline)

Config por variável de ambiente:
  DD_API_KEY   (obrigatório)  a 'SpeedTestUserApiKey' do Info.plist do IPA
  DD_LAT       default -23.55  latitude (define o país via geolocalização)
  DD_LON       default -46.63  longitude
  DD_SITE_ID   opcional        força um siteID (ignora lat/lon na seleção de país)
  DD_WATCH     opcional        slugs separados por vírgula (ex: nubank,govbr,claro).
                               vazio = TODOS os serviços do país (cuidado em país grande)
  DD_TTL       default 120     segundos de cache da coleta (não bate na API a cada scrape)
  DD_PORT      default 9693    porta do /metrics
  DD_UA        default SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0

Uso:
  DD_API_KEY=xxxxx DD_WATCH=nubank,govbr python3 downdetector_exporter.py
  curl localhost:9693/metrics
"""
import os, sys, json, time, uuid, threading, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ.get("DD_API_KEY", "").strip()
LAT     = os.environ.get("DD_LAT", "-23.55")
LON     = os.environ.get("DD_LON", "-46.63")
SITE_ID = os.environ.get("DD_SITE_ID", "").strip()
WATCH   = {s.strip().lower() for s in os.environ.get("DD_WATCH", "").split(",") if s.strip()}
TTL     = int(os.environ.get("DD_TTL", "120"))
PORT    = int(os.environ.get("DD_PORT", "9693"))
UA      = os.environ.get("DD_UA", "SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0")

CONFIG_BASE = "https://consumer-downdetector-api.speedtest.net/v1"
DATA_BASE   = "https://st.downdetectorapi.com/v2"
FIELDS      = "id,stats_24,name,status,baseline,indicators,slug,description,logos"
STATUS_NUM  = {"ok": 0, "warning": 1, "danger": 2}

_lock = threading.Lock()
_cache = {"ts": 0, "metrics": "", "ok": False}
_token = {"value": None, "exp": 0, "site_id": None, "country": None}


def _req(method, url, headers=None, body=None, timeout=40):
    req = urllib.request.Request(url, method=method, data=body)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)


def ensure_token():
    """Renova o accessToken via /dd/config quando expira (~15min). Serve p/ todos os países."""
    now = time.time()
    if _token["value"] and now < _token["exp"] - 60:
        return
    u = str(uuid.uuid4())
    body = json.dumps({
        "device": {"id": u, "guid": u, "idfv": u, "platform": "ios",
                   "model": "iPhone15,2", "version": "7.1.2"},
        "app": {"version": "7.1.2"},
    }).encode()
    q = urllib.parse.urlencode({"latitude": LAT, "longitude": LON})
    _, txt, _ = _req("POST", f"{CONFIG_BASE}/dd/config?{q}",
                     {"X-Ookla-Api-Key": API_KEY, "Content-Type": "application/json",
                      "User-Agent": UA}, body)
    d = json.loads(txt)
    _token["value"] = d["accessToken"]
    _token["exp"] = now + 13 * 60
    _token["country"] = d["countryInfo"]
    _token["site_id"] = int(SITE_ID) if SITE_ID else d["countryInfo"]["siteID"]


def fetch_companies():
    ensure_token()
    sid = _token["site_id"]
    base = f"{DATA_BASE}/sites/{sid}/companies"
    q = urllib.parse.urlencode({"order_by": "status", "order_direction": "desc",
                                "page_size": 50, "fields": FIELDS})
    auth = {"Authorization": f"Bearer {_token['value']}", "User-Agent": UA}
    _, opt, _ = _req("OPTIONS", f"{base}?{q}", auth)
    pages = json.loads(opt).get("pages", [])
    out = {}
    for cur in pages:
        try:
            _, txt, _ = _req("GET", f"{base}?{q}&page={urllib.parse.quote(cur)}", auth)
            for c in json.loads(txt):
                if isinstance(c, dict) and "id" in c:
                    if WATCH and c.get("slug", "").lower() not in WATCH:
                        continue
                    out[c["id"]] = c
        except Exception:
            continue
        if WATCH and len(out) >= len(WATCH):
            break  # já achou todos da watchlist; para cedo em país grande
    return out.values()


def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def build_metrics():
    t0 = time.time()
    lines = [
        "# HELP downdetector_reports_current Reports de usuário no último intervalo (15min).",
        "# TYPE downdetector_reports_current gauge",
        "# HELP downdetector_baseline_current Baseline (nível normal) no último intervalo.",
        "# TYPE downdetector_baseline_current gauge",
        "# HELP downdetector_reports_24h_total Soma dos reports nas últimas 24h.",
        "# TYPE downdetector_reports_24h_total gauge",
        "# HELP downdetector_status Status do serviço (0=ok 1=warning 2=danger).",
        "# TYPE downdetector_status gauge",
        "# HELP downdetector_ratio_vs_baseline reports_current / baseline_current.",
        "# TYPE downdetector_ratio_vs_baseline gauge",
    ]
    comps = list(fetch_companies())
    iso = (_token["country"] or {}).get("iso2", "?")
    n = 0
    for c in comps:
        s24 = c.get("stats_24") or []
        base = c.get("baseline") or []
        now = s24[-1] if s24 else 0
        bl = base[-1] if base else 0
        total = sum(s24)
        st = c.get("status", "ok")
        lbl = (f'company="{esc(c.get("name",""))}",slug="{esc(c.get("slug",""))}",'
               f'country="{iso}",status="{esc(st)}"')
        lines.append(f'downdetector_reports_current{{{lbl}}} {now}')
        lines.append(f'downdetector_baseline_current{{{lbl}}} {bl}')
        lines.append(f'downdetector_reports_24h_total{{{lbl}}} {total}')
        lines.append(f'downdetector_status{{{lbl}}} {STATUS_NUM.get(st, 0)}')
        lines.append(f'downdetector_ratio_vs_baseline{{{lbl}}} {round(now/bl,3) if bl else 0}')
        n += 1
    dur = time.time() - t0
    lines += [
        "# HELP downdetector_up 1 se a última coleta funcionou.",
        "# TYPE downdetector_up gauge", f"downdetector_up 1",
        "# HELP downdetector_services_scraped Número de serviços coletados.",
        "# TYPE downdetector_services_scraped gauge", f"downdetector_services_scraped {n}",
        "# HELP downdetector_scrape_duration_seconds Duração da coleta.",
        "# TYPE downdetector_scrape_duration_seconds gauge",
        f"downdetector_scrape_duration_seconds {round(dur,3)}",
    ]
    return "\n".join(lines) + "\n"


def get_metrics():
    with _lock:
        if time.time() - _cache["ts"] < TTL and _cache["metrics"]:
            return _cache["metrics"]
        try:
            m = build_metrics()
            _cache.update(ts=time.time(), metrics=m, ok=True)
            return m
        except Exception as e:
            _cache["ok"] = False
            stale = _cache["metrics"] or ""
            return stale + f'downdetector_up 0\n# scrape_error: {esc(e)}\n'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = get_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        elif self.path in ("/", "/health"):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"downdetector-exporter ok. GET /metrics\n")
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass  # silencioso


def main():
    if not API_KEY:
        sys.exit("ERRO: defina DD_API_KEY (a 'SpeedTestUserApiKey' do Info.plist do IPA).")
    print(f"downdetector-exporter em :{PORT}/metrics | país lat={LAT},lon={LON}"
          f"{' siteID='+SITE_ID if SITE_ID else ''} | watch={sorted(WATCH) or 'TODOS'} | ttl={TTL}s",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
