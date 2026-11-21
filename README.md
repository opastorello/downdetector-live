# Downdetector Live

Acesso à **API do Downdetector** usada pelo app **Speedtest by Ookla** (iOS e Android),
descoberta por engenharia reversa (análise do IPA/APK + captura de tráfego TLS).

**Vantagem sobre scraping:** usa a API oficial do app — sem browser, sem Cloudflare Turnstile,
mais rápido e estável. 34 países, ~20 mil serviços, dados em tempo real (status, reports 24h,
baseline, indicadores).

---

## Estrutura

```
downdetector/
├── server/server.py              # API REST (compare entre países, outages globais)
├── exporter/                     # Exporter Prometheus (/metrics)
├── alerts/notifier.py            # Notificador de incidentes (Google Chat + Slack)
├── examples/                     # Cliente Python e coletor de snapshot
├── monitoring/
│   ├── prometheus/               # prometheus.yml + alerts.yml
│   ├── alertmanager/             # alertmanager.yml (Prometheus → Slack/webhook)
│   └── grafana/                  # Datasource + dashboard provisionados
├── docker-compose.yml            # Stack completa (7 serviços)
├── Dockerfile
├── .env.example
├── REFERENCE.md                  # Documentação técnica da API
└── schemas/models.json
```

---

## Quick Start

### 1. Obter a chave de API

A chave é **estática e embutida no app** (mesma para toda instalação). Extraia do IPA ou APK:

**iOS (IPA):**
```bash
unzip -o com.ookla.speedtest*.ipa -d /tmp/st >/dev/null
plutil -extract SpeedTestUserApiKey raw /tmp/st/Payload/*.app/Info.plist
```

**Android (APK):**
```bash
apktool d -f Speedtest*.apk -o /tmp/st-apk >/dev/null
F=$(grep -rl 'consumer-downdetector-api.speedtest.net' /tmp/st-apk/smali*/com/ookla/mobile4/app/ | head -1)
awk '/consumer-downdetector-api\.speedtest\.net/{f=1} f && match($0,/"[A-Za-z0-9]{30}"/){print substr($0,RSTART+1,30); exit}' "$F"
```

### 2. Configurar

```bash
cp .env.example .env
# edite .env com DD_API_KEY e DD_WEBHOOK (Google Chat)
```

### 3. Subir a stack

```bash
docker compose up -d
```

Acesse:
- **Grafana:** http://localhost:3000 (admin/admin)
- **Prometheus:** http://localhost:9090
- **Alertmanager:** http://localhost:9093
- **API REST:** http://localhost:8000

---

## Componentes

### Exporter Prometheus (`:9693/metrics`)

Expõe métricas dos serviços monitorados:

```
downdetector_reports_current{company,slug,country,status}   # reports no último 15min
downdetector_baseline_current{...}                          # nível normal esperado
downdetector_status{...}                                    # 0=ok 1=warning 2=danger
downdetector_ratio_vs_baseline{...}                         # current/baseline
downdetector_up                                             # 1 se coletando
```

### API REST (`:8000`)

| Endpoint | Descrição |
|----------|-----------|
| `GET /api/countries` | Lista dos 34 países |
| `GET /api/companies?iso=BR` | Serviços do país, ao vivo (com cache) |
| `GET /api/compare?q=fivem` | Um serviço comparado entre todos os países |
| `GET /api/global` | Serviços com mais problemas no mundo |
| `GET /metrics` | Métricas Prometheus |

### Notificador (Google Chat + Slack)

Monitora serviços e envia alertas no Google Chat e/ou Slack quando:
- Serviço entra em **outage** (danger) → alerta 🔴
- Serviço **normaliza** → alerta ✅ com duração do incidente

