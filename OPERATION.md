# LinkedIn Gateway — Operação

O gateway roda via Docker Compose em `/root/linkedin-gateway/deployment`.

## Runtime canônico

```bash
cd /root/linkedin-gateway/deployment
docker compose ps
```

Estado esperado:

- `linkedin-gateway-core-api` / service `backend`: `Up ... (healthy)`
- `linkedin-gateway-core-db` / service `postgres`: `Up ... (healthy)`
- Porta publicada: `0.0.0.0:7778->7778/tcp`

## Healthcheck

```bash
curl -sS http://127.0.0.1:7778/health
```

Resultado esperado:

```json
{"status":"ok"}
```

## Reiniciar o gateway

```bash
cd /root/linkedin-gateway/deployment
docker compose up -d backend
docker compose logs --tail=100 backend
```

## Importante: não usar systemd host em paralelo

Em 2026-07-07 foi identificado que o Compose já publica a porta 7778 via
`docker-proxy`. Um unit host como `/etc/systemd/system/linkedin-gateway.service`
tenta subir outro `uvicorn` na mesma porta e entra em restart loop com:

```text
[Errno 98] error while attempting to bind on address ('0.0.0.0', 7778): address already in use
```

Por isso o unit host foi desabilitado/removido. Se for necessário migrar para
systemd no futuro, primeiro pare/remova o serviço Docker Compose ou altere a
porta; nunca rode os dois publicando 7778 ao mesmo tempo.

## Env / secrets

O Compose lê `/root/linkedin-gateway/deployment/.env`. Manter esse arquivo com
permissão restrita (`0600`) e nunca commitar secrets.
