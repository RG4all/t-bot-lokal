# Peer-Review-Patches (`patches/`)

Eingang für Review-Vorschläge, Sicherheitsanmerkungen und fremde Patches, die noch
nicht im PR-System laufen. Patches werden **angeboten**, nicht direkt auf `tbot.local`
gewedet: angenommen wird ausschließlich über PR mit Maintainer-Freigabe.

## Ordner

- `inbox/` – unverarbeitete Vorschläge (`git format-patch`-Serien und/oder `.md`-Review)
- `accepted/` –quittierte Serien nach Merge (beim nächsten Jahres-Audit löschen)
- `done/` – abgelehnte oder erledigte Diskussionen mit Begründungsdatei

## Ablauf

1. **Angebot abgeben:** Serie + Review-Kommentar in `patches/inbox/` ablegen
   (Dateinamen: `<JJJJ-MM-TT>-<kurz-slug>.patch|.md`). Review-Kopf folgt
   [`TEMPLATE-REVIEW.md`](TEMPLATE-REVIEW.md). Alternativ direkt als PR.
2. **Prüfen:**
   ```bash
   git checkout -b review/<slug>
   git am --3way patches/inbox/<date>-<slug>*.patch
   bash tests/run_tests.sh
   AUTOSTART_BOTS=False DEBUG=True RENDER=False python manage.py test --noinput
   python3 scripts/check_docs.py
   ```
3. **Entscheidung dokumentieren:** Ergebnis als Kommentar in die Review-`.md`
   (übernommen/teilweise abgelehnt mit Begründung).
4. **Annehmen:** PR nach `tbot.local`; gemergte Patch-Serie nach `accepted/` verschieben,
   betroffenes Findings-Dokument auf `status: fixed` setzen, CHANGELOG-Eintrag ergaenzen.
5. **Ablehnen:** Review-Datei (mit Begründung) nach `done/`; Patch-Dateien loeschen.

## Pflichten bei Doku-/Findings-Aenderungen

- Neues Findings-Dokument nach [`../docs/findings/TEMPLATE.md`](../docs/findings/TEMPLATE.md)
  anlegen bzw. Status aktualisieren; Statuspflege nur dort und im CHANGELOG.
- Vor Merge muss `python3 scripts/check_docs.py` fehlerfrei durchlaufen (das gilt auch
  fuer reine Dokumentations-Patches).
