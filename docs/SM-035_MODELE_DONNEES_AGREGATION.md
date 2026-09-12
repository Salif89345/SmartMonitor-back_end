# SM-035 — Modèle de données et d'agrégation V1

## Statut et portée

Ce document fixe le contrat V1 entre le firmware, MQTT, le backend et les
futures interfaces d'historique. Il formalise le comportement existant et
interdit l'historisation de mesures anciennes ou déclarées invalides.

SM-035 ne modifie ni l'acquisition matérielle, ni la cadence MQTT, ni le
schéma de base de données, ni l'OTA.

## Horodatage et cadences

- DHT22 : acquisition toutes les 2 secondes.
- Canaux électriques PZEM : acquisition toutes les 1 seconde.
- Télémétrie MQTT `state` : publication toutes les 5 secondes.
- Historisation électrique brute : au maximum un échantillon par canal et
  par minute.
- Tous les instants persistés sont des dates UTC avec fuseau.
- Les journées civiles sont calculées avec le fuseau `Europe/Paris`, y
  compris lors des changements d'heure.

La période d'une minute est un sous-échantillonnage d'historique : elle ne
remplace pas les valeurs temps réel utilisées par l'application.

## Indicateurs

| Indicateur | Unité | Historique V1 | Agrégation |
|---|---:|---:|---|
| `temperature_c` | °C | Non | Valeur temps réel uniquement |
| `humidity_pct` | % HR | Non | Valeur temps réel uniquement |
| `voltage_v` | V | Oui | min, moyenne, max |
| `current_a` | A | Oui | min, moyenne, max |
| `power_w` | W | Oui | min, moyenne, max |
| `energy_kwh` | kWh | Oui | première, dernière, différence |
| `frequency_hz` | Hz | Oui | min, moyenne, max |
| `power_factor` | sans unité | Oui | min, moyenne, max |

Le compteur `energy_kwh` n'est jamais moyenné. Sa consommation correspond à
une différence entre deux valeurs valides et monotones.

## Validité et données manquantes

Une mesure électrique n'est historisée que si :

1. le bloc du canal est marqué `fresh` ;
2. `voltage_v`, `current_a` et `power_w` sont présents, finis et marqués
   `quality=ok` ;
3. son horodatage est valide, synchronisé par NTP et postérieur ou égal au
   dernier échantillon connu.

Les grandeurs facultatives `energy_kwh`, `frequency_hz` et `power_factor`
deviennent `null` lorsque leur qualité n'est pas `ok`. PostgreSQL ignore ces
valeurs nulles dans les calculs statistiques.

Une valeur `stale`, `unavailable`, `incoherent`, infinie ou `NaN` n'est jamais
transformée en zéro. Aucun trou temporel n'est interpolé. Une tranche vide
reste absente et la première consommation après un trou reste indéterminée.

## Agrégations et rétention

- Historique détaillé : 90 jours maximum.
- Résumés journaliers : 365 jours maximum.
- Résolutions détaillées autorisées : 1, 2, 5, 10, 15, 30 minutes, puis
  1, 2, 3, 6, 12 et 24 heures.
- L'API vise 60 à 120 points, avec 90 points par défaut.
- Les moyennes de plusieurs résumés journaliers sont pondérées par le nombre
  d'échantillons.
- Une baisse du compteur d'énergie est considérée comme anormale et n'engendre
  jamais une consommation négative.

## Limites V1 assumées

- Température et humidité ne sont pas encore historisées ; elles restent
  disponibles en temps réel. Leur historisation nécessitera un stockage et
  une politique de rétention dédiés.
- La qualité et la fraîcheur ne sont pas stockées comme colonnes dans
  `power_measurements` : elles servent de filtre avant insertion.
- Les résumés journaliers décrivent les canaux électriques séparément ; une
  somme multi-canaux devra être calculée explicitement par le consommateur.

## Sources exécutables du contrat

- `app/measurement_contract.py` : unités, cadences, agrégations et règles de
  valeur manquante.
- `app/mqtt_client.py` : filtrage avant persistance.
- `app/history_service.py` : agrégations et résolution de restitution.
- `app/power_daily_summary.py` : résumés journaliers et énergie monotone.
- `app/data_retention.py` : fenêtres de conservation.
- `tests/test_sm035_measurement_contract.py` : contrôle automatique du contrat.
