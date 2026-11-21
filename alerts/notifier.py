#!/usr/bin/env python3
"""
Downdetector — notificador de incidentes (Google Chat + Slack).

Monitora uma watchlist de serviços via API oficial e envia alertas no Google Chat
e/ou Slack quando um serviço entra em outage e quando normaliza (com a duração do
incidente). Usa a API oficial (sem scraping), multi-país. Zero dependências (stdlib).

Config (env):
  DD_API_KEY   (obrigatório)  chave do app (iOS Info.plist ou Android dex)
  DD_WEBHOOK   opcional       webhook(s) do Google Chat, separados por vírgula
  DD_SLACK     opcional       webhook(s) do Slack, separados por vírgula
                              (pelo menos um de DD_WEBHOOK ou DD_SLACK é obrigatório)
  DD_WATCH     opcional       slugs a vigiar (ex: nubank,govbr); vazio = todos do país
  DD_ISO       default BR     país (BR, US, GB, ...)
  DD_LAT/DD_LON default SP    geolocalização p/ o token
  DD_INTERVAL  default 120    segundos entre checagens
  DD_TRIGGER   default danger trigger do alerta: 'danger' ou 'warning' (warning inclui danger)
  DD_RATIO     default 0      se >0, também alerta quando reports/baseline >= RATIO

Uso:
  DD_API_KEY=xxx DD_WEBHOOK="https://chat.googleapis.com/..." DD_SLACK="https://hooks.slack.com/..." \
    DD_WATCH=nubank,govbr python3 notifier.py
"""
import os, sys, json, time, uuid, urllib.request, urllib.parse
from datetime import datetime

API_KEY  = os.environ.get("DD_API_KEY", "").strip()
WEBHOOKS_GCHAT = [w.strip() for w in os.environ.get("DD_WEBHOOK", "").split(",") if w.strip()]
WEBHOOKS_SLACK = [w.strip() for w in os.environ.get("DD_SLACK", "").split(",") if w.strip()]
WATCH    = {s.strip().lower() for s in os.environ.get("DD_WATCH", "").split(",") if s.strip()}
ISO      = os.environ.get("DD_ISO", "BR").upper()
LAT      = os.environ.get("DD_LAT", "-23.55")
LON      = os.environ.get("DD_LON", "-46.63")
INTERVAL = int(os.environ.get("DD_INTERVAL", "120"))
TRIGGER  = os.environ.get("DD_TRIGGER", "danger").lower()
RATIO    = float(os.environ.get("DD_RATIO", "0"))
UA = "SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0"
CONFIG_BASE = "https://consumer-downdetector-api.speedtest.net/v1"
DATA_BASE   = "https://st.downdetectorapi.com/v2"
FIELDS = "id,stats_24,name,status,baseline,indicators,slug,description,logos"
SITE_IDS = {"NL":1,"DE":2,"US":3,"BE":5,"GB":6,"FR":7,"RU":8,"IT":10,"IN":11,"ES":12,"MX":13,
 "CA":15,"AU":16,"BR":18,"AT":21,"ZA":30,"SE":31,"TR":33,"PL":34,"DK":35,"NZ":37,"PT":38,
 "CH":39,"IE":40,"AR":45,"FI":46,"CL":49,"NO":50,"PE":59,"MY":60,"ID":61,"PH":63,"TH":68,"KR":91}
TRIGGER_SET = {"danger"} if TRIGGER == "danger" else {"danger", "warning"}

_token = {"v": None, "exp": 0}
_incidents = {}  # slug -> {started: ts, status: str}


def _req(method, url, headers=None, body=None, timeout=45):
    r = urllib.request.Request(url, method=method, data=body)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def token():
    now = time.time()
    if _token["v"] and now < _token["exp"]:
        return _token["v"]
    u = str(uuid.uuid4())
    body = json.dumps({"device": {"id": u, "guid": u, "idfv": u, "platform": "ios",
                       "model": "iPhone15,2", "version": "7.1.2"}, "app": {"version": "7.1.2"}}).encode()
    q = urllib.parse.urlencode({"latitude": LAT, "longitude": LON})
    d = json.loads(_req("POST", f"{CONFIG_BASE}/dd/config?{q}",
                   {"X-Ookla-Api-Key": API_KEY, "Content-Type": "application/json", "User-Agent": UA}, body))
    _token["v"] = d["accessToken"]; _token["exp"] = now + 13 * 60
    return _token["v"]


def fetch():
    sid = SITE_IDS.get(ISO, 18)
    base = f"{DATA_BASE}/sites/{sid}/companies"
    q = urllib.parse.urlencode({"order_by": "status", "order_direction": "desc",
                                "page_size": 50, "fields": FIELDS})
    auth = {"Authorization": f"Bearer {token()}", "User-Agent": UA}
    pages = json.loads(_req("OPTIONS", f"{base}?{q}", auth)).get("pages", [])
    seen = {}
    for cur in pages:
        try:
            for c in json.loads(_req("GET", f"{base}?{q}&page={urllib.parse.quote(cur)}", auth)):
                if isinstance(c, dict) and "id" in c:
                    if WATCH and c.get("slug", "").lower() not in WATCH:
                        continue
                    seen[c["slug"]] = c
        except Exception:
            continue
        if WATCH and len(seen) >= len(WATCH):
            break
    return seen


