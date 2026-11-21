#!/usr/bin/env python3
"""
Downdetector server — consulta a API ao vivo e serve o dashboard.

NÃO usa dados estáticos: cada país é buscado na API do Downdetector sob demanda,
com cache por TTL (consulta de tempo em tempo). Serve também o dashboard web,
que consome /api/* na mesma origem (sem problema de CORS).

  GET /                      -> dashboard
  GET /api/countries         -> lista de países (iso2, siteID, nome, flag)
  GET /api/companies?iso=BR  -> serviços do país ao vivo (cache TTL) + meta
  GET /metrics               -> métricas Prometheus (watchlist via DD_WATCH)

Config (env):
  DD_API_KEY  (obrigatório)  'SpeedTestUserApiKey' do Info.plist do IPA
  DD_PORT     default 8000
  DD_TTL      default 120     cache por país (segundos)
  DD_WATCH    opcional        slugs p/ /metrics (ex: nubank,govbr); vazio=todos do BR
  DD_METRICS_ISO default BR   país das métricas Prometheus
"""
import os, sys, json, time, uuid, threading, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ.get("DD_API_KEY", "").strip()
PORT    = int(os.environ.get("DD_PORT", "8000"))
TTL     = int(os.environ.get("DD_TTL", "120"))
WATCH   = {s.strip().lower() for s in os.environ.get("DD_WATCH", "").split(",") if s.strip()}
METRICS_ISO = os.environ.get("DD_METRICS_ISO", "BR").upper()
UA      = "SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0"
CONFIG_BASE = "https://consumer-downdetector-api.speedtest.net/v1"
DATA_BASE   = "https://st.downdetectorapi.com/v2"
FIELDS      = "id,stats_24,name,status,baseline,indicators,slug,description,logos"
STATUS_NUM  = {"ok": 0, "warning": 1, "danger": 2}

# iso2 -> [siteID, domínio, flag, nome]
COUNTRIES = {
 "NL":[1,"allestoringen.nl","🇳🇱","Holanda"],"DE":[2,"allestörungen.de","🇩🇪","Alemanha"],
 "US":[3,"downdetector.com","🇺🇸","Estados Unidos"],"BE":[5,"allestoringen.be","🇧🇪","Bélgica"],
 "GB":[6,"downdetector.co.uk","🇬🇧","Reino Unido"],"FR":[7,"downdetector.fr","🇫🇷","França"],
 "RU":[8,"downdetector.ru","🇷🇺","Rússia"],"IT":[10,"downdetector.it","🇮🇹","Itália"],
 "IN":[11,"downdetector.in","🇮🇳","Índia"],"ES":[12,"downdetector.es","🇪🇸","Espanha"],
 "MX":[13,"downdetector.mx","🇲🇽","México"],"CA":[15,"downdetector.ca","🇨🇦","Canadá"],
 "AU":[16,"downdetector.com.au","🇦🇺","Austrália"],"BR":[18,"downdetector.com.br","🇧🇷","Brasil"],
 "AT":[21,"allestörungen.at","🇦🇹","Áustria"],"ZA":[30,"downdetector.co.za","🇿🇦","África do Sul"],
 "SE":[31,"downdetector.se","🇸🇪","Suécia"],"TR":[33,"downdetector.tr","🇹🇷","Turquia"],
 "PL":[34,"downdetector.pl","🇵🇱","Polônia"],"DK":[35,"downdetector.dk","🇩🇰","Dinamarca"],
 "NZ":[37,"downdetector.co.nz","🇳🇿","Nova Zelândia"],"PT":[38,"downdetector.pt","🇵🇹","Portugal"],
 "CH":[39,"allestörungen.ch","🇨🇭","Suíça"],"IE":[40,"downdetector.ie","🇮🇪","Irlanda"],
 "AR":[45,"downdetector.com.ar","🇦🇷","Argentina"],"FI":[46,"downdetector.fi","🇫🇮","Finlândia"],
 "CL":[49,"downdetector.cl","🇨🇱","Chile"],"NO":[50,"downdetector.no","🇳🇴","Noruega"],
 "PE":[59,"downdetector.pe","🇵🇪","Peru"],"MY":[60,"downdetector.my","🇲🇾","Malásia"],
 "ID":[61,"downdetector.id","🇮🇩","Indonésia"],"PH":[63,"downdetector.ph","🇵🇭","Filipinas"],
 "TH":[68,"downdetector.th","🇹🇭","Tailândia"],"KR":[91,"kr.downdetector.com","🇰🇷","Coreia do Sul"],
}

_lock = threading.Lock()
_token = {"value": None, "exp": 0}
_cache = {}   # iso -> {"ts":.., "data":[...]}


