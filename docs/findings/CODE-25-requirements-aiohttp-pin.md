# CODE-25 – `aiohttp` genutzt, aber nicht deklariert: direkter Versionspin

- **Finding:** `K4` (Umfassender Code-Review 2026-09-09)
- **Status:** **Fixed**
- **Release:** **2.5.0** · **Datum:** 2026-09-09
- **Einstufung:** MEDIUM – Build-/Deploy-Reproduzierbarkeit (kein Laufzeitbug im Bestand)
- **Geprüfter Ausgangsstand:** 2.4.20 / `727d3ee534b9f304f59a49b60bdaf7a1e5943ee7`
- **Fix-Commit:** `69a32a5` – `build(deps): declare aiohttp explicitly in requirements.txt`

## Befund und Root Cause

`trading/market_data.py` importiert `aiohttp` direkt, die
`requirements.txt` führte es aber nicht auf – es war nur als Transitive
Abhängigkeit von `ccxt` installiert. Damit hing der gesamte HTTP-Marktdaten-
Pfad von der Nebenentscheidung einer Third-Party-Bibliothek ab: Ein
ccxt-Release ohne oder mit anderer aiohttp-Range hätte den Bot-Stack zur
Laufzeit mit `ModuleNotFoundError` bzw. API-Bruch laufen lassen, ohne dass
ein eigenes Dependency-Update die Ursache war. Die CI nutzte überdies die
lokal vorhandene Version und konnte den Bruch nicht zeigen.

## Fix und Umfang

`requirements.txt` deklariert jetzt `aiohttp==3.14.3` (die im Referenz-venv
validierte Version) direkt hinter `ccxt`; Kommentarloser Pin nach dem
bestehenden Muster aller Runtime-Pins. `pip check` bestätigt die
Konsistenz mit der ccxt-Abhängigkeit. Bewusst **nicht** geändert: ccxt selbst
(Version unverändert) – der Pin dokumentiert nur die tatsächlich genutzte
Laufzeitversion und macht sie unabhängig vom ccxt-Lockfile.

## Regressionstests

Kein Unit-Test sinnvoll (Packungsdeklaration); das Quality-Gate `pip check`
läuft in `.github/workflows/quality.yml` bei jedem Push. Rot→Grün-Nachweis:
eine `pip uninstall aiohttp`-Simulation im venv lässt `import trading.market_data`
fehlschlagen, obwohl die bestehenden Pins „vollständig" wirkten.

## Prüfgrenzen

Der Pin ist eine Version, kein Hash-Lockfile (kein `pip-compile`/`uv lock` im
Projekt); Transitive Abhängigkeiten anderer Pakete bleiben weiterhin
unkontrolliert gelöst. Eine vollständige Lockfile-Strategie ist als
Backlog-Punkt notiert, nicht Teil dieses Fixes.
