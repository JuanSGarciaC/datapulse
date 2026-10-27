# Fuentes de datos

DataPulse ingiere tres fuentes de un e-commerce. Todas son **sintéticas o públicas**:
no hay datos personales reales en el repositorio.

| Fuente | Formato | Origen | Particularidades |
|--------|---------|--------|------------------|
| Clientes | CSV diario | `data/samples/customers/` (generador) | Exportación completa el día 1 y deltas después; cambio de esquema el día 3 |
| Pedidos | JSON por lotes (anidado) | `data/samples/orders/` (generador) | Reenvíos, actualizaciones tardías, líneas anidadas |
| Productos | API REST paginada | DummyJSON real **o** `mock-source` | Paginación `limit`/`skip`, marca de agua en `meta.updatedAt` |

## API de productos: DummyJSON

**Por qué DummyJSON:** es gratuita, no requiere registro, tiene un catálogo de e-commerce
(productos, usuarios, carritos) y ofrece paginación real y simulación de latencia y estados
HTTP. Encaja con el dominio sin inventar un contrato.

### Comportamiento verificado en vivo (2026-10-27)

Comprobado con peticiones reales, no con la documentación (que no cubre varios de estos puntos):

| Petición | Resultado observado |
|----------|---------------------|
| `GET /products` | `{products, total, skip, limit}`; 30 elementos por defecto; `total` = 194 |
| `GET /products?limit=10&skip=190` | 4 elementos y `limit: 4` (el `limit` de la respuesta es lo enviado) |
| `GET /products?limit=10&skip=500` | 200 con `products: []`, `limit: 0` (no es error) |
| `GET /products?limit=0` | Todos los elementos |
| `limit=-1` o `limit=abc` | 400 `{"message": "Invalid 'limit' - should be a positive number"}` |
| `skip=-1` | 400 `{"message": "Invalid 'skip' - should be a positive number"}` |
| `select=title,price` | Solo esos campos **más `id`** |
| `sortBy=id&order=desc` | Orden descendente |
| `delay=1500` | ~1.6 s de respuesta |
| `GET /products/987654` | 404 `{"message": "Product with id '987654' not found"}` |
| `GET /http/429`, `/http/503/msg` | Estado pedido, `application/problem+json` |
| Cabeceras | `x-ratelimit-limit: 100`, `x-ratelimit-remaining` |
| `meta.updatedAt` | ISO-8601 con zona → sirve como marca de agua incremental |

### Lo que DummyJSON **no** permite (y por eso existe `mock-source`)

1. **Fallos intermitentes a mitad de una paginación.** `/http/503` siempre falla; no hay forma
   de que la página 3 falle dos veces y luego funcione. Sin eso no se pueden probar
   reintentos ni recuperación.
2. **`Retry-After` en el 429.** El 429 simulado no lo incluye; un cliente bien hecho debe respetarlo.
3. **Datos que cambian entre ejecuciones de forma controlada.** El catálogo real no cambia
   cuando lo necesitamos, así que no permite probar la carga incremental de forma determinista.
4. **Disponibilidad garantizada.** Las pruebas de CI no deben depender de un tercero.

## `mock-source`: réplica del contrato con fallos inyectables

Implementa el comportamiento de la tabla anterior **y** estas extensiones, bajo `/_mock`
(desactivadas si no hay `DATAPULSE_MOCK_ADMIN_TOKEN`; comparación de token en tiempo constante):

| Endpoint | Efecto |
|----------|--------|
| `PUT /_mock/faults` | `fail_rate` (con semilla), `fail_status`, `latency_ms`, `rate_limit` + `rate_window_s` (429 con `Retry-After`), `fail_offsets` (`{"30": 2}`: el `skip=30` falla 2 veces y luego funciona) |
| `PUT /_mock/clock` | `as_of`: la API muestra el catálogo tal como estaba en esa fecha |
| `GET /_mock/state` | Configuración y contadores (`requests_seen`, `faults_injected`) |

Los fallos también se configuran al arrancar con `DATAPULSE_MOCK_FAULTS` (JSON).

### Cómo se garantiza que la simulación es fiel

`tests/contract/test_products_contract.py` es **una sola suite** parametrizada con dos
implementaciones: la simulada (siempre) y DummyJSON real (marcador `live`). En CI la variante
real corre en cada push y cada semana; si DummyJSON cambia su contrato, el job falla.

## Datos sintéticos

`python -m datapulse.synthetic` genera `data/samples/` de forma **determinista** (semilla 42):
mismos bytes en cada ejecución y sistema operativo. `--check` verifica que los archivos
versionados coinciden con el generador (también lo comprueba una prueba unitaria).

### Defectos inyectados (respuesta conocida)

Cada defecto queda registrado en `data/samples/manifest.json`, con archivo, clave y campo.
La fase 3 debe detectar **exactamente** estos conteos:

| Fuente | Defecto | Cantidad | Qué debe hacer el pipeline |
|--------|---------|----------|----------------------------|
| clientes | email vacío | 3 | Cuarentena (campo obligatorio) |
| clientes | email sin `@` | 2 | Cuarentena (formato) |
| clientes | `signup_date` = `31/02/2026` | 2 | Cuarentena (tipo/fecha imposible) |
| clientes | fila repetida en el mismo archivo | 3 | Deduplicar dentro del lote |
| clientes | archivo reenviado con otro nombre | 1 | Omitir por hash de contenido |
| clientes | columna nueva `loyalty_tier` (día 3) | 1 | Aceptar, guardar en `_extra`, avisar |
| pedidos | pedido idéntico en otro lote | 5 | Ignorar por hash de registro |
| pedidos | nueva versión del pedido (estado y `updated_at` posteriores) | 5 | Gana la última versión en staging |
| pedidos | `quantity = "two"` | 2 | Cuarentena (tipo) |
| pedidos | `quantity` 0 o negativa | 2 | Cuarentena (rango) |
| pedidos | `customer_id` nulo | 2 | Cuarentena (obligatorio) |
| pedidos | pedido sin líneas | 1 | Cuarentena (colección vacía) |
| pedidos | producto o cliente inexistente | 4 | Aviso en prueba de relaciones (dbt) |
| productos | `price` nulo o `title` vacío | 3 | Cuarentena |
| productos | precio negativo | 1 | Cuarentena (rango) |

Valores esperados: 230 clientes distintos, 40 cambios de atributos, 450 pedidos distintos
(460 registros), 200 productos distintos (230 versiones).

**Por qué sin Faker:** su salida cambia entre versiones y rompería la reproducibilidad byte a
byte. Los correos usan `example.com`, dominio reservado por RFC 2606 que nunca es real.