def _req(method, url, headers=None, body=None, timeout=45):
    req = urllib.request.Request(url, method=method, data=body)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def token():
    now = time.time()
    with _lock:
        if _token["value"] and now < _token["exp"]:
            return _token["value"]
        u = str(uuid.uuid4())
        body = json.dumps({"device": {"id": u, "guid": u, "idfv": u, "platform": "ios",
                           "model": "iPhone15,2", "version": "7.1.2"}, "app": {"version": "7.1.2"}}).encode()
        txt = _req("POST", f"{CONFIG_BASE}/dd/config?latitude=-23.55&longitude=-46.63",
                   {"X-Ookla-Api-Key": API_KEY, "Content-Type": "application/json", "User-Agent": UA}, body)
        _token["value"] = json.loads(txt)["accessToken"]
        _token["exp"] = now + 13 * 60
        return _token["value"]


def fetch_country(iso):
    """Busca todos os serviços de um país ao vivo. Páginas baixadas em PARALELO."""
    sid = COUNTRIES[iso][0]
    at = token()
    base = f"{DATA_BASE}/sites/{sid}/companies"
    q = urllib.parse.urlencode({"order_by": "status", "order_direction": "desc",
                                "page_size": 50, "fields": FIELDS})
    auth = {"Authorization": f"Bearer {at}", "User-Agent": UA}
    pages = json.loads(_req("OPTIONS", f"{base}?{q}", auth)).get("pages", [])

    def one(cur):
        try:
            return json.loads(_req("GET", f"{base}?{q}&page={urllib.parse.quote(cur)}", auth))
        except Exception:
            return []

    seen = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        for arr in ex.map(one, pages):
            for c in arr:
                if isinstance(c, dict) and "id" in c:
                    seen[c["id"]] = c
    return list(seen.values())


_refreshing = set()

def _bg_refresh(iso):
    if iso in _refreshing:
        return
    _refreshing.add(iso)
    try:
        data = fetch_country(iso)
        _cache[iso] = {"ts": time.time(), "data": data}
    except Exception:
        pass
    finally:
        _refreshing.discard(iso)


def country_data(iso):
    """stale-while-revalidate: serve cache na hora; atualiza em background se vencido."""
    iso = iso.upper()
    if iso not in COUNTRIES:
        raise KeyError(iso)
    now = time.time()
    c = _cache.get(iso)
    if c:
        if now - c["ts"] >= TTL:
            threading.Thread(target=_bg_refresh, args=(iso,), daemon=True).start()
        return c["data"], c["ts"], (now - c["ts"] < TTL)
    # primeira vez: precisa buscar sincronicamente
    data = fetch_country(iso)
    _cache[iso] = {"ts": now, "data": data}
    return data, now, False


def compare_service(q):
    """Procura um serviço (por slug ou nome) em TODOS os países e compara."""
    q = q.lower().strip()
    def find(iso):
        try:
            data, ts, _ = country_data(iso)
        except Exception:
            return None
        best = None
        for c in data:
            slug = c.get("slug", "").lower(); name = c.get("name", "").lower()
            if slug == q or name == q:
                best = c; break
            if (q in slug or q in name) and best is None:
                best = c
        if not best:
            return None
        s24 = best.get("stats_24") or [0]; base = best.get("baseline") or [0]
        meta = COUNTRIES[iso]
        return {"iso2": iso, "country": meta[3], "flag": meta[2], "domain": meta[1],
                "id": best.get("id"), "name": best.get("name"), "slug": best.get("slug"),
                "status": best.get("status", "ok"),
                "now": s24[-1], "baseline": base[-1], "sum24": sum(s24),
                "ratio": round(s24[-1] / base[-1], 2) if base[-1] else 0,
                "stats_24": s24}
    with ThreadPoolExecutor(max_workers=10) as ex:
        res = [r for r in ex.map(find, list(COUNTRIES)) if r]
    res.sort(key=lambda x: -x["now"])
    return res


