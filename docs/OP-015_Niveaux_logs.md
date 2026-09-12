# OP-015 — Niveaux de logs DEV / PROD

## Objectif

Réduire les traces répétitives en production sans perdre les informations nécessaires au diagnostic, à la sécurité ou à l'exploitation.

## Décisions

- Firmware DEV : niveau minimum `DEBUG`.
- Firmware PROD : niveau minimum `INFO` et traces internes ESP-IDF limitées aux erreurs.
- Backend DEV : niveau par défaut `DEBUG`.
- Backend PROD : niveau par défaut `INFO`.
- Le backend accepte une surcharge explicite avec `LOG_LEVEL` parmi `DEBUG`, `INFO`, `WARNING`, `ERROR` et `CRITICAL`.
- Les acquisitions réussies, le heartbeat et les échanges MQTT courants sont classés `DEBUG`.
- Les échecs de connexion, refus d'abonnement, saturation de file et exceptions du worker restent visibles en production.

## Compromis

La migration est volontairement ciblée sur les flux fréquents et critiques. Les anciens messages ponctuels restent compatibles et pourront être migrés progressivement vers `LogManager` ou `logging` sans modifier le comportement métier.

## Effets fonctionnels

Aucun changement sur les mesures, les cadences d'acquisition, MQTT, l'API, la base de données, l'OLED, la matrice, le BLE ou l'OTA.

## Validation

- contrôle statique des profils et des traces filtrées ;
- tests unitaires des valeurs par défaut DEV/PROD et du rejet des niveaux invalides ;
- suite backend complète ;
- compilation Python ;
- builds PlatformIO explicites des profils DEV et PROD.
