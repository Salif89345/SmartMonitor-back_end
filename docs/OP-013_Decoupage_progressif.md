# OP-013 — Découpage progressif des gros fichiers

## Lot réalisé

La gestion du cycle de vie des preuves d’association a été extraite de `app/mqtt_client.py` vers `app/claim_proof_store.py`.

Le nouveau composant prend en charge :

- l’enregistrement temporaire d’une preuve ;
- sa durée de vie de 120 secondes ;
- la réservation exclusive pendant une association ;
- la libération après échec ;
- la consommation après succès ;
- la protection contre la republication et le rejeu ;
- la synchronisation entre threads.

`MqttManager` conserve la validation du topic et du payload MQTT ainsi que ses méthodes publiques. Les routes et les appelants existants ne changent donc pas.

## Décision

Le découpage se fait par responsabilité et par petits lots testables. Aucune réécriture globale de `mqtt_client.py`, `MqttManager.cpp` ou `CommandEngine.cpp` n’est engagée.

Cette approche limite le risque fonctionnel et rend chaque extraction réversible et vérifiable.

## Comportement conservé

- même topic de preuve d’association ;
- mêmes validations d’UID, de digest et d’identité MQTT ;
- même calcul SHA-256 du nonce ;
- même TTL de 120 secondes ;
- mêmes règles de réservation, libération, consommation et anti-rejeu ;
- aucune modification de schéma MQTT, API ou base de données.

## Vérifications

- tests unitaires dédiés au nouveau composant ;
- conservation des tests d’intégration existants de `MqttManager` ;
- exécution de toute la suite backend ;
- compilation Python du dossier `app` ;
- mesure du nombre de lignes retirées de `mqtt_client.py`.

## Limites et suites éventuelles

Les responsabilités suivantes restent encore dans `MqttManager` et pourront être extraites uniquement lorsqu’un futur changement les touche :

- corrélation des commandes et réponses ;
- état online/offline des appareils ;
- routage et validation des familles de messages MQTT ;
- orchestration de la queue d’ingestion.

Les gros fichiers C++ du firmware ne sont pas modifiés dans ce lot. Leur découpage suivra la même règle : une responsabilité, des tests avant/après et aucun changement fonctionnel involontaire.
