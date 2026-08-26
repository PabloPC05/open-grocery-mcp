# Hoja de ruta de equivalencias

Este documento es la memoria de trabajo para ampliar y verificar la búsqueda y
comparación semántica de Open Grocery MCP. Una casilla solo se marca como
completada cuando existe implementación, regresiones automatizadas y una
validación proporcionada contra los catálogos reales que correspondan.

Estados:

- `[ ]`: pendiente.
- `[~]`: parcialmente implementado; el criterio de aceptación indica qué falta.
- `[x]`: implementado y verificado.

## Principios que deben mantenerse

- Un conflicto semántico explícito siempre prevalece sobre la similitud léxica.
- Una característica crítica ausente produce incertidumbre, no una equivalencia inventada.
- «Compatible» no significa «idéntico» ni autoriza por sí solo una sustitución en un pedido.
- Toda afirmación de mínimo o de cobertura debe incluir límites, errores y saturación observada.
- Las búsquedas y auditorías de catálogo son de solo lectura. Nunca requieren escribir un carrito.
- Las reglas deben ser explicables y compartidas por búsqueda, cesta y ofertas.

## P0 — Exactitud de producto

- [x] **P0.1 — Carnes frescas y procesadas.** Reconocer especie, corte, preparación,
  formato y mezcla. Debe separar, como mínimo, cerdo/ternera/pollo/pavo/cordero/conejo;
  lomo/solomillo/costilla/chuleta/pechuga/muslo/alitas; fresco/adobado/marinado/empanado;
  pieza/filete/tiras/dados/picado y carne picada de una o varias especies. Validar con
  Mercadona, Froiz, Gadis y Eroski.
- [x] **P0.2 — Pescados y mariscos restantes.** Añadir merluza, bacalao, sardina,
  dorada, lubina, bonito fresco, cefalópodos y mariscos; distinguir especie, corte,
  fresco/congelado/salado/ahumado/conserva y producto preparado.
- [x] **P0.3 — Frutas, verduras y hortalizas.** Distinguir especie/variedad, origen
  cuando sea relevante, fresco/congelado/conserva, entero/cortado y calibre o unidad
  de venta sin confundir sabores o ingredientes secundarios.
- [x] **P0.4 — Bebidas.** Separar agua, refrescos, zumos, bebidas vegetales, cerveza y
  destilados; modelar sabor, con/sin gas, azúcar, alcohol, concentración y formato.
  Los productos regulados seguirán excluidos de automatizaciones de compra.
- [x] **P0.5 — Congelados y platos preparados.** Crear subfamilias útiles en lugar de
  agrupar todo como `prepared_meal`: pizzas, croquetas, empanadas, lasañas, ensaladas,
  sopas, cremas y platos de arroz/pasta, con ingrediente principal y preparación.
- [x] **P0.6 — Despensa restante.** Modelar harina, azúcar/edulcorantes, sal, salsas,
  conservas, cereales, galletas, snacks, caldos, especias y repostería.
- [x] **P0.7 — Limpieza del hogar.** Ampliar detergentes con uso, tejido, concentración
  y dosis; añadir suavizantes, limpiadores, lejía, lavavajillas manual, bolsas y papel.
- [x] **P0.8 — Higiene y cosmética.** Separar champú, gel, jabón, desodorante, higiene
  dental, cuidado infantil y formatos/variantes que no sean intercambiables.
- [x] **P0.9 — Productos infantiles y mascotas.** Sustituir las familias de exclusión
  genéricas por subfamilias seguras, manteniéndolas separadas de alimentos humanos.
- [x] **P0.10 — Completar familias ya reconocidas.** Ampliar variedades, sabores y
  formatos de pan, yogur, queso, pasta, arroz, chocolate, café, aceite, huevos, tofu,
  jamón, atún y salmón a partir de falsos positivos y falsos negativos reales.

## P1 — Cobertura de búsqueda

- [x] **P1.1 — Paginación y totales por proveedor.** Investigar y utilizar cursores,
  páginas o endpoints de categoría cuando existan; documentar límites duros cuando no.
