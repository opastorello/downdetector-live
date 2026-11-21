# Downdetector API — Referência Técnica (confirmada via tráfego real)

> Engenharia reversa do **Speedtest by Ookla** — iOS v7.1.2 (`com.ookla.speedtest`) e
> Android v7.1.4 (`com.ookla.mobile4`). A funcionalidade "Downdetector" é Kotlin Multiplatform
> (módulo `downdetectorcore`), então **iOS e Android compartilham a mesma API** — as seções
> abaixo valem para os dois, com as diferenças de cada plataforma na **seção 9 (Android)**.
> Os endpoints foram **confirmados capturando o tráfego real do app com mitmproxy** (TLS intercept).

Legenda: **[C]** confirmado em captura · **[C-bin]** confirmado por string no binário.

---

## 1. Hosts

| Host | Papel |
|------|-------|
| `consumer-downdetector-api.speedtest.net/v1` | **[C]** Auth/bootstrap (proxy Ookla). Só `/dd/config`. |
| `st.downdetectorapi.com/v2` | **[C]** API de dados (lista, indicadores, heatmap). |
| `data-api.downdetector.com/v1` | **[C]** Séries de reports por empresa. Protegido por Cloudflare. |
| `cdn2.downdetector.com` | **[C]** Logos/assets (`/static/uploads/...`). |

---

## 2. Autenticação — fluxo de 2 passos **[C]**

### Passo 1 — bootstrap
```
POST https://consumer-downdetector-api.speedtest.net/v1/dd/config
     ?latitude={lat}&longitude={lng}
Header: X-Ookla-Api-Key: <SpeedTestUserApiKey do Info.plist>
Body:   {"device":{"id":"<uuid>","guid":"<uuid>","idfv":"<uuid>",
                   "platform":"ios","model":"iPhone15,2","version":"7.1.2"},
         "app":{"version":"7.1.2"}}
```
- A `X-Ookla-Api-Key` é a chave estática **`SpeedTestUserApiKey`** do `Info.plist` (não a `APIKey`).
- É **obrigatório** um identificador de device (`id`/`guid`/`idfv`), senão → `400 missing device identifier`.
- `latitude/longitude` definem o país retornado (geolocalização).

**Resposta:**
```json
{
  "accessToken": "<JWT HS512, exp ~15min>",
  "countryInfo": { "siteID": 18, "countryID": 109, "iso2": "BR", "name": "downdetector.com.br" }
}
```
O `accessToken` serve para **todos os países** (não é por-país). O `siteID` é o que entra no path dos dados.

### Passo 2 — chamadas de dados
```
Header: Authorization: Bearer <accessToken>
        User-Agent: SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0
```

> ⚠️ **Segurança:** a `X-Ookla-Api-Key` e o JWT ficam no IPA. Qualquer um que extraia o app
> consegue falar com a API. Chaves/tokens **não** são reproduzidos neste documento.

---

## 3. Endpoints de dados (host `st.downdetectorapi.com/v2`)

### 3.1 Cursores de paginação (o "getPageIds") **[C]**
```
OPTIONS /v2/sites/{siteID}/companies?order_by=status&order_direction=desc&page_size=50&fields={F}
→ {"pages": ["0-<hash>", "50-<hash>", "100-<hash>", ...]}
```
O **preflight OPTIONS** devolve a lista de cursores. Cada cursor é `{offset}-{snapshotHash}`.
O `snapshotHash` é **por país** e muda quando o dataset é regerado. É assim que o app sabe quantas
páginas existem e qual o cursor inicial.

### 3.2 Lista de serviços (empresas) **[C]**
```
GET /v2/sites/{siteID}/companies
    ?order_by=status            (status | name)
    &order_direction=desc       (asc | desc)
    &page_size=50
    &fields=id,stats_24,name,status,baseline,indicators,slug,description,logos
    &page={cursor}              (ex.: 0-<hash>, obtido do OPTIONS)
```
Resposta: **array** de objetos (ver §4). Headers úteis:
`x-page-total` (total de serviços), `x-page-count`, `x-page-next` (próximo cursor).
Sem `page` válido → `400/404 "Please provide a valid page parameter"`.

Exemplo real: BR (`siteID=18`) → **959 serviços** · US (`siteID=3`) → ~3000 · UK (`siteID=6`) → ~1650.

### 3.3 Indicadores de um serviço **[C]**
```
GET /v2/companies/{companyId}/indicators
```
Distribuição de tipos de problema (para o gráfico de pizza).

### 3.4 Heatmap de um serviço **[C]**
```
GET /v2/slugs/{slug}/heatmap/current
```
Clusters geográficos de reports.

## 4. Séries de reports (host `data-api.downdetector.com/v1`) **[C]**
```
GET /v1/companies/{companyId}/reports
```
Histórico de reports usado nos gráficos de detalhe.

---

## 5. Estrutura do serviço (SiteDTO real) **[C]**

