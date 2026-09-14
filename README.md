# Monitoreo de pasajes · Concepción → Portugal, julio 2027

Registro diario de precios de Google Flights (vía [SerpApi](https://serpapi.com/google-flights-api)) para
decidir cuándo comprar. El dashboard está en `index.html` y se publica en GitHub Pages.

## Qué se consulta

- 2 adultos + 1 niño, ida 12 jul y vuelta 31 jul 2027, con ±2 días en cada extremo.
- **Origen principal Santiago (SCL)**; Concepción (CCP) solo como referencia. Medido el 2026-09-14: el mismo par
  de vuelos LA 706 + IB 1151 costaba $5.202.627 como pasaje único desde CCP y $3.176.457 desde SCL. El tramo
  CCP–SCL se compra aparte y después; para comparar se usa el valor medido en `config.json`.
- Destinos: Oporto, Lisboa y los dos itinerarios que llegan a una ciudad y vuelven desde la otra.
- **Todos los días** (3 créditos): fechas base SCL→OPO, SCL→LIS y CCP→OPO (referencia).
- **Por turnos** (3 créditos diarios): las 48 combinaciones restantes de fechas desde SCL y los 2 itinerarios
  combinados. La vuelta completa toma 16,7 días.
- Búsqueda normal, no `deep_search`: en la prueba, la profunda trajo 2 opciones y la normal 8, más baratas.
- **Domingos** (6 créditos): tarifa y equipaje de la opción más barata de las 3 rutas más económicas.
- Ruta = primera escala fuera de Chile: Madrid, Brasil, Lima, París u otra.

Presupuesto: unos 212 créditos al mes, dentro de los 250 del plan gratuito. Si quedan menos de 12 créditos,
solo se consultan las fechas base.

## Archivos

| Archivo | Contenido |
|---|---|
| `config.json` | pasajeros, fechas, holgura, maletas, corredores, presupuesto |
| `monitor.py` | consulta, destila y regenera el dashboard (solo biblioteca estándar) |
| `plantilla.html` | dashboard; `index.html` es la plantilla con los datos incrustados |
| `data/consultas.csv` | una fila por llamada: estado, mínimo y créditos restantes |
| `data/opciones.csv` | una fila por alternativa de vuelo que devolvió Google |
| `data/insights.csv` | nivel y rango típico que informa Google |
| `data/historial_google.csv` | serie de precios que Google guarda por búsqueda |
| `data/maletas.csv` | tarifas y equipaje (lectura de los domingos) |
| `data/raw/AAAA-MM-DD/*.json.gz` | respuestas originales, sin la clave |

## Dónde corre

Todo corre en el PC de Rodrigo y se sube desde ahí: la clave nunca sale del equipo. Una tarea programada de
Windows ejecuta cada día `monitor.py diario --publicar`, que consulta, regenera `index.html`, hace commit y push,
y GitHub Pages publica. Si el PC está apagado a la hora programada, la tarea corre al encenderlo; un día sin
encender queda sin registro (no se rellena). La salida de cada corrida queda en `logs/`, fuera del repo.

```bash
python -X utf8 monitor.py plan --dias 5
python -X utf8 monitor.py factibilidad
python -X utf8 monitor.py diario --publicar
python -X utf8 monitor.py dashboard
```

La clave se lee de `SERPAPI_KEY` o de `~/.secrets/serpapi.txt`, que queda **fuera** del repo.