- [x] **P1.2 — Detección de saturación precisa.** Diferenciar «se alcanzó el límite» de
  «hay más resultados» usando totales o `has_next` del retailer cuando estén disponibles.
- [x] **P1.3 — Expansión morfológica.** Singular/plural, género, tildes, gallego/castellano,
  abreviaturas, errores ortográficos frecuentes y nombres de corte equivalentes.
- [x] **P1.4 — Alias aprendidos y revisables.** Mantener alias por concepto en datos
  versionados, con procedencia y regresiones, en lugar de crecer solo mediante código.
- [x] **P1.5 — Búsqueda por categorías.** Combinar texto y navegación por categoría para
  recuperar productos que el buscador del supermercado no indexa correctamente.
- [x] **P1.6 — Cobertura geográfica.** Probar almacenes o tiendas representativas sin
  vincular la ontología a una ciudad; hacer visibles las diferencias de surtido.

## P1 — Calidad de la equivalencia

- [x] **P1.7 — Metadatos estructurados.** Incorporar ingredientes, denominación legal,
  taxonomía, atributos, nutrición y preparación cuando el proveedor los exponga.
- [x] **P1.8 — Separar identidad, equivalencia y sustitución.** Mantener puntuaciones y
  criterios distintos para mismo SKU, sustituto directo y alternativa culinaria.
- [x] **P1.9 — Equivalencias asimétricas.** Modelar casos en que A puede sustituir a B
  bajo una intención concreta, pero no necesariamente al revés.
- [x] **P1.10 — Intención de uso.** Permitir consultas como «para plancha», «para guiso»,
  «para bocadillo» o «para pizza» sin convertirlas en reglas universales del producto.
- [x] **P1.11 — Marcas y marca blanca.** Normalizar fabricante/marca, submarcas y marcas
  propias sin considerar que compartir marca implique equivalencia.
- [x] **P1.12 — Envases y peso variable.** Mejorar multipacks, peso escurrido, unidades,
  rollos/dosis, venta al peso, rangos estimados y comparación entre bases compatibles.

## P1 — Ofertas y cestas

- [x] **P1.13 — Ofertas con mayor evidencia.** Exigir evidencia suficiente de equivalencia,
  unidad y mecánica; explicar por separado por qué se acepta el producto y el descuento.
- [x] **P1.14 — Fidelización y cupones.** Representar precios de tarjeta y cupones como
  escenarios separados, sin asumir que el usuario puede aplicarlos.
- [x] **P1.15 — Optimización de cesta semántica.** Permitir sustituciones controladas por
  línea, penalización por diferencia y coste de envío/mínimo, conservando la opción exacta.
- [x] **P1.16 — Restricciones del usuario.** Alergias, dieta y preferencias deben actuar
  como filtros duros explícitos; nunca inferirse de compras anteriores.

## P2 — Verificación y mantenimiento

- [x] **P2.1 — Corpus anonimizado de catálogo.** Guardar ejemplos mínimos y sanitizados
  de las cuatro cadenas con etiqueta esperada, procedencia y fecha de observación.
- [x] **P2.2 — Matriz de regresión.** Añadir pares positivos, negativos y ambiguos por
  familia; medir falsos positivos, falsos negativos y cobertura reconocida.
- [x] **P2.3 — Auditoría reproducible.** Crear una herramienta de solo lectura que muestree
  consultas, perfiles, rechazos, incertidumbres y saturación sin imprimir datos privados.
- [x] **P2.4 — Presupuesto de calidad.** Definir umbrales mínimos por familia antes de
  habilitarla para ofertas o sustitución automática.
- [x] **P2.5 — Observabilidad MCP.** Exponer versión de ontología, reglas aplicadas y causa
  de cada conflicto, incertidumbre, expansión y descarte.
- [x] **P2.6 — Rendimiento y caché.** Cachear perfiles y resultados públicos con claves por
  tienda/almacén, TTL explícito y límites de concurrencia respetuosos.
- [x] **P2.7 — Contratos de proveedores.** Añadir pruebas para cambios de esquema, límites,
  errores, cierres de sesión y degradación segura de cada supermercado.
