# CI-Definitionen

Diese Dateien liegen bewusst **außerhalb** von `.github/`, weil die hier eingebundene
GitHub-App keine `workflows`-Push-Berechtigung besitzt (vgl. CHANGELOG-Historie
2.4.16/2.4.19: "Der GitHub-App fehlt die Berechtigung für Workflow-Änderungen").

**Einmalige Übernahme durch den Maintainer:**

```bash
mkdir -p .github/workflows
cp ci/quality.yml .github/workflows/quality.yml
git add .github/workflows && git commit -m "ci: quality-workflow aktivieren" && git push origin tbot.local
```

Danach läuft der Workflow bei jedem Push/PR; `ci/quality.yml` bleibt als editierbare
Referenz im Repo. Bitte beide Kopien bei Änderungen an einem der beiden Orte
nachziehen, bis die App-Rechte erweitert werden.
