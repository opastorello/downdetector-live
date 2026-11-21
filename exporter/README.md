# Downdetector Prometheus Exporter

Serviço que expõe métricas do Downdetector em `/metrics` para o Prometheus fazer scrape.
Sem dependências (Python 3 stdlib). Dados reais da API do app Speedtest.

## Rodar

```bash
export DD_API_KEY="<SpeedTestUserApiKey do Info.plist>"
export DD_WATCH="nubank,govbr,claro,vivo,tim"     # slugs a monitorar (vazio = todos do país)
python3 downdetector_exporter.py
# -> http://localhost:9693/metrics
```

### Variáveis de ambiente

| Var | Default | Descrição |
|-----|---------|-----------|
| `DD_API_KEY` | — (obrigatório) | `SpeedTestUserApiKey` do `Info.plist` do IPA |
| `DD_WATCH` | vazio = todos | slugs separados por vírgula (`nubank,govbr`) |
| `DD_LAT` / `DD_LON` | `-23.55` / `-46.63` | geolocalização → define o país |
| `DD_SITE_ID` | — | força um país por siteID (BR=18, US=3, GB=6…) |
| `DD_TTL` | `120` | cache da coleta em segundos (não bate na API a cada scrape) |
| `DD_PORT` | `9693` | porta do `/metrics` |

> **Dica:** sempre use `DD_WATCH` com os serviços que importam. Sem watchlist em país
> grande (US tem ~3000 serviços) cada coleta baixa o país inteiro.

## Métricas expostas

```
downdetector_reports_current{company,slug,country,status}   # reports no último 15min
downdetector_baseline_current{company,slug,country,status}  # nível normal esperado
downdetector_reports_24h_total{...}                         # soma 24h
downdetector_status{...}                                    # 0=ok 1=warning 2=danger
downdetector_ratio_vs_baseline{...}                        # current/baseline (bom p/ alerta)
downdetector_up                                            # 1 se a última coleta funcionou
downdetector_services_scraped
downdetector_scrape_duration_seconds
```

## Prometheus — `prometheus.yml`

```yaml
scrape_configs:
  - job_name: downdetector
    scrape_interval: 60s
    scrape_timeout: 45s
    static_configs:
      - targets: ["localhost:9693"]
```

> Mantenha `scrape_interval` >= `DD_TTL` ou perto disso. O exporter cacheia a coleta por
> `DD_TTL`, então scrapes mais frequentes não geram chamadas extras à API.

## Alerta de outage (Prometheus rules)

```yaml
groups:
  - name: downdetector
    rules:
      - alert: ServicoComOutage
        expr: downdetector_status >= 2
        for: 5m
        labels: {severity: critical}
        annotations:
          summary: "Outage em {{ $labels.company }} ({{ $labels.country }})"
          description: "{{ $value }} · {{ $labels.slug }} em queda há 5min."

      - alert: ReportsAcimaDoNormal
        expr: downdetector_ratio_vs_baseline > 3 and downdetector_reports_current > 20
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.company }} com reports {{ $value }}x acima do baseline"

      - alert: ExporterDown
        expr: downdetector_up == 0
        for: 5m
        labels: {severity: warning}
        annotations: {summary: "Exporter do Downdetector sem coletar"}
```

## Painéis Grafana (queries PromQL)

```promql
# serviços em outage agora
count(downdetector_status >= 2)

# top 10 por reports atuais
topk(10, downdetector_reports_current)

# reports vs baseline de um serviço
downdetector_reports_current{slug="nubank"}
downdetector_baseline_current{slug="nubank"}

# tabela de status (0/1/2) por serviço
downdetector_status
```

## systemd (opcional)

```ini
# /etc/systemd/system/downdetector-exporter.service
[Unit]
Description=Downdetector Prometheus Exporter
After=network-online.target

[Service]
Environment=DD_API_KEY=xxxxx
Environment=DD_WATCH=nubank,govbr,claro
ExecStart=/usr/bin/python3 /opt/downdetector/downdetector_exporter.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now downdetector-exporter
```

## Docker (opcional)

```dockerfile
FROM python:3.12-slim
COPY downdetector_exporter.py /app/
EXPOSE 9693
CMD ["python3","/app/downdetector_exporter.py"]
```
```bash
docker build -t dd-exporter .
docker run -d -p 9693:9693 -e DD_API_KEY=xxxxx -e DD_WATCH=nubank,govbr dd-exporter
```

## Avisos

- Depende da `SpeedTestUserApiKey` embutida no IPA. Se a Ookla rotacionar a chave, extraia a nova do app.
- A API tem rate limit (`DdApiRateLimitedException`). Use `DD_WATCH` e `DD_TTL >= 60` para ser gentil.
- Uso para monitoramento/pesquisa. A API pertence à Ookla/Downdetector; respeite os termos.
