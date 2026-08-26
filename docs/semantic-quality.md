# Búsqueda, equivalencias y calidad semántica

La búsqueda simple (`search_products`) sirve para exploración interactiva cuando basta una
respuesta corta de una tienda. No debe usarse para afirmar «el más barato» o «todos»: Froiz y
Eroski solo tienen verificada una muestra acotada, mientras Mercadona expone páginas y totales
exactos y Gadis los usa cuando su respuesta incluye el total.

Para comparaciones de cobertura se usa `search_products_expanded`. La respuesta explica cada
expansión (`query_expansions`), páginas, total/`has_next`, caché, saturación, errores, descartes y
contrato del proveedor. `category_search=true` añade categorías solo donde existe un contrato
verificado. El TTL público predeterminado es 120 segundos y su clave incluye proveedor,
ubicación, consulta, modo ecológico y límite.

## Qué herramienta usar

| Necesidad | Herramienta |
|---|---|
| Encontrar unos pocos artículos | `search_products` |
| Comparar mínimos o ampliar cobertura | `search_products_expanded` |
| Saber si dos descripciones son compatibles | `explain_product_equivalence` |
| Separar mismo SKU, equivalente y alternativa | `explain_product_relationship` |
| Evaluar una sustitución para un uso y restricciones | `assess_substitution_candidate` |
| Ver ofertas observadas | `search_offers` |
| Cribar si la oferta mejora una alternativa comparable | `filter_worthwhile_offers` |
| Comparar una cesta sin sustitución semántica entre tiendas | `compare_basket` |
| Optimizar una cesta dividida con sustituciones y envío | `optimize_basket_combination` |
| Revisar límites de cada catálogo | `catalogue_contracts` |
| Comparar surtido por zona | `compare_catalogue_regions` |
| Ejecutar regresiones locales | `audit_semantic_corpus` |
| Auditar muestras públicas sin devolver identificadores | `audit_catalogue_quality` |

## Sustituciones y restricciones

La identidad usa tienda+identificador o EAN. La equivalencia compara familia y facetas críticas.
La sustitución es direccional y puede recibir `intent` (`para plancha`, `para guiso`, `para
bocadillo` o `para pizza`). Una intención solo aporta evidencia a esa petición; no altera la
ontología universal del producto.

Las restricciones son siempre explícitas: `allergens`, `diet`, `exclude_terms`, `require_terms`,
`allowed_brands` y `excluded_brands`. Si una alergia o dieta no puede verificarse porque faltan
ingredientes o una declaración del retailer, el candidato se bloquea. Nunca se infieren estas
restricciones del historial de compra.

Cada línea de `optimize_basket_combination` admite además `constraints`, `intent`,
`allow_review_substitutes`, `max_unit_price`, `quantity` y `required`. El resultado mantiene
separados coste real observado y penalización de alternativas dudosas. Los gastos y mínimos se
incluyen cuando el proveedor ofrece una política pública; `delivery_costs_complete=false` impide
presentar como exacto un total con portes desconocidos.

## Ofertas, fidelización y cupones

Una oferta solo modifica el precio cuando hay mecánica, cantidad, vigencia y unidad suficientes.
Los precios de fidelización requieren `include_loyalty=true`. Los cupones personales se muestran
como escenario no accionable y nunca se presuponen canjeables. El cribado de ofertas exige además
que la equivalencia supere el presupuesto de calidad de su familia y que las bases kg/L/unidad
sean compatibles.

## Auditoría reproducible

El corpus versionado está en `data/equivalence_corpus.json`; los alias revisables en
`data/semantic_aliases.json`; y los presupuestos en `data/quality_budgets.json`. Para ejecutar la
matriz local:

```powershell
python .\tools\audit_equivalence.py
```

Para una muestra pública, de solo lectura y sin productos ni identificadores en la salida:

```powershell
python .\tools\audit_equivalence.py --query harina --query sal --postal-code 15001
```

La auditoría informa falsos positivos/negativos del corpus, cobertura de familias, descartes,
incertidumbres, errores y saturación. No abre el carrito ni realiza peticiones de pedido o pago.