```bash
# Google Chat
DD_API_KEY=xxx DD_WEBHOOK="https://chat.googleapis.com/v1/spaces/..." \
  DD_WATCH=nubank,govbr python3 alerts/notifier.py

# Slack
DD_API_KEY=xxx DD_SLACK="https://hooks.slack.com/services/..." \
  DD_WATCH=nubank,govbr python3 alerts/notifier.py

# Ambos
DD_API_KEY=xxx DD_WEBHOOK="..." DD_SLACK="..." python3 alerts/notifier.py
```

### Alertmanager (Prometheus → Slack/Webhook)

O Alertmanager recebe alertas do Prometheus e envia para Slack, Google Chat ou qualquer webhook:
- Roteamento por severidade (critical → canal separado)
- Agrupamento de alertas por serviço/país
- Notificação de resolução automática

Acesse http://localhost:9093 para ver alertas ativos.

### Grafana Dashboard (provisionado)

Dashboard pronto com:
- KPIs: serviços em outage, instáveis, reports total, coleta ativa
- Gráfico: top 15 serviços por reports
- Pie chart: distribuição de status
- Tabela: todos os serviços com status, reports, baseline, ratio
- Variável de país: filtra por BR, US, GB, etc.

---

## Alertas Prometheus

Regras prontas em `monitoring/prometheus/alerts.yml`:

```yaml
- alert: ServicoEmOutage         # danger por 5min
- alert: ReportsAcimaDoNormal    # ratio > 3 por 10min
- alert: ExporterSemColetar      # downdetector_up == 0
```

---

## Configuração

Variáveis de ambiente (`.env`):

| Var | Default | Descrição |
|-----|---------|-----------|
| `DD_API_KEY` | — | Chave do app (obrigatório) |
| `DD_ISO` | BR | País monitorado |
| `DD_WATCH` | — | Slugs a vigiar (ex: `nubank,govbr`) |
| `DD_TTL` | 120 | Cache por país (segundos) |
| `DD_WEBHOOK` | — | Webhook(s) do Google Chat |
| `DD_SLACK` | — | Webhook(s) do Slack |
| `DD_TRIGGER` | danger | Trigger do alerta: `danger` ou `warning` |
| `DD_RATIO` | 0 | Alerta se reports/baseline >= RATIO |
| `GRAFANA_USER` | admin | Usuário do Grafana |
| `GRAFANA_PASSWORD` | admin | Senha do Grafana |

---

## Rodando sem Docker

```bash
# Exporter
DD_API_KEY=xxx DD_WATCH=nubank,govbr python3 exporter/downdetector_exporter.py

# API
DD_API_KEY=xxx python3 server/server.py

# Notificador
DD_API_KEY=xxx DD_WEBHOOK=https://... python3 alerts/notifier.py
```

---

## Como a API funciona (resumo)

```
1. POST consumer-downdetector-api.speedtest.net/v1/dd/config   (X-Ookla-Api-Key)
   → accessToken (~15min) + countryInfo.siteID
2. OPTIONS st.downdetectorapi.com/v2/sites/{siteID}/companies   (Bearer)
   → {"pages": ["0-<hash>", ...]}   (cursores)
3. GET     .../companies?...&page={cursor}
   → [ {id, name, slug, status, baseline[96], stats_24[96], indicators[], logos{}}, ... ]
```

Detalhes completos em [`REFERENCE.md`](REFERENCE.md).

---

## Aviso

Projeto para fins de **pesquisa e monitoramento**. A API e os dados pertencem à
**Ookla / Downdetector**; respeite os Termos de Serviço deles.

A chave de API fica embutida no app — ela **não** está neste repositório e não deve
ser compartilhada. Se a Ookla rotacionar a chave, extraia a nova do IPA/APK.

## Procedência

Speedtest by Ookla — iOS v7.1.2 (`com.ookla.speedtest`, binário arm64) e Android v7.1.4
(`com.ookla.mobile4`, smali) · módulo `downdetectorcore` (Kotlin Multiplatform / Ktor) ·
endpoints confirmados por captura de tráfego. iOS e Android usam a mesma API (chaves diferentes).
