# Cashbacks

Reconciliación de compras y cashback para el core bancario, disparada de forma
asíncrona (SQS) después de que la compra ya fue autorizada. Ver
[`CASHBACKS_DESIGN.md`](./CASHBACKS_DESIGN.md) para el diseño completo (CAP,
modelo de datos, contrato del evento, diagramas).

## Estructura del proyecto

```
src/
  lambda_function.py    # handler SQS (entry point)
  purchase_service.py    # orquestación: TransactWriteItems, fallback de campaña, idempotencia
  campaign_service.py    # resolución y ranking de campañas aplicables
  validation.py           # parseo/validación del evento de compra
  models.py               # tipos de datos (CampaignRule, PurchaseRequest, PurchaseResult)
  errors.py                # excepciones de dominio
  logging_utils.py        # logging estructurado (JSON) para CloudWatch Logs
scripts/
  seed_campaigns.py       # migra la regla legacy (>100 -> 5%) a la campaña GLOBAL
tests/                     # unit puros, integración con moto, concurrencia con threads
```

## Setup

Requiere Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

`requirements-dev.txt` incluye `requirements.txt` (boto3) más `pytest`,
`moto[dynamodb]`, `ruff`, `mypy` y `boto3-stubs[dynamodb]` (stubs de tipos para
las llamadas a DynamoDB), necesarios solo para desarrollo/tests.

## Correr los tests

```bash
source .venv/bin/activate
pytest
```

Ningún test toca AWS real: los de integración (`test_purchase_service.py`,
`test_lambda_handler.py`, `test_concurrency.py`) usan `moto` para mockear
DynamoDB en memoria. `conftest.py` en la raíz agrega `src/` al `PYTHONPATH`
automáticamente, así que no hace falta instalar el paquete.

Para correr solo una capa:

```bash
pytest tests/test_models.py tests/test_validation.py tests/test_campaign_service.py  # unit puros
pytest tests/test_purchase_service.py tests/test_lambda_handler.py                    # integración (moto)
pytest tests/test_concurrency.py                                                       # concurrencia (threads reales)
```

## Lint y type-check

```bash
source .venv/bin/activate
ruff check .     # lint (incluye orden de imports y reglas de simplificación)
mypy              # type-check de src/ (config en pyproject.toml)
```

## Logging estructurado

`lambda_function.py` llama a `logging_utils.configure_logging()` al importarse:
reemplaza el handler de texto plano que el runtime de Lambda deja instalado por
uno que emite cada línea como un objeto JSON (nivel, logger, mensaje, y
cualquier campo pasado vía `extra={...}`), para poder filtrar/consultar en
CloudWatch Logs Insights por `idempotency_key`, `status`, `applied_campaign_id`,
etc.

## Variables de entorno (Lambda desplegada)

`lambda_function.py` espera estas tres al ejecutarse en AWS:

- `USERS_TABLE`
- `CAMPAIGNS_TABLE`
- `TRANSACTIONS_TABLE`

## Seed de la campaña base

Antes de desplegar, insertar la campaña `GLOBAL` que reemplaza la regla
hardcodeada original (`purchase_amount > 100 -> 5%`):

```bash
python scripts/seed_campaigns.py --table campaigns
```