- [x] **P2.8 — Documentación de uso.** Documentar cuándo usar búsqueda simple, expandida,
  explicación de equivalencia, ofertas y comparación de cesta.

## Criterio global de finalización

La equivalencia general se considerará estable cuando todas las familias P0 estén
implementadas, las búsquedas indiquen sus límites reales, exista un corpus de los
cuatro supermercados y ninguna familia pueda intervenir en ofertas o sustituciones
sin superar su presupuesto de calidad. Esto no implica que el catálogo sea estático:
los casos desconocidos deben seguir siendo visibles y alimentar nuevas regresiones.

## Registro de tareas completadas

### 2026-08-25 — P1 y P2 completos

- Mercadona pagina mediante Algolia con total y `has_next` exactos; una prueba real de
  `queso` recuperó 150 posiciones en dos páginas estables y conservó visible que quedaban
  37. Gadis soporta página y total si aparece el campo, pero la respuesta real actual no lo
  incluye: degrada a `bounded_unknown` y marca saturación. Froiz y Eroski declaran sus límites
  duros de primera muestra sin inventar totales.
- La búsqueda expandida añade variantes de tilde, número y género, alias versionados y
  categorías verificadas; informa procedencia de cada expansión, descartes, páginas, caché,
  errores y saturación. La caché usa TTL y ubicación explícitos y se desactiva en el catálogo
  Froiz autenticado, cuya tienda puede cambiar fuera de los parámetros de consulta.
- La comparación semántica incorpora metadatos estructurados, identidad por tienda/ID o EAN,
  sustitución direccional, intención de uso, marcas propias y fabricante/submarca, multipacks,
  peso escurrido, rangos de peso variable y bases kg/L/unidad compatibles.
- Las ofertas pasan un presupuesto de calidad por familia. Fidelización es opt-in y los cupones
  personales se muestran como escenario no accionable. La optimización de cesta contempla
  sustituciones por línea, penalización de revisión, límites de precio, portes, mínimos y costes
  de entrega desconocidos sin inventarlos.
- Alergias, dieta, términos y marcas permitidas/excluidas son restricciones explícitas y duras.
  Si falta evidencia de ingredientes o dieta, el candidato se rechaza; nunca se infieren
  preferencias del historial del usuario.
- El corpus versionado contiene 20 casos sanitizados con las cuatro cadenas. La auditoría dio
  20/20, cobertura de familias 100 %, 0 falsos positivos y 0 falsos negativos. La auditoría real
  agregada de `harina` y `queso arzua` observó 420 resultados, aceptó 97, rechazó 323, no tuvo
  errores y mantuvo visibles 18 consultas saturadas sin devolver productos ni identificadores.
- La comparación geográfica de muestras 15001/28001 mostró el mismo conjunto acotado de harina
  en Mercadona y diferencias de surtido/servicio en Gadis; esas diferencias no alteran la
  ontología global.
- Nuevas herramientas MCP: contratos y regiones de catálogo, estado de ontología, auditoría de
  corpus y catálogo, relación de producto, sustitución dirigida y optimización de cesta.
- Verificación final: 528 pruebas, `compileall` y Ruff correctos. El wheel incluye corpus, alias y
  presupuestos. Todas las comprobaciones fueron búsquedas/lecturas de catálogo; no se escribió
  ningún carrito ni se alcanzó una ruta de checkout, pedido o pago.

### 2026-08-25 — P0.1 Carnes frescas y procesadas

- Implementadas especie, raza, alimentación/calidad, corte, preparación, formato,
  conservación y presencia de hueso.
- Cubiertos cerdo, vacuno, pollo, pavo, cordero y conejo, incluida carne picada mixta.
- Separados fresco, congelado, cocido, curado, adobado/marinado, empanado, salado y
  relleno; pieza, filetes, lonchas, tiras, dados, picada, hamburguesa y otros formatos.
- Añadidas exclusiones para pescado, platos preparados, alternativas vegetales,
  comida de mascotas, condimentos, snacks, grasa de cocina y productos de limpieza.
- Validadas consultas de lomo de cerdo, pechuga de pollo, carne picada, cordero y
  conejo contra Mercadona, Froiz, Gadis y Eroski. Todos los resultados aceptados
  pertenecieron a la familia de carne y respetaron la especie solicitada cuando la
  consulta la especificaba.
