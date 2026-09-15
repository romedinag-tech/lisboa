#!/usr/bin/env python3
"""Monitoreo de pasajes Concepción -> Portugal, julio 2027.

Consulta Google Flights a través de SerpApi, guarda cada respuesta cruda
(comprimida, sin la clave), la destila a CSV y regenera index.html.

Uso:
  python -X utf8 monitor.py diario          # consultas del día
  python -X utf8 monitor.py diario --publicar   # ídem + commit y push (lo corre la tarea de Windows)
  python -X utf8 monitor.py maletas         # tarifas y equipaje de lo más barato por corredor
  python -X utf8 monitor.py factibilidad    # prueba chica antes de dejarlo corriendo
  python -X utf8 monitor.py dashboard       # solo regenera index.html desde los CSV
  python -X utf8 monitor.py plan [--dias N] # muestra qué se consultaría, sin gastar créditos

La clave se lee de la variable SERPAPI_KEY o del archivo ~/.secrets/serpapi.txt.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent
DATA = RAIZ / "data"
RAW = DATA / "raw"
CFG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
VIAJE, SA = CFG["viaje"], CFG["serpapi"]
ENDPOINT = "https://serpapi.com/search.json"
INICIO_ROTACION = dt.date(2026, 9, 14)

CAMPOS = {
    "consultas.csv": ["fecha", "hora_utc", "modo", "clave", "tipo", "origen", "destino", "vuelta_desde",
                      "ida", "vuelta", "estado", "n_opciones", "precio_min", "creditos_restantes",
                      "serpapi_id", "archivo_raw", "nota"],
    "opciones.csv": ["fecha", "clave", "tipo", "origen", "destino", "vuelta_desde", "ida", "vuelta", "grupo", "orden",
                     "corredor", "via", "aerolineas", "vuelos", "salida", "llegada", "duracion_min",
                     "n_escalas", "precio", "extensiones"],
    "insights.csv": ["fecha", "clave", "precio_mas_bajo", "nivel", "tipico_min", "tipico_max"],
    "historial_google.csv": ["clave", "fecha", "precio"],
    "maletas.csv": ["fecha", "clave", "corredor", "aerolineas_ida", "aerolineas_vuelta", "precio_itinerario",
                    "vendedor", "es_aerolinea", "tarifa", "precio_opcion", "equipaje", "extensiones", "nota"],
}


# ---------------------------------------------------------------- utilidades

def clave_api():
    k = os.environ.get("SERPAPI_KEY", "").strip()
    if not k:
        f = pathlib.Path.home() / ".secrets" / "serpapi.txt"
        if f.exists():
            k = f.read_text(encoding="utf-8-sig").strip()  # -sig: el Bloc de notas puede poner BOM
    if not k:
        sys.exit("Falta la clave de SerpApi (variable SERPAPI_KEY o ~/.secrets/serpapi.txt).")
    return k


def ahora():
    return dt.datetime.now(dt.timezone.utc)


def fecha_chile():
    # Fecha local del PC, que está en hora de Chile y aplica el horario de verano. Un desfase fijo
    # (UTC-4) fechaba la corrida de las 00:30 en verano como el día anterior.
    return dt.datetime.now().date()


def leer_csv(nombre):
    p = DATA / nombre
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def agregar_csv(nombre, filas):
    if not filas:
        return
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / nombre
    nuevo = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS[nombre], extrasaction="ignore", lineterminator="\n")
        if nuevo:
            w.writeheader()
        w.writerows(filas)


def escribir_csv(nombre, filas):
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / nombre).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS[nombre], extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(filas)


def http_json(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")
        try:
            return json.loads(cuerpo)
        except ValueError:
            return {"error": f"HTTP {e.code}: {cuerpo[:300]}"}
    except Exception as e:  # red, timeout
        return {"error": f"{type(e).__name__}: {e}"}


def creditos(key):
    """La API de cuenta de SerpApi no consume búsquedas."""
    r = http_json("https://serpapi.com/account.json?" + urllib.parse.urlencode({"api_key": key}), 30)
    for campo in ("total_searches_left", "plan_searches_left"):
        if isinstance(r.get(campo), int):
            return r[campo]
    return None


# ---------------------------------------------------------------- consultas

def consulta_rt(origen, destino, ida, vuelta):
    return {"clave": f"RT-{origen}-{destino}-{ida:%Y%m%d}-{vuelta:%Y%m%d}", "tipo": "RT",
            "origen": origen, "destino": destino, "vuelta_desde": destino, "ida": ida, "vuelta": vuelta}


def consulta_oj(origen, llega, sale, ida, vuelta):
    """Open jaw: se llega a un aeropuerto y se vuelve desde el otro."""
    return {"clave": f"OJ-{origen}-{llega}-{sale}-{ida:%Y%m%d}-{vuelta:%Y%m%d}", "tipo": "OJ",
            "origen": origen, "destino": llega, "vuelta_desde": sale, "ida": ida, "vuelta": vuelta}


def fechas_canonicas():
    return dt.date.fromisoformat(VIAJE["ida_canonica"]), dt.date.fromisoformat(VIAJE["vuelta_canonica"])


def catalogo():
    """fijas: todos los días · rotacion: por turnos. El tramo nacional no se consulta: se compra aparte y
    después; para comparar se usa el valor de referencia medido que está en config.json."""
    ida0, v0 = fechas_canonicas()
    o, ref = VIAJE["origen_principal"], VIAJE["origen_referencia"]
    destinos = sorted(VIAJE["destinos"], key=lambda d: d != VIAJE["destino_preferido"])
    fijas = [consulta_rt(o, d, ida0, v0) for d in destinos] + [consulta_rt(ref, destinos[0], ida0, v0)]
    a, b = destinos[0], destinos[1]
    rotacion = [consulta_oj(o, a, b, ida0, v0), consulta_oj(o, b, a, ida0, v0)]
    for ida in map(dt.date.fromisoformat, VIAJE["idas"]):
        for vuelta in map(dt.date.fromisoformat, VIAJE["vueltas"]):
            if (ida, vuelta) == (ida0, v0):
                continue
            for d in destinos:
                rotacion.append(consulta_rt(o, d, ida, vuelta))
    return fijas, rotacion


def llegada_max():
    return dt.date.fromisoformat(VIAJE["llegada_max_chile"])


SUFIJO_LATAM = "-LA"


def consulta_latam():
    """Santiago → Lisboa en fechas base, solo LATAM: el único destino con itinerario 100 % LATAM (medido 2026-09-15).
    Se usa en las corridas extra del CyberMonday para no depender de que la búsqueda general muestre LATAM."""
    ida0, v0 = fechas_canonicas()
    q = consulta_rt(VIAJE["origen_principal"], "LIS", ida0, v0)
    q["clave"] += SUFIJO_LATAM
    q["filtro"] = "LA"
    return q


def plan_del_dia(dia):
    fijas, rot = catalogo()
    k = SA["rotacion_diaria"]
    i = ((dia - INICIO_ROTACION).days * k) % len(rot)
    return fijas + [rot[(i + j) % len(rot)] for j in range(k)]


def parametros(q, extra=None):
    p = {"engine": "google_flights", "adults": VIAJE["pasajeros"]["adultos"],
         "children": VIAJE["pasajeros"]["ninos"], "currency": SA["moneda"], "gl": SA["gl"], "hl": SA["hl"]}
    if SA.get("deep_search"):
        p["deep_search"] = "true"
    if q.get("filtro"):
        p["include_airlines"] = q["filtro"]
    if q["tipo"] == "RT":
        p.update({"type": 1, "departure_id": q["origen"], "arrival_id": q["destino"],
                  "outbound_date": q["ida"].isoformat(), "return_date": q["vuelta"].isoformat()})
    else:
        p.update({"type": 3, "multi_city_json": json.dumps([
            {"departure_id": q["origen"], "arrival_id": q["destino"], "date": q["ida"].isoformat()},
            {"departure_id": q["vuelta_desde"], "arrival_id": q["origen"], "date": q["vuelta"].isoformat()}])})
    if extra:
        p.update(extra)
    return p


def buscar(key, q, extra=None, sufijo=""):
    """Una búsqueda = un crédito. Devuelve (respuesta, ruta_raw)."""
    p = parametros(q, extra)
    p["api_key"] = key
    r = http_json(ENDPOINT + "?" + urllib.parse.urlencode(p), SA["timeout_s"])
    dia = fecha_chile()
    carpeta = RAW / f"{dia:%Y-%m-%d}"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{q['clave']}{sufijo}.json.gz"
    texto = json.dumps(r, ensure_ascii=False).replace(key, "***")  # el repo es público
    with gzip.open(ruta, "wt", encoding="utf-8") as fh:
        fh.write(texto)
    return json.loads(texto), ruta.relative_to(RAIZ).as_posix()


# ---------------------------------------------------------------- destilado

def corredor(escalas):
    chile = set(CFG["aeropuertos_chile"])
    for a in escalas:
        if a in chile:
            continue
        for c in CFG["corredores"]:
            if a in c["aeropuertos"]:
                return c["id"]
        return "otra"
    return "otra"


def opciones(r):
    for grupo in ("best_flights", "other_flights"):
        for n, o in enumerate(r.get(grupo) or []):
            yield grupo, n, o


def destilar(q, r, dia):
    filas = []
    for grupo, n, o in opciones(r):
        tramos = o.get("flights") or []
        escalas = [l.get("id") for l in (o.get("layovers") or []) if l.get("id")]
        aerol = list(dict.fromkeys(t.get("airline", "") for t in tramos if t.get("airline")))
        filas.append({
            "fecha": dia.isoformat(), "clave": q["clave"], "tipo": q["tipo"], "origen": q["origen"],
            "destino": q["destino"],
            "vuelta_desde": q["vuelta_desde"], "ida": q["ida"].isoformat(), "vuelta": q["vuelta"].isoformat(),
            "grupo": "mejor" if grupo == "best_flights" else "otro", "orden": n,
            "corredor": corredor(escalas), "via": "-".join(escalas),
            "aerolineas": " / ".join(aerol),
            "vuelos": " · ".join(t.get("flight_number", "") for t in tramos),
            "salida": (tramos[0].get("departure_airport") or {}).get("time", "") if tramos else "",
            "llegada": (tramos[-1].get("arrival_airport") or {}).get("time", "") if tramos else "",
            "duracion_min": o.get("total_duration", ""), "n_escalas": len(escalas),
            "precio": o.get("price", ""),  # NULL si Google no publica precio
            "extensiones": " | ".join(o.get("extensions") or []),
        })
    return filas


def destilar_insights(q, r, dia):
    pi = r.get("price_insights") or {}
    rango = pi.get("typical_price_range") or [None, None]
    fila = {"fecha": dia.isoformat(), "clave": q["clave"], "precio_mas_bajo": pi.get("lowest_price", ""),
            "nivel": pi.get("price_level", ""), "tipico_min": rango[0] if len(rango) > 0 else "",
            "tipico_max": rango[1] if len(rango) > 1 else ""}
    hist = []
    for par in pi.get("price_history") or []:
        if isinstance(par, list) and len(par) == 2 and par[1] is not None:
            ts = par[0] / 1000 if par[0] > 1e11 else par[0]
            hist.append({"clave": q["clave"], "fecha": dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat(),
                         "precio": par[1]})
    return (fila if pi else None), hist


def fusionar_historial(nuevas):
    if not nuevas:
        return
    idx = {(f["clave"], f["fecha"]): f for f in leer_csv("historial_google.csv")}
    for f in nuevas:
        idx[(f["clave"], f["fecha"])] = f
    escribir_csv("historial_google.csv", sorted(idx.values(), key=lambda f: (f["clave"], f["fecha"])))


def precio_min(filas):
    ps = [int(f["precio"]) for f in filas if str(f["precio"]).isdigit()]
    return min(ps) if ps else ""


# ---------------------------------------------------------------- modos

def correr(key, consultas, modo):
    dia = fecha_chile()
    # el modo diario no repite en el mismo día (reintentos de la tarea); "fijas" sí puede
    hechas = {(f["fecha"], f["clave"]) for f in leer_csv("consultas.csv")
              if f["estado"] == "ok" and f["modo"] == modo == "diario"}
    saldo = creditos(key)
    print(f"Créditos disponibles al inicio: {saldo}")
    hist = []
    for q in consultas:
        if (dia.isoformat(), q["clave"]) in hechas:
            print(f"  = {q['clave']} ya consultada hoy, se omite")
            continue
        if saldo is not None and saldo <= SA["reserva_creditos"] and q not in catalogo()[0]:
            print(f"  ! {q['clave']} omitida: quedan {saldo} créditos (reserva {SA['reserva_creditos']})")
            agregar_csv("consultas.csv", [registro(q, modo, "omitida_reserva", nota=f"saldo {saldo}")])
            continue
        # las corridas extra se repiten en el día: la hora en el nombre evita pisar la respuesta cruda anterior
        suf = "" if modo == "diario" else f"_{modo}_{dt.datetime.now():%H%M}"
        r, ruta = buscar(key, q, sufijo=suf)
        if r.get("error"):
            print(f"  x {q['clave']}: {r['error']}")
            agregar_csv("consultas.csv", [registro(q, modo, "error", archivo=ruta, nota=str(r["error"])[:200])])
            continue
        nota = ""
        if q["tipo"] == "RT" and q["vuelta"] >= llegada_max():
            filas, nota = verificar_vuelta(key, q, r, dia, suf)
        else:
            filas = destilar(q, r, dia)
        ins, h = destilar_insights(q, r, dia)
        agregar_csv("opciones.csv", filas)
        agregar_csv("insights.csv", [ins] if ins else [])
        hist += h
        saldo = creditos(key)
        agregar_csv("consultas.csv", [registro(q, modo, "ok", len(filas), precio_min(filas), saldo,
                                               (r.get("search_metadata") or {}).get("id", ""), ruta, nota)])
        print(f"  ✓ {q['clave']}: {len(filas)} opciones, mínimo {precio_min(filas)} {SA['moneda']}, "
              f"quedan {saldo}")
    fusionar_historial(hist)


def verificar_vuelta(key, q, r, dia, suf):
    """Vuelta que sale de Portugal el día límite: el precio de la búsqueda de ida y vuelta puede venir de una vuelta
    que llega a Chile al día siguiente (medido 2026-09-15: de 3 vueltas del 1 ago, 2 llegaban el 2). Se toma la ida
    más barata, se piden sus vueltas (1 crédito más) y solo se registran las que llegan a tiempo. Si ninguna llega,
    el día queda sin precio para esa combinación: no se rellena."""
    idas = [o for _, _, o in opciones(r) if o.get("departure_token") and isinstance(o.get("price"), int)]
    if not idas:
        return [], "sin ida con departure_token para verificar la vuelta"
    ida = min(idas, key=lambda o: o["price"])
    base = next(f for f in destilar(q, {"best_flights": [ida]}, dia))
    rv, _ = buscar(key, q, {"departure_token": ida["departure_token"]}, suf + "_vueltas")
    if rv.get("error"):
        return [], f"error al pedir vueltas: {str(rv['error'])[:120]}"
    filas, total = [], 0
    for grupo, n, v in opciones(rv):
        tramos = v.get("flights") or []
        if not tramos or not isinstance(v.get("price"), int):
            continue
        total += 1
        llega = (tramos[-1].get("arrival_airport") or {}).get("time", "")
        if not llega or dt.date.fromisoformat(llega[:10]) > llegada_max():
            continue
        fila = dict(base, grupo="mejor" if grupo == "best_flights" else "otro", orden=n, precio=v["price"])
        fila["extensiones"] = (f"Vuelta verificada: {' · '.join(t.get('flight_number', '') for t in tramos)}, "
                               f"sale {(tramos[0].get('departure_airport') or {}).get('time', '')}, llega {llega}")
        filas.append(fila)
    return filas, f"vuelta verificada: {len(filas)} de {total} llegan a Chile a más tardar el {llegada_max():%d-%m}"


def registro(q, modo, estado, n="", pmin="", saldo="", sid="", archivo="", nota=""):
    t = ahora()
    return {"fecha": fecha_chile().isoformat(), "hora_utc": t.strftime("%H:%M"), "modo": modo, "clave": q["clave"],
            "tipo": q["tipo"], "origen": q["origen"], "destino": q["destino"], "vuelta_desde": q["vuelta_desde"],
            "ida": q["ida"].isoformat(), "vuelta": q["vuelta"].isoformat(), "estado": estado, "n_opciones": n,
            "precio_min": pmin, "creditos_restantes": "" if saldo is None else saldo, "serpapi_id": sid,
            "archivo_raw": archivo, "nota": nota}


def leer_raw(ruta):
    with gzip.open(RAIZ / ruta, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def modo_maletas(key):
    """Para lo más barato de cada corredor en la búsqueda canónica del destino preferido:
    elige la vuelta más barata y baja las opciones de compra (tarifa y equipaje).
    Cuesta 2 créditos por corredor."""
    dia = fecha_chile()
    q = catalogo()[0][0]
    ult = [f for f in leer_csv("consultas.csv") if f["clave"] == q["clave"] and f["estado"] == "ok"]
    if not ult:
        print("No hay búsqueda canónica registrada todavía.")
        return
    r = leer_raw(ult[-1]["archivo_raw"])
    mejores = {}
    for _, _, o in opciones(r):
        if not o.get("departure_token") or not isinstance(o.get("price"), int):
            continue
        c = corredor([l.get("id") for l in o.get("layovers") or []])
        if c not in mejores or o["price"] < mejores[c]["price"]:
            mejores[c] = o
    filas = []
    # solo los 3 corredores más baratos: 6 créditos por semana
    for c, o in sorted(mejores.items(), key=lambda kv: kv[1]["price"])[:3]:
        aer_ida = " / ".join(dict.fromkeys(t.get("airline", "") for t in o.get("flights") or []))
        rv, _ = buscar(key, q, {"departure_token": o["departure_token"]}, f"_vuelta_{c}")
        vueltas = [v for _, _, v in opciones(rv) if v.get("booking_token") and isinstance(v.get("price"), int)]
        if not vueltas:
            filas.append({"fecha": dia.isoformat(), "clave": q["clave"], "corredor": c, "aerolineas_ida": aer_ida,
                          "precio_itinerario": o["price"], "nota": rv.get("error", "sin vueltas con booking_token")})
            continue
        v = min(vueltas, key=lambda x: x["price"])
        aer_v = " / ".join(dict.fromkeys(t.get("airline", "") for t in v.get("flights") or []))
        rb, _ = buscar(key, q, {"booking_token": v["booking_token"]}, f"_compra_{c}")
        ops = rb.get("booking_options") or []
        if not ops:
            filas.append({"fecha": dia.isoformat(), "clave": q["clave"], "corredor": c, "aerolineas_ida": aer_ida,
                          "aerolineas_vuelta": aer_v, "precio_itinerario": v["price"],
                          "nota": rb.get("error", "sin opciones de compra")})
        for op in ops:
            partes = [op["together"]] if op.get("together") else [op.get("departing") or {}, op.get("returning") or {}]
            for i, p in enumerate(partes):
                filas.append({
                    "fecha": dia.isoformat(), "clave": q["clave"], "corredor": c, "aerolineas_ida": aer_ida,
                    "aerolineas_vuelta": aer_v, "precio_itinerario": v["price"], "vendedor": p.get("book_with", ""),
                    "es_aerolinea": p.get("airline", ""), "tarifa": p.get("option_title", ""),
                    "precio_opcion": p.get("price", ""), "equipaje": " | ".join(p.get("baggage_prices") or []),
                    "extensiones": " | ".join(p.get("extensions") or []),
                    "nota": "" if len(partes) == 1 else ("tramo ida (pasajes separados)" if i == 0 else "tramo vuelta (pasajes separados)")})
        print(f"  ✓ maletas {c}: {len(ops)} opciones de compra")
    agregar_csv("maletas.csv", filas)
    agregar_csv("consultas.csv", [registro(q, "maletas", "ok", len(filas), saldo=creditos(key))])


def modo_factibilidad(key):
    """Mide lo que hay que saber antes de dejarlo corriendo: si el precio es del grupo o por
    persona, qué corredores aparecen, cuánto cuesta cada tipo de llamada y qué trae el open jaw."""
    ida0, v0 = fechas_canonicas()
    q = consulta_rt(VIAJE["origen_principal"], VIAJE["destino_preferido"], ida0, v0)
    s0 = creditos(key)
    print(f"Créditos iniciales: {s0}")
    r, ruta = buscar(key, q, sufijo="_prueba_grupo")
    s1 = creditos(key)
    print(f"[1] {q['clave']} 2A+1N -> {ruta}  créditos gastados: {None if s0 is None else s0 - s1}")
    if r.get("error"):
        sys.exit(f"Error: {r['error']}")
    filas = destilar(q, r, fecha_chile())
    por_corr = {}
    for f in filas:
        por_corr.setdefault(f["corredor"], []).append(f)
    print(f"    {len(filas)} opciones; por corredor:")
    for c, fs in sorted(por_corr.items()):
        print(f"      {c:7s} n={len(fs):2d} min={precio_min(fs)}  ej: {fs[0]['via']} {fs[0]['aerolineas']}")
    pi = r.get("price_insights") or {}
    print(f"    price_insights: nivel={pi.get('price_level')} tipico={pi.get('typical_price_range')} "
          f"historial={len(pi.get('price_history') or [])} puntos")
    r1, _ = buscar(key, q, {"adults": 1, "children": 0}, "_prueba_1adulto")
    print(f"[2] mismo vuelo, 1 adulto: mínimo {precio_min(destilar(q, r1, fecha_chile()))} "
          f"vs grupo {precio_min(filas)}  (si el grupo ~ 3x, el precio es total)")
    oj = consulta_oj(VIAJE["origen_principal"], VIAJE["destino_preferido"], "LIS", ida0, v0)
    ro, _ = buscar(key, oj, sufijo="_prueba")
    fo = destilar(oj, ro, fecha_chile())
    print(f"[3] open jaw {oj['clave']}: {ro.get('error') or ''} {len(fo)} opciones, mínimo {precio_min(fo)}")
    s2 = creditos(key)
    print(f"Créditos finales: {s2}  (gastados en total: {None if s0 is None else s0 - s2})")


# ---------------------------------------------------------------- dashboard

# códigos IATA del grupo LATAM (Chile, Brasil, Perú, Ecuador, Colombia, Argentina, Paraguay)
LATAM = {"LA", "JJ", "LP", "XL", "4C", "4M", "PZ"}


def vuelos_de(texto):
    """'IB 118 · IB 543' (formato actual) o 'IB 118 IB 543' (registros del 14 y 15 sep)."""
    return re.findall(r"[A-Z0-9]{2} \d{1,4}", texto or "")


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def construir_dashboard():
    cons = leer_csv("consultas.csv")
    ops_todas = [f for f in leer_csv("opciones.csv") if num(f["precio"]) is not None]
    # las búsquedas filtradas por aerolínea no representan el mercado: solo alimentan el seguimiento LATAM
    ops = [f for f in ops_todas if not f["clave"].endswith(SUFIJO_LATAM)]
    ins = leer_csv("insights.csv")
    hist = leer_csv("historial_google.csv")
    mal = leer_csv("maletas.csv")
    fijas, rot = catalogo()
    ida0, v0 = fechas_canonicas()

    # serie diaria: mínimo por corredor y total, por clave
    serie = {}
    for f in ops:
        s = serie.setdefault(f["clave"], {}).setdefault(f["fecha"], {})
        p = num(f["precio"])
        for c in (f["corredor"], "todos"):
            s[c] = min(s.get(c, p), p)

    # grilla: última observación por (destino, ida, vuelta) de ida y vuelta al mismo aeropuerto
    grilla = {}
    for f in ops:
        if f["tipo"] != "RT":
            continue
        k = (f["origen"], f["destino"], f["ida"], f["vuelta"])
        g = grilla.get(k)
        p = num(f["precio"])
        if g is None or f["fecha"] > g["fecha"]:
            grilla[k] = {"origen": f["origen"], "destino": f["destino"], "ida": f["ida"], "vuelta": f["vuelta"], "fecha": f["fecha"],
                         "precio": p, "corredor": f["corredor"]}
        elif f["fecha"] == g["fecha"] and p < g["precio"]:
            g.update(precio=p, corredor=f["corredor"])

    # seguimiento LATAM (sale de las mismas búsquedas, sin créditos extra): mínimo diario de itinerarios
    # 100 % LATAM y de itinerarios con al menos un tramo LATAM. Un día en que Google no muestre LATAM queda vacío.
    # Incluye las búsquedas filtradas por LATAM de los días Cyber (clave con sufijo -LA), sumadas a su búsqueda base.
    latam = {}
    for f in ops_todas:
        cods = [v.split()[0] for v in vuelos_de(f["vuelos"])]
        if not cods or not any(c in LATAM for c in cods):
            continue
        tipo = "todo" if all(c in LATAM for c in cods) else "con"
        d = latam.setdefault(f["clave"].removesuffix(SUFIJO_LATAM), {}).setdefault(f["fecha"], {})
        p = num(f["precio"])
        if tipo not in d or p < d[tipo]["precio"]:
            d[tipo] = {"precio": p, "vuelos": " · ".join(vuelos_de(f["vuelos"])), "via": f["via"],
                       "aerolineas": f["aerolineas"], "salida": f["salida"], "llegada": f["llegada"],
                       "duracion_min": f["duracion_min"]}

    # opciones del último registro de cada clave
    ultimas = {}
    for clave in {f["clave"] for f in ops}:
        fs = [f for f in ops if f["clave"] == clave]
        fmax = max(f["fecha"] for f in fs)
        top = sorted((f for f in fs if f["fecha"] == fmax), key=lambda f: num(f["precio"]))[:15]
        ultimas[clave] = [{k: f[k] for k in ("fecha", "corredor", "via", "aerolineas", "vuelos", "salida",
                                              "llegada", "duracion_min", "precio", "extensiones")} for f in top]

    insights = {}
    for f in ins:
        insights[f["clave"]] = f  # el CSV está en orden de llegada: queda el último
    historial = {}
    for f in hist:
        historial.setdefault(f["clave"], []).append([f["fecha"], num(f["precio"])])

    fmal = max((f["fecha"] for f in mal), default=None)
    ok = [f for f in cons if f["estado"] == "ok"]

    # enlace a la misma búsqueda en Google Flights (fechas, ruta y 2 adultos + 1 niño van en el parámetro tfs)
    urls = {}
    for f in ok:
        if f["archivo_raw"] and (RAIZ / f["archivo_raw"]).exists():
            urls[f["clave"]] = f["archivo_raw"]  # queda el más reciente
    for clave, ruta in urls.items():
        try:
            urls[clave] = (leer_raw(ruta).get("search_metadata") or {}).get("google_flights_url")
        except (OSError, ValueError):
            urls[clave] = None
    datos = {
        "generado": ahora().strftime("%Y-%m-%d %H:%M UTC"),
        "viaje": {"origen": VIAJE["origen_principal"], "referencia": VIAJE["origen_referencia"],
                  "origenes": VIAJE["origenes"], "tramo_nacional": VIAJE["tramo_nacional"], "pasajeros": VIAJE["pasajeros"], "maletas_bodega": VIAJE["maletas_bodega"],
                  "ida": ida0.isoformat(), "vuelta": v0.isoformat(), "idas": VIAJE["idas"], "vueltas": VIAJE["vueltas"],
                  "llegada_max": VIAJE["llegada_max_chile"],
                  "destinos": VIAJE["destinos"], "preferido": VIAJE["destino_preferido"], "moneda": SA["moneda"]},
        "corredores": [{"id": c["id"], "nombre": c["nombre"]} for c in CFG["corredores"]] +
                      [{"id": "otra", "nombre": "Otra ruta"}],
        "claves": {q["clave"]: {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in q.items()}
                   for q in fijas + rot},
        "fijas": [q["clave"] for q in fijas],
        "serie": serie, "grilla": list(grilla.values()), "ultimas": ultimas, "urls": urls, "latam": latam,
        "insights": insights, "historial": historial,
        "maletas": [f for f in mal if f["fecha"] == fmal],
        "registro": {"consultas_ok": len(ok), "dias": len({f["fecha"] for f in ok}),
                     "primera": min((f["fecha"] for f in ok), default=None),
                     "ultima": max((f["fecha"] for f in ok), default=None),
                     "creditos": next((f["creditos_restantes"] for f in reversed(cons) if f["creditos_restantes"]), None),
                     "errores": sum(f["estado"] == "error" for f in cons)},
    }
    plantilla = (RAIZ / "plantilla.html").read_text(encoding="utf-8")
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (RAIZ / "index.html").write_text(plantilla.replace("/*__DATOS__*/null", js), encoding="utf-8", newline="\n")
    print(f"index.html regenerado: {len(ops)} opciones con precio, {len(ok)} consultas ok")


class Tee:
    """Duplica la salida a la consola y al log de la corrida."""
    def __init__(self, *flujos):
        self.flujos = [f for f in flujos if f is not None]  # pythonw no tiene consola

    def write(self, s):
        for f in self.flujos:
            f.write(s)
            f.flush()

    def flush(self):
        for f in self.flujos:
            f.flush()


def git(*args):
    import subprocess
    sin_ventana = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # la tarea corre con pythonw: sin consolas emergentes
    r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8",
                       creationflags=sin_ventana)
    salida = (r.stdout + r.stderr).strip()
    if salida:
        print(f"  git {' '.join(args)}: {salida}")
    return r.returncode


def publicar():
    """Sube el registro y el dashboard a GitHub (Pages se actualiza solo)."""
    git("add", *[p for p in ("data", "index.html") if (RAIZ / p).exists()])
    if git("diff", "--cached", "--quiet") == 0:
        print("Publicar: sin cambios.")
        return 0
    if git("commit", "-q", "-m", f"Registro {fecha_chile().isoformat()}") != 0:
        return 1
    codigo = git("push", "-q", "origin", "main")
    print("Publicado en GitHub." if codigo == 0 else f"ERROR al hacer push (código {codigo}).")
    return codigo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modo", choices=["diario", "fijas", "cyber", "maletas", "factibilidad", "dashboard", "plan"])
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--publicar", action="store_true", help="commit y push de data/ e index.html al terminar")
    a = ap.parse_args()
    if a.publicar:
        (RAIZ / "logs").mkdir(exist_ok=True)
        log = (RAIZ / "logs" / f"{fecha_chile():%Y-%m-%d}_{a.modo}.log").open("a", encoding="utf-8")
        sys.stdout = sys.stderr = Tee(sys.__stdout__, log)
        print(f"\n=== {ahora():%Y-%m-%d %H:%M} UTC · {a.modo} ===")
    ejecutar(a)
    if a.publicar:
        sys.exit(publicar())


def ejecutar(a):
    if a.modo == "plan":
        for i in range(a.dias):
            d = fecha_chile() + dt.timedelta(i)
            print(d, [q["clave"] for q in plan_del_dia(d)])
        _, rot = catalogo()
        print(f"Rotación: {len(rot)} consultas; ciclo completo cada {len(rot) / SA['rotacion_diaria']:.1f} días")
        return
    if a.modo == "dashboard":
        construir_dashboard()
        return
    key = clave_api()
    if a.modo == "factibilidad":
        modo_factibilidad(key)
        return
    if a.modo == "diario":
        correr(key, plan_del_dia(fecha_chile()), "diario")
        if fecha_chile().weekday() == SA["maletas_dia_semana"]:
            modo_maletas(key)
    elif a.modo == "fijas":
        # corrida extra: solo fechas base, se puede repetir en el día
        correr(key, catalogo()[0], "fijas")
    elif a.modo == "cyber":
        # corridas extra del CyberMonday (5-7 oct 2026): fechas base + Santiago → Lisboa solo LATAM = 4 créditos
        correr(key, catalogo()[0] + [consulta_latam()], "cyber")
    elif a.modo == "maletas":
        modo_maletas(key)
    construir_dashboard()


if __name__ == "__main__":
    main()
