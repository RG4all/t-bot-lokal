---
id: BUG-29
title: Charts zeigten „WebGL is not supported" trotz verfügbarem WebGL
finding: Nutzerbericht „die charts werden nicht dargestellt" (Brave und Firefox, 08.11.2026)
severity: MEDIUM
status: fixed
fixed_in: null
audit_ref: null
tests: trading/tests/test_dashboard_charts.py
---

# BUG-29 – Charts zeigten „WebGL is not supported" trotz verfügbarem WebGL

- **Finding:** Dashboard-Charts blieben leer; an ihrer Stelle stand
  „WebGL is not supported by your browser - visit https://get.webgl.org for
  more info" — reproduzierbar in Brave (Chromium-basiert) und in Firefox, auf
  einer Maschine, auf der `get.webgl.org` einen funktionierenden Kontext
  meldet.
- **Status:** **Fixed** (unreleased)
- **Ursprüngliche Einstufung:** MEDIUM – kein Datenverlust, aber die zentrale
  Analyseansicht des Dashboards war funktionslos; die Fehlermeldung schickt
  Nutzer in die falsche Richtung (Grafiktreiber statt Extension-Abfrage).

## Befund und Root Cause

Die Markt-Charts im Dashboard wurden ausschließlich als `scattergl`-Traces
gerendert. Plotly zeichnet diese über die gebündelte regl-Bibliothek, die
vor dem ersten Frame **zwei WebGL1-Extensions zwingend** anfordert
(`src/lib/prepare_regl.js` → `createRegl(..., extensions:
['ANGLE_instanced_arrays', 'OES_element_index_uint'])`). Fehlt eine davon,
liefert `createRegl` `null`, `prepareRegl` gibt `false` zurück und der Chart
wird durch die statische Meldung `WebGL is not supported by your browser`
ersetzt (`src/lib/show_no_webgl_msg.js`). Einen SVG-Rückfall gibt es für
`scattergl` nicht.

Dass beide Browser betroffen waren, hat zwei voneinander unabhängige
Auslöser, die zum selben Fehlerbild führen:

1. **Extensions im WebGL1-Kontext nicht abfragbar.** Brave 1.93 liefert für
   `getContext('webgl')` den Extensionsatz von WebGL2; `ANGLE_instanced_arrays`
   und `OES_element_index_uint` wurden in WebGL2 in den Core übernommen und
   fehlen dort (brave/brave-browser#57736). Zusätzlich fragt regl die Namen
   **kleingeschrieben** ab (`angle_instanced_arrays`), was laut Spezifikation
   case-insensitiv sein muss, in dem fehlerhaften Build aber `null` ergibt.
2. **Kein nutzbarer GPU-Kontext.** Ohne Hardware-Beschleunigung — VM, RDP,
   deaktivierte Grafikbeschleunigung, blocklistete GPU — existiert gar kein
   WebGL-Kontext. Firefox und Chrome verweigern seit 2025 den stillen
   SwiftShader-Rückfall (`--enable-unsafe-swiftshader` ist Opt-in), sodass
   `getContext('webgl')` `null` liefert. `get.webgl.org` kann in derselben
   Sitzung trotzdem einen anderen (funktionierenden) Pfad testen — der
   Hinweis in der Meldung ist deshalb irreführend.

Nachgestellt in Chromium 153 mit aktivem WebGL: Ein einziger
`scattergl`-Chart rendert einwandfrei; erst wenn die beiden Extensions
fehlen, erscheint exakt die gemeldete Meldung.

Zwei weitere Befunde im selben Chart-Code, beide still:

3. **Chart-Titel ging verloren.** Plotly 3.0 entfernte `title` als String;
   `title: symbol` wird ohne Fehler verworfen, der Chart blieb unbeschriftet.
   Erst `title: {text: …}` ist gültig.
4. **Kein Verhalten bei spätem Kontextverlust.** Bei erreichter
   Kontext-Obergrenze oder GPU-Prozess-Neustart kann ein zunächst
   erfolgreicher Chart nachträglich auf die Meldung zurückfallen; das
   Dashboard hat das nicht bemerkt und beim nächsten Poll denselben Zustand
   neu erzeugt.

## Fix und Umfang

`trading/templates/trading/dashboard.html`:

- **Vorabprüfung wie regl:** `detectWebglTraces()` fragt genau die beiden
  Extensions in genau der Schreibweise ab, die Plotly verwendet. Der
  Trace-Typ kommt aus `traceType()` — `scattergl` bei nutzbarem WebGL, sonst
  `scatter` (SVG). Datenstruktur und Layout sind identisch, es ändert sich
  nur der Renderer.
- **Laufzeit-Rückfall:** `downgradeChartIfWebglFailed()` erkennt den von
  Plotly eingefügten `.no-webgl`-Hinweis nach jedem Render, entfernt ihn,
  schaltet einmalig dauerhaft auf SVG um und zeichnet neu. Damit ist auch der
  Fall abgedeckt, in dem die Vorabprüfung noch erfolgreich war.
- **Sichtbarer Hinweis:** `#webgl-fallback-hint` erklärt die vereinfachte
  Darstellung und nennt die realistischen Hebel (Hardware-Beschleunigung,
  Grafik-/Fingerprint-Schutz) statt einer Fehlermeldung ohne Handlungswert.
- **Titel korrigiert:** `title: {text: symbol}` (Plotly-3-API).

Bewusst nicht geändert: Der Equity-Chart bleibt ein SVG-`scatter` (kein
WebGL nötig), die Charts bleiben bei 5.000 Punkten gedeckelt
(`_MAX_API_ROWS`), und die Poll-Kadenz bleibt bei 10 s. Gemessen mit 5.000
Punkten und sechs Traces: SVG zeichnet den Erstaufbau in ~0,44 s, WebGL in
~1,4 s; bei Folge-Updates liegen beide bei ~0,12–0,26 s. Der SVG-Rückfall
ist damit auch bei der maximalen Punktzahl vertretbar.

## Regressionstests

`trading/tests/test_dashboard_charts.py`, 5 Tests, alle am Ausgangsstand rot:

- `title: {text: …}` vorhanden, `title: "<string>"` nicht mehr.
- Trace-Typ läuft über `traceType()`, kein hart verdrahtetes
  `type: "scattergl"` mehr in den Chart-Daten.
- Die Vorabprüfung fragt die beiden Extensions kleingeschrieben ab
  (gleich wie regl) — sonst erkennt das Dashboard WebGL als nutzbar und
  Plotly fällt danach doch aus.
- Laufzeit-Rückfall auf `.no-webgl` ist verdrahtet.
- Der Nutzerhinweis wird gerendert.

## Prüfgrenzen

- **Kein Browser-Test in CI.** Die Tests prüfen das ausgelieferte Markup und
  die Verdrahtung, nicht das Renderergebnis. Verifiziert wurde der Fix
  manuell in Chromium 153 (SwiftShader) in drei Szenarien: WebGL vorhanden →
  `scattergl` mit drei Canvas-Ebenen; Extensions fehlen → SVG ohne Meldung;
  Chart-Titel sichtbar.
- **Firefox nicht separat nachgestellt.** Der Firefox-Pfad entspricht
  Auslöser 2 (kein Kontext ohne GPU/SwiftShader); ein Firefox-Build mit
  fehlender Extension-Abfrage wurde nicht geprüft.
- **Keine Aussage zu WebGL2-only-Bundles.** Sobald Plotly selbst auf WebGL2
  umstellt, entfällt die Extension-Abhängigkeit; die Vorabprüfung ist dann
  zu überarbeiten, weil WebGL2 diese Extensions nicht mehr ausgibt.
- Limitierte Kontextzahl (typisch 8–16) bleibt ein Browser-Limit; der
  Laufzeit-Rückfall fängt es ab, verhindert es aber nicht.