- Validadas ofertas reales sin comparar fresco con adobado/curado/relleno ni mezclar
  especie, corte, formato, raza o bellota/cebo.
- Suite completa: 470 pruebas; compilación y Ruff correctos.

### 2026-08-25 — P0.2 Pescados y mariscos restantes

- Implementadas familias separadas para pescado y marisco, conservando atún y
  salmón como perfiles especializados.
- Cubiertas merluza, bacalao, sardina, dorada, lubina, bonito y otras especies
  observadas; cefalópodos, crustáceos y moluscos incluyen forma y calibre cuando
  el nombre aporta esa evidencia.
- Modelados cortes como pieza, filete, lomo, porción, medallón, rodaja, tajada,
  palitos, trozos/menú, cola, migas y ventresca; además de piel, espinas y origen
  salvaje o de acuicultura.
- Separadas preparación y conservación: fresco, congelado, descongelado, salado,
  punto de sal, desalado, ahumado, cocido, empanado y conserva, incluido su medio.
- Añadidas exclusiones para platos preparados, galletas, comida de mascotas,
  salsas y especies acuáticas cercanas mencionadas por el buscador.
- Validadas en modo de solo lectura las consultas `merluza`, `bacalao`, `sardina`,
  `dorada`, `lubina`, `bonito`, `pulpo`, `calamar`, `gamba` y `mejillón` contra
  Mercadona, Froiz, Gadis y Eroski con el código postal representativo 15001.
- La repetición de ofertas de bacalao de Eroski examinó 20 productos y 4 ofertas:
  comparó lomo desalado solo con otro lomo desalado y dejó filete, palitos y
  tajada sin verificar al no existir una alternativa suficientemente equivalente.
- Suite completa: 476 pruebas; compilación y Ruff correctos. No se escribió ningún
  carrito ni se alcanzó una ruta de checkout, pedido o pago.

### 2026-08-25 — P0.3 Frutas, verduras y hortalizas

- Implementadas familias de fruta y hortaliza, conservando el plátano como perfil
  especializado, con especies comunes de fruta, hoja, raíz, bulbo, fruto y seta.
- Modeladas variedades observadas de manzana, pera, cítricos, melón, tomate,
  patata, cebolla, lechuga, pimiento, calabacín y calabaza, incluidas mezclas.
- Separados fresco, congelado, refrigerado, deshidratado y conserva; entero,
  pelado, cortado, rallado, triturado, dados, tiras, gajos, floretes y procesado.
- Añadidos uso de mesa/zumo/freír/cocer, producción ecológica, origen explícito,
  calibre físico y venta por peso, unidad o envase. El calibre prioriza medidas
  como `70/85 mm` frente a códigos comerciales como `5/6`.
- Excluidos por el sustantivo principal zumos, refrescos, yogures, mermeladas,
  postres, tortillas, cremas, salsas, condimentos, snacks, comida de mascotas y
  productos no alimentarios que solo mencionan una fruta u hortaliza.
- Validadas en modo de solo lectura las consultas `manzana`, `naranja`, `fresa`,
  `pera`, `tomate`, `patata`, `cebolla`, `calabacín`, `lechuga`, `pimiento`,
  `zanahoria` y `brócoli` contra Mercadona, Froiz, Gadis y Eroski con el código
  postal representativo 15001. Froiz y Gadis, y algunas consultas de Mercadona,
  alcanzaron el límite solicitado: la muestra valida precisión, no exhaustividad.
- El evaluador de ofertas filtra ahora la intención semántica antes de examinar
  promociones y expone productos observados, examinados y rechazados. En la
  repetición real, Lay's/Ruffles dejaron de aparecer como ofertas de patata y la
  prefrita McCain solo se comparó con otra prefrita Maheso.
- Suite completa: 483 pruebas; compilación y Ruff correctos. No se escribió ningún
  carrito ni se alcanzó una ruta de checkout, pedido o pago.

### 2026-08-25 — P0.4 Bebidas