```jsonc
{
  "_sort": "1.00000530000446",   // chave de ordenação interna
  "id": 53941,
  "name": "GOV.BR",
  "slug": "govbr",
  "status": "warning",           // ok | warning | danger
  "baseline": [4,4,4,3, ...],    // 96 pontos (15min x 24h)
  "stats_24": [3,1,1,1,2, ...],  // 96 pontos de reports (Kotlin: chartStats)
  "indicators": ["Website","Login","Pagamentos","Aplicativo Celular","Atualizações","Forms & Applications"],
  "description": "GOV.BR é a plataforma digital oficial do governo brasileiro...",
  "logos": {
    "original": "https://cdn2.downdetector.com/static/uploads/logo/govbr_tile.webp",
    "large":    "https://cdn2.downdetector.com/static/uploads/...",
    ...
  }
}
```

Observações:
- `stats_24` e `baseline` são **arrays de 96 inteiros** (um ponto a cada 15 min nas últimas 24h).
- `indicators` na lista são **nomes** (strings). A distribuição com percentuais vem de `/companies/{id}/indicators`.
- `status`: valores reais `ok` / `warning` / `danger`. O binário mapeia para `success/warning/danger`.

---

## 6. Países (siteID) **[C]**

33 países confirmados. O `siteID` vem do `/dd/config` (por geolocalização) e indexa o path dos dados.
Exemplos: BR=18, US=3, GB=6, DE=2, FR=7, IT=10, ES=12, PT=38, MX=13, AR=45, CA=15, AU=16,
IN=11, JP=20, NL=1, RU=8, PL=34, SE=31, KR=91, ID=61, TH=68, PH=63, MY=60, ZA=30, TR=33,
AT=21, CH=39, IE=40, NZ=37, DK=35, FI=46, NO=50, CL=49, PE=59, BE=5. (lista completa gerada em `data/index.json`)

---

## 7. Modelos de config (binário) **[C-bin]**

```jsonc
UserIdentifiersDevice { id, platform, model, version, idfa, idfaEnabled, idfv,
  guid, androidId, fingerprint, brand, product, hardware, buildId, manufacturer,
  isRooted, radio, telephonyDeviceId, telephonyImei, telephonyMeid, telephonyPhoneType }
// no iOS só id/guid/idfv/platform/model/version são relevantes.
```

---

## 8. Como reproduzir (resumo)

```bash
# 1) token + siteID
POST /v1/dd/config?latitude=..&longitude=..  (X-Ookla-Api-Key, body com device.id)
# 2) cursores do país
OPTIONS /v2/sites/{siteID}/companies?...     (Bearer)   → {"pages":[...]}
# 3) cada página
GET /v2/sites/{siteID}/companies?...&page={cursor}  (Bearer) → [ ...serviços... ]
```
Cliente de referência: [`examples/api_client.py`](examples/api_client.py).
Coletor completo (todos os países): [`examples/collect.py`](examples/collect.py).

---

## 9. Android (Speedtest APK)

O módulo `downdetectorcore` é Kotlin Multiplatform, então **o Android usa exatamente os mesmos
hosts, endpoints, fluxo e DTOs**. As diferenças são:

| Item | iOS | Android |
|------|-----|---------|
| Chave de API (`X-Ookla-Api-Key`) | `SpeedTestUserApiKey` no `Info.plist` | string de 30 chars **hardcoded no dex**, em `com.ookla.mobile4.app` logo após o baseURL |
| `pid` (platform id) no JWT | `2` | `1` |
| Device identifiers enviados | `idfa`, `idfaEnabled`, `idfv` | `androidId`, `fingerprint`, `brand`, `product`, `hardware`, `buildId`, `manufacturer`, `androidApi`, `isRooted`, `radio` |
| `device.platform` | `ios` | `android` |

A chave do Android é **diferente** da do iOS, mas o resto é igual — o mesmo `accessToken`
obtido via `/dd/config` dá acesso a `/v2/sites/{siteID}/companies` em qualquer plataforma.

### Extrair a chave do APK

```bash
apktool d -f Speedtest*.apk -o /tmp/st-apk >/dev/null
# a key é a 1ª const-string de 30 chars logo após o baseURL do downdetector
F=$(grep -rl 'consumer-downdetector-api.speedtest.net' /tmp/st-apk/smali*/com/ookla/mobile4/app/ | head -1)
awk '/consumer-downdetector-api\.speedtest\.net/{f=1} f && match($0,/"[A-Za-z0-9]{30}"/){print substr($0,RSTART+1,30); exit}' "$F"
# use o valor impresso como DD_API_KEY
```

Request de config no Android (os campos de device mudam):
```jsonc
POST /v1/dd/config?latitude=..&longitude=..
Header: X-Ookla-Api-Key: <chave do APK>
Body: {"device":{"id":"<uuid>","guid":"<uuid>","androidId":"<id>","platform":"android",
                 "model":"Pixel 7","version":"7.1.4"}, "app":{"version":"7.1.4"}}
```

> O servidor e o exporter deste repo funcionam com **qualquer uma das chaves** (iOS ou Android)
> em `DD_API_KEY` — o endpoint de dados é o mesmo.

---

## 10. Procedência da análise

- IPA: Speedtest iOS v7.1.2 · binário arm64 · módulo `downdetectorcore` (Kotlin/Native)
- APK: Speedtest Android v7.1.4 · `com.ookla.mobile4` · smali via apktool
- Endpoints/headers/estruturas: capturados com **mitmproxy 12** interceptando o app real
- Chaves de auth: `SpeedTestUserApiKey` (iOS, `Info.plist`) e a chave hardcoded no dex (Android) — nenhuma reproduzida aqui
