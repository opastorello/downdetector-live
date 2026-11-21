"""
Cliente da API Downdetector (via Speedtest/Ookla).
Fluxo REAL confirmado por captura de tráfego do app iOS v7.1.2.

Passos:
  1. POST consumer-downdetector-api.speedtest.net/v1/dd/config  (X-Ookla-Api-Key) -> accessToken + siteID
  2. OPTIONS st.downdetectorapi.com/v2/sites/{siteID}/companies  (Bearer) -> {"pages": [cursores]}
  3. GET     st.downdetectorapi.com/v2/sites/{siteID}/companies?...&page={cursor} -> [serviços]

A X-Ookla-Api-Key é a 'SpeedTestUserApiKey' do Info.plist do app (não incluída aqui).
Requer: httpx
"""
from __future__ import annotations
import uuid, httpx
from dataclasses import dataclass, field

CONFIG_BASE = "https://consumer-downdetector-api.speedtest.net/v1"
DATA_BASE   = "https://st.downdetectorapi.com/v2"
UA          = "SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0"
FIELDS      = "id,stats_24,name,status,baseline,indicators,slug,description,logos"


@dataclass
class Company:
    id: int
    name: str
    slug: str
    status: str                      # ok | warning | danger
    baseline: list = field(default_factory=list)   # 96 pts
    stats_24: list = field(default_factory=list)    # 96 pts
    indicators: list = field(default_factory=list)  # nomes dos problemas
    description: str = ""
    logos: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, d: dict) -> "Company":
        return cls(d["id"], d.get("name",""), d.get("slug",""), d.get("status",""),
                   d.get("baseline",[]), d.get("stats_24",[]), d.get("indicators",[]),
                   d.get("description",""), d.get("logos",{}))


class DowndetectorClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.http = httpx.Client(timeout=40, headers={"User-Agent": UA})
        self.token: str | None = None
        self.site_id: int | None = None
        self.country: dict | None = None

    def config(self, latitude: float, longitude: float) -> dict:
        """Passo 1: bootstrap. Define país pela geolocalização; retorna token + siteID."""
        u = str(uuid.uuid4())
        body = {"device": {"id": u, "guid": u, "idfv": u, "platform": "ios",
                           "model": "iPhone15,2", "version": "7.1.2"},
                "app": {"version": "7.1.2"}}
        r = self.http.post(f"{CONFIG_BASE}/dd/config",
                           params={"latitude": latitude, "longitude": longitude},
                           headers={"X-Ookla-Api-Key": self.api_key},
                           json=body)
        r.raise_for_status()
        d = r.json()
        self.token = d["accessToken"]
        self.country = d["countryInfo"]
        self.site_id = self.country["siteID"]
        return d

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def page_cursors(self, site_id: int | None = None,
                     order_by="status", order_direction="desc", page_size=50) -> list[str]:
        """Passo 2: OPTIONS devolve os cursores de paginação do país."""
        sid = site_id or self.site_id
        params = {"order_by": order_by, "order_direction": order_direction,
                  "page_size": page_size, "fields": FIELDS}
        r = self.http.request("OPTIONS", f"{DATA_BASE}/sites/{sid}/companies",
                              params=params, headers=self._auth())
        r.raise_for_status()
        return r.json().get("pages", [])

    def companies(self, site_id: int | None = None,
                  order_by="status", order_direction="desc", page_size=50) -> list[Company]:
        """Passo 3: baixa TODAS as páginas do país e retorna a lista completa."""
        sid = site_id or self.site_id
        params = {"order_by": order_by, "order_direction": order_direction,
                  "page_size": page_size, "fields": FIELDS}
        out: dict[int, Company] = {}
        for cur in self.page_cursors(sid, order_by, order_direction, page_size):
            r = self.http.get(f"{DATA_BASE}/sites/{sid}/companies",
                              params={**params, "page": cur}, headers=self._auth())
            if r.status_code != 200:
                continue
            for c in r.json():
                if isinstance(c, dict) and "id" in c:
                    out[c["id"]] = Company.parse(c)
        return list(out.values())

    def indicators(self, company_id: int) -> list:
        r = self.http.get(f"{DATA_BASE}/companies/{company_id}/indicators", headers=self._auth())
        r.raise_for_status(); return r.json()

    def heatmap(self, slug: str) -> dict:
        r = self.http.get(f"{DATA_BASE}/slugs/{slug}/heatmap/current", headers=self._auth())
        r.raise_for_status(); return r.json()

    def close(self): self.http.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


if __name__ == "__main__":
    # A key é a 'SpeedTestUserApiKey' do Info.plist do IPA.
    with DowndetectorClient(api_key="<SpeedTestUserApiKey>") as dd:
        info = dd.config(latitude=-23.55, longitude=-46.63)   # São Paulo -> Brasil
        print("país:", info["countryInfo"]["name"], "siteID:", dd.site_id)
        comps = dd.companies()
        print(f"{len(comps)} serviços")
        for c in sorted(comps, key=lambda x: -(x.stats_24[-1] if x.stats_24 else 0))[:10]:
            print(f"  {c.name:22} {c.status:8} agora={c.stats_24[-1] if c.stats_24 else '-'}")