def global_outages(min_countries=2, limit=60):
    """Agrega serviços entre TODOS os países: quais têm mais problemas no mundo."""
    def load(iso):
        try:
            data, _, _ = country_data(iso)
            return iso, data
        except Exception:
            return iso, []
    agg = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for iso, data in ex.map(load, list(COUNTRIES)):
            flag = COUNTRIES[iso][2]
            for c in data:
                slug = c.get("slug", "")
                if not slug:
                    continue
                s24 = c.get("stats_24") or [0]
                a = agg.setdefault(slug, {"slug": slug, "name": c.get("name", slug),
                    "countries": 0, "problem_countries": 0, "danger_countries": 0,
                    "warning_countries": 0, "now": 0, "sum24": 0, "where": []})
                a["countries"] += 1
                a["now"] += s24[-1]
                a["sum24"] += sum(s24)
                st = c.get("status")
                if st != "ok":
                    a["problem_countries"] += 1
                    if st == "danger": a["danger_countries"] += 1
                    elif st == "warning": a["warning_countries"] += 1
                    a["where"].append({"iso2": iso, "flag": flag, "status": st, "now": s24[-1]})
    res = [a for a in agg.values() if a["countries"] >= min_countries]
    # ordena: danger primeiro, depois warning, depois reports agora
    res.sort(key=lambda x: (-x["danger_countries"], -x["warning_countries"], -x["now"]))
    for a in res:
        a["where"].sort(key=lambda w: -w["now"])
    return res[:limit]


def metrics_text():
    data, ts, _ = country_data(METRICS_ISO)
    iso = METRICS_ISO
    out = ["# TYPE downdetector_reports_current gauge",
           "# TYPE downdetector_baseline_current gauge",
           "# TYPE downdetector_status gauge",
           "# TYPE downdetector_ratio_vs_baseline gauge"]
    for c in data:
        if WATCH and c.get("slug","").lower() not in WATCH:
            continue
        s24 = c.get("stats_24") or [0]; base = c.get("baseline") or [0]
        now = s24[-1]; bl = base[-1]; st = c.get("status","ok")
        lbl = f'company="{c.get("name","")}",slug="{c.get("slug","")}",country="{iso}",status="{st}"'
        out.append(f'downdetector_reports_current{{{lbl}}} {now}')
        out.append(f'downdetector_baseline_current{{{lbl}}} {bl}')
        out.append(f'downdetector_status{{{lbl}}} {STATUS_NUM.get(st,0)}')
        out.append(f'downdetector_ratio_vs_baseline{{{lbl}}} {round(now/bl,3) if bl else 0}')
    out.append("downdetector_up 1")
    return "\n".join(out) + "\n"


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        try:
            if u.path in ("/", "/health"):
                return self._send(200, json.dumps({"service": "downdetector-live api", "ok": True,
                    "endpoints": ["/api/countries", "/api/companies?iso=BR", "/api/compare?q=fivem",
                                  "/api/global", "/metrics"]}, ensure_ascii=False))
            if u.path == "/api/countries":
                lst = [{"iso2": k, "siteID": v[0], "domain": v[1], "flag": v[2], "name": v[3]}
                       for k, v in COUNTRIES.items()]
                lst.sort(key=lambda x: x["name"])
                return self._send(200, json.dumps(lst, ensure_ascii=False))
            if u.path == "/api/companies":
                iso = urllib.parse.parse_qs(u.query).get("iso", ["BR"])[0].upper()
                data, ts, cached = country_data(iso)
                meta = COUNTRIES.get(iso, [None, "", "", iso])
                return self._send(200, json.dumps({
                    "iso2": iso, "name": meta[3], "flag": meta[2], "domain": meta[1],
                    "updated": int(ts), "cached": cached, "count": len(data),
                    "companies": data}, ensure_ascii=False))
            if u.path == "/api/global":
                res = global_outages()
                return self._send(200, json.dumps({"count": len(res), "results": res}, ensure_ascii=False))
            if u.path == "/api/compare":
                q = urllib.parse.parse_qs(u.query).get("q", [""])[0]
                if not q.strip():
                    return self._send(400, json.dumps({"error": "faltou ?q="}))
                res = compare_service(q)
                return self._send(200, json.dumps({"query": q, "countries": len(res), "results": res}, ensure_ascii=False))
            if u.path == "/metrics":
                return self._send(200, metrics_text(), "text/plain; version=0.0.4; charset=utf-8")
            self._send(404, json.dumps({"error": "not found"}))
        except KeyError as e:
            self._send(404, json.dumps({"error": f"país desconhecido: {e}"}))
        except Exception as e:
            self._send(502, json.dumps({"error": str(e)}))

    def log_message(self, *a):
        pass


def main():
    if not API_KEY:
        sys.exit("ERRO: defina DD_API_KEY (a 'SpeedTestUserApiKey' do Info.plist do IPA).")
    print(f"Downdetector server em http://localhost:{PORT}  (dashboard + API ao vivo)")
    print(f"  {len(COUNTRIES)} países · cache {TTL}s · /metrics país={METRICS_ISO} watch={sorted(WATCH) or 'todos'}", flush=True)
    # pré-aquece os países mais usados em background (primeira tela abre instantânea)
    for w in ("BR", "US", METRICS_ISO):
        threading.Thread(target=_bg_refresh, args=(w,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