def notify(text_gchat, text_slack=None):
    """Envia para Google Chat e/ou Slack."""
    if text_slack is None:
        text_slack = text_gchat
    # Google Chat
    if WEBHOOKS_GCHAT:
        payload = json.dumps({"text": text_gchat}).encode()
        for wh in WEBHOOKS_GCHAT:
            try:
                _req("POST", wh, {"Content-Type": "application/json; charset=UTF-8"}, payload, timeout=15)
            except Exception as e:
                print("falha Google Chat:", e, flush=True)
    # Slack
    if WEBHOOKS_SLACK:
        payload = json.dumps({"text": text_slack}).encode()
        for wh in WEBHOOKS_SLACK:
            try:
                _req("POST", wh, {"Content-Type": "application/json; charset=UTF-8"}, payload, timeout=15)
            except Exception as e:
                print("falha Slack:", e, flush=True)


def fmt_dur(ms):
    m = ms // 60000; h = m // 60; mr = m % 60
    return f"{h}h {mr}min" if h else (f"{m}min" if m else "<1min")


def dd_url(slug):
    dom = {"BR":"downdetector.com.br","US":"downdetector.com","GB":"downdetector.co.uk",
           "PT":"downdetector.pt"}.get(ISO, "downdetector.com")
    return f"https://{dom}/status/{slug}/"


def check():
    data = fetch()
    now = time.time()
    for slug, c in data.items():
        st = c.get("status", "ok")
        s24 = c.get("stats_24") or [0]; base = c.get("baseline") or [0]
        cur, bl = s24[-1], base[-1]
        ratio = (cur / bl) if bl else 0
        triggered = st in TRIGGER_SET or (RATIO and ratio >= RATIO)
        inc = _incidents.get(slug)
        name = c.get("name", slug)
        emoji = "🔴" if st == "danger" else "🟡"
        if triggered and not inc:
            _incidents[slug] = {"started": now, "status": st}
            ts = datetime.now().strftime('%d/%m/%Y %H:%M')
            url = dd_url(slug)
            # Google Chat: *negrito*, <url|texto>, emojis unicode
            gchat = (f"{emoji} *Instabilidade — {name}* ({ISO})\n"
                     f"*Status:* {st}\n*Reports agora:* {cur} (baseline {bl}, {ratio:.1f}x)\n"
                     f"*Início:* {ts}\n<{url}|Ver no Downdetector>")
            # Slack: :emoji:, *negrito*, <url|texto>
            slack_emoji = ":red_circle:" if st == "danger" else ":warning:"
            slack = (f"{slack_emoji} *Instabilidade — {name}* ({ISO})\n"
                     f"• *Status:* `{st}`\n• *Reports agora:* {cur} (baseline {bl}, {ratio:.1f}x)\n"
                     f"• *Início:* {ts}\n<{url}|Ver no Downdetector>")
            notify(gchat, slack)
            print(f"[ALERTA] {name} {st} ({cur} reports)", flush=True)
        elif inc and not triggered and st == "ok":
            dur = fmt_dur(int((now - inc["started"]) * 1000))
            url = dd_url(slug)
            gchat = (f"✅ *Normalizado — {name}* ({ISO})\n"
                     f"*Status:* resolvido\n*Duração:* {dur}\n"
                     f"<{url}|Ver no Downdetector>")
            slack = (f":white_check_mark: *Normalizado — {name}* ({ISO})\n"
                     f"• *Status:* `resolved`\n• *Duração:* {dur}\n"
                     f"<{url}|Ver no Downdetector>")
            notify(gchat, slack)
            print(f"[RESOLVIDO] {name} após {dur}", flush=True)
            _incidents.pop(slug, None)
    return len(data)


def main():
    if not API_KEY:
        sys.exit("ERRO: defina DD_API_KEY.")
    if not WEBHOOKS_GCHAT and not WEBHOOKS_SLACK:
        sys.exit("ERRO: defina DD_WEBHOOK (Google Chat) e/ou DD_SLACK.")
    destinos = []
    if WEBHOOKS_GCHAT: destinos.append(f"gchat:{len(WEBHOOKS_GCHAT)}")
    if WEBHOOKS_SLACK: destinos.append(f"slack:{len(WEBHOOKS_SLACK)}")
    print(f"notifier: país={ISO} watch={sorted(WATCH) or 'todos'} trigger={TRIGGER}"
          f"{f' ratio>={RATIO}' if RATIO else ''} intervalo={INTERVAL}s destinos={','.join(destinos)}", flush=True)
    while True:
        try:
            n = check()
            print(f"checado {n} serviços · {len(_incidents)} incidentes ativos · {datetime.now():%H:%M:%S}", flush=True)
        except Exception as e:
            print("erro no ciclo:", e, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
