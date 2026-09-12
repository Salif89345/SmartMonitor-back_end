# OP-017 — Méthode de baseline backend

## Mesures produites

- latence p50/p95 de l'API locale `/api/v1/health` ;
- latence p50/p95 de `/api/v1/health/database` ;
- latence p50/p95 d'un aller-retour PostgreSQL `SELECT 1` ;
- délai observé ESP/MQTT vers DB à partir de `measured_at` et `received_at` ;
- nombre réel d'instructions SQL du chemin d'ingestion pour cache froid, cache chaud et persistance due ;
- estimation avant/après OP-003 au rythme actuel de 5 secondes et avec deux canaux ;
- CPU et mémoire du processus backend pendant la mesure.

## Sécurité de la mesure

- aucune publication MQTT artificielle ;
- aucune écriture dans PostgreSQL ;
- la mesure SQL contrôlée utilise une base SQLite temporaire en mémoire ;
- aucun endpoint, contrat, modèle ou traitement métier ajouté ;
- les seules écritures sont les rapports dans `reports/op017/`.

## Interprétation

La mention `PASS` signifie que tous les indicateurs prévus ont pu être collectés et que le budget SQL attendu n'a pas dérivé. Elle ne signifie pas encore que les performances satisfont une exigence de certification : les seuils produit doivent être définis séparément, puis vérifiés sur l'hébergement cible et sous charge représentative.