- Separadas agua, refrescos, zumos/néctares/smoothies, bebidas vegetales y otras
  bebidas; cerveza, sidra, vino y destilados tienen familias reguladas propias.
- Modelados sabor y variante, con/sin gas, azúcar normal o `zero`, cafeína,
  pulpa, fruta del zumo, base vegetal, estilo barista, calcio y concentración.
- Diferenciados bebida lista para tomar y concentrado, botella PET o de vidrio,
  botellín, garrafa, lata, brik y multipacks con cantidades distintas.
- El alcohol se modeló localmente por tipo, estilo, presencia y graduación. No se
  consultaron catálogos de productos regulados ni se habilitó su automatización;
  las protecciones existentes continúan bloqueándolos en operaciones de compra.
- Excluidos agua destilada, oxigenada o micelar, productos de limpieza/cosmética,
  golosinas de cola, cacao `COLA CAO`, platos con cerveza y salsas con destilados.
- Validadas en modo de solo lectura las consultas `agua`, `agua con gas`,
  `refresco cola`, `zumo naranja`, `bebida avena` y `bebida almendra` contra
  Mercadona, Froiz, Gadis y Eroski con el código postal representativo 15001.
  Varias respuestas de Froiz, Gadis y Mercadona alcanzaron el límite solicitado:
  la muestra valida precisión, no exhaustividad.
- En ofertas reales, el agua con gas se comparó solo con agua compatible; refresco
  normal, `zero`, sin cafeína, sabores y formatos quedaron separados; los zumos
  respetaron fruta, pulpa y preparación; y una avena con matcha no sustituyó a una
  barista neutra. Los resultados irrelevantes se contabilizaron como rechazados.
- Suite completa: 489 pruebas; compilación y Ruff correctos. No se escribió ningún
  carrito ni se alcanzó una ruta de checkout, pedido o pago.

### 2026-08-25 — P0.5 Congelados y platos preparados

- Añadidos subtipos críticos para pizza, croqueta, empanada/empanadilla, lasaña,
  ensalada, sopa, crema, caldo, paella, arroz/pasta preparados, tortilla, salteado,
  puré, poke, sándwich y fritura, sin perder la familia común `prepared_meal`.
- Modelados ingrediente principal y variantes observables: jamón e ibérico, pollo,
  bacalao, merluza, atún, marisco, carne, queso, verdura y mezclas; cuatro quesos,
  barbacoa, César, boloñesa, minestrone y estilos de paella, entre otros.
- Separados plato completo, mezcla de hojas, sopa/crema deshidratada, caldo y kit de
  ingredientes; además de congelado, refrigerado y seco, y listo para comer,
  calentar, cocinar, reconstituir o usar como ingrediente.
- Corregida la confusión de lasañas listas con pasta seca: placas, hojas, cajas y
  pasta de lasaña permanecen en `pasta`; lasañas de carne, pollo, atún, verduras o
  espinacas y queso se reconocen como platos preparados.
- Excluidas masas, bases y obleas de pizza/empanada, cremas cosméticas y hortalizas
  «a la crema». Las ofertas ya no usan un relleno más barato, otra conservación o
  un kit de paella como alternativa directa del producto promocionado.
- Validadas en modo de solo lectura las consultas `pizza`, `croquetas`, `lasaña`,
  `empanada`, `ensalada`, `sopa`, `crema de verduras` y `paella` contra Mercadona,
  Froiz, Gadis y Eroski con el código postal representativo 15001. La consulta sin
  tilde `lasana` evitó una respuesta 404 observada en Eroski para la variante
  acentuada. Pizza y ensalada, y varias respuestas de Froiz/Gadis, alcanzaron el
  límite solicitado: la muestra valida precisión y diversidad, no exhaustividad.
- Suite completa: 496 pruebas; compilación y Ruff correctos. No se escribió ningún
  carrito ni se alcanzó una ruta de checkout, pedido o pago.

### 2026-08-25 — P0.6 Despensa restante

- Separadas harina por cereal/legumbre, refinado e intención de uso; azúcar y
  edulcorantes por sustancia y formato; sal por origen, grano, yodo y sabor; y
  salsas por tipo, estilo, sal y azúcar observable.
- Añadidos perfiles críticos para cereales, galletas, snacks, especias, levaduras y
  conservas. Los caldos distinguen líquido, concentrado, polvo y pastillas, además
  del ingrediente principal y la necesidad de dilución.
- La consulta transversal `conservas` conserva intencionadamente las familias de
  atún, pescado, marisco, fruta, verdura o legumbre en vez de inventar una única
  identidad. Su medio y conservación siguen siendo facetas del alimento real.
- Validadas `harina`, `azúcar`, `sal`, `salsa soja`, `cereales`, `galletas`,
  `especias` y `caldo` en los cuatro catálogos. Las expansiones de especias
  recuperaron productos reales en los cuatro y descartaron harina, cerveza y
  otros resultados del buscador. Las muestras estuvieron frecuentemente saturadas.

### 2026-08-25 — P0.7 Limpieza del hogar

- Diferenciados detergente de ropa, lavavajillas manual y de máquina, con forma,
  concentración y especialidad textil; suavizante, lejía, limpiador por superficie,
  sal/aditivos de lavavajillas, papel de cocina y bolsas por capacidad y cierre.
- Excluidos electrodomésticos que contienen «lavavajillas» y la marca blanca dejó
  de interpretarse erróneamente como detergente específico para ropa blanca.
- Validadas `detergente`, `lavavajillas a mano`, `suavizante`, `lejía`, `limpiador`
  y `bolsas basura` en Mercadona, Froiz, Gadis y Eroski. Una oferta de detergente
  de ropa solo usa otra alternativa de ropa, nunca lavavajillas más barato.

### 2026-08-25 — P0.8 Higiene y cosmética

- Creadas familias separadas para champú, gel de ducha, jabón, desodorante,
  dentífrico, cepillo, colutorio, higiene femenina y cuidado de la piel, con
  necesidad, formato, usuario y edad cuando el nombre aporta evidencia.
- `pasta dental` se expande a `dentífrico` y `pasta de dientes`; `gel ducha` a
  `gel de baño`. Gadis recuperó dentífricos mediante la expansión. Froiz continuó
  devolviendo pasta alimentaria para esos términos y se descartó de forma segura.
- Validadas además `champú`, `jabón manos`, `desodorante` y `cepillo dental` en los
  cuatro catálogos. No se mezclan champú/gel/jabón ni durezas o usos incompatibles.

### 2026-08-25 — P0.9 Productos infantiles y mascotas

- Alimentación infantil distingue tarro, bolsita, papilla y snack, edad mínima e
  ingrediente principal. Higiene infantil diferencia pañal y toallitas y conserva
  la talla explícita.
- Mascotas distingue perro, gato, conejo y hámster; seco, húmedo o snack; etapa,
  esterilizado/mini/light/bolas de pelo e ingrediente principal.
- Validadas `potito`, `comida perro` y `comida gato` en los cuatro catálogos. Las
  expansiones de potito recuperaron tarritos en Gadis, Froiz y Eroski; Mercadona no
  mostró tarritos equivalentes en la muestra y sus papillas/bolsitas se rechazaron
  al pedir específicamente tarro. Nunca se mezclan especie, formato o necesidad.

### 2026-08-25 — P0.10 Completar familias ya reconocidas

- Ampliadas variedades y formatos de queso, arroz y yogur; intensidad del café,
  uso del aceite, forma de pasta, firmeza del tofu, cereal y masa madre del pan,
  formato/tamaño de huevo, calidad de jamón y conservación/medio del salmón.
- Corregido el tamaño de huevo para que la `s` final de «huevos» no se interprete
  como talla S. Pasta de lenteja conserva simultáneamente base y forma.
- Revalidadas en modo de solo lectura `pan`, `yogur`, `pasta`, `arroz`, `tofu`,
  `jamón`, `atún` y `salmón` en Mercadona, Froiz, Gadis y Eroski; todos los aceptados
  conservaron su familia. Los límites alcanzados impiden afirmar exhaustividad.
- Suite final P0: 508 pruebas; compilación y Ruff correctos. No se escribió ningún
  carrito ni se alcanzó una ruta de checkout, pedido o pago.
