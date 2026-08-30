---
title: "SmartMonitor - SM-071 - Documentation consolidée"
subtitle: "Comptes, sessions, association, appareils, commandes, sécurité, robustesse et validation verticale"
date: "22 août 2026"
lang: fr-FR
toc: true
toc-depth: 3
numbersections: true
documentclass: article
papersize: a4
geometry:
  - margin=2cm
fontsize: 10pt
---

# Objet du document

Ce document consolide le travail réalisé dans le cadre de **SM-071** pour SmartMonitor. Il sert de référence technique et de trace de décision pour l'application mobile, le backend FastAPI, PostgreSQL, le broker MQTT et le firmware ESP32.

Il documente :

- les décisions d'architecture ;
- les contrats entre APP, Backend, MQTT et Firmware ;
- les compromis retenus pour la V1 ;
- les protections de sécurité et de robustesse ;
- les tests automatisés et les tests réels ;
- les anomalies trouvées pendant la validation ;
- les corrections apportées ;
- les validations physiques volontairement reportées ;
- les améliorations recommandées pour la suite.

La roadmap reste la référence de planification. Le présent document est la référence de **conception, d'implémentation et de validation** pour SM-071.

## Périmètre

Le périmètre consolidé comprend :

- **SM-071-1** - Compte utilisateur et session ;
- **SM-071-2** - Association / provisioning d'un SmartMonitor ;
- **SM-071-3** - Liste et détail des appareils ;
- **SM-071-4** - Commandes, ACK/NACK, diagnostic, sécurité, robustesse et validation verticale.

Ne sont pas considérés comme terminés dans SM-071 :

- **SM-071A - Historique dans l'APP**, prévu plus tard ;
- le **broker distant et TLS de production**, qui est l'étape suivante de la roadmap ;
- les quelques tests physiques explicitement reportés dans ce document.

# Synthèse exécutive

SM-071 a fait passer SmartMonitor d'un ensemble de composants techniques à une chaîne applicative cohérente, authentifiée et exploitable par un utilisateur réel.

Architecture retenue :

```text
SmartMonitor APP
      |
      | HTTPS / API REST authentifiée
      v
FastAPI Backend
      |
      | MQTT
      v
MQTT Broker
      |
      | MQTT
      v
ESP32 SmartMonitor

FastAPI Backend <-> PostgreSQL
```

La décision structurante est la suivante :

> **L'APP ne communique jamais directement en MQTT avec le SmartMonitor.**

Le backend est l'autorité centrale pour :

- l'authentification ;
- les sessions ;
- les droits owner/member ;
- l'association des appareils ;
- la persistance PostgreSQL ;
- les commandes ;
- le rate limiting ;
- la corrélation des réponses MQTT ;
- les événements ;
- la traduction des erreurs vers l'APP.

Le firmware conserve la responsabilité du matériel, des mesures, du Wi-Fi, du MQTT, de la fenêtre physique de pairing et de l'exécution des commandes.

Le prototype historique de MQTT direct dans Flutter ne doit donc **pas être réintroduit**.

# Environnement principal de validation

Les validations réelles de fin de SM-071 ont été effectuées sur une chaîne locale représentative :

- APP Flutter sur **Huawei ELE-L29, Android 10 / API 29** ;
- backend FastAPI sur le PC de développement ;
- PostgreSQL local ;
- Mosquitto local, sans TLS, pour le développement ;
- ESP32 SmartMonitor réel connecté au Wi-Fi et au broker ;
- tests APP complémentaires disponibles sur émulateur Android.

Ce contexte explique pourquoi le **broker distant + TLS production** reste une étape distincte : SM-071 valide les contrats et les comportements applicatifs, pas encore l'infrastructure Internet de production.

# État consolidé de SM-071

| Sous-point | Objet | État | Réserve / remarque |
|---|---|---|---|
| SM-071-1 | Compte et session | VALIDÉ | Session, refresh, logout, stockage sécurisé |
| SM-071-2 | Association / provisioning | VALIDÉ RÉEL E2E | Pairing physique + BLE + Wi-Fi + preuve MQTT + claim backend |
| SM-071-3 | Liste / détail appareils | VALIDÉ RÉEL | 0 / 1 / plusieurs appareils, owner/member, online/offline, mesures |
| SM-071-4A | Audit et contrat de commande | VALIDÉ | Architecture et contrat figés |
| SM-071-4B | Ping | VALIDÉ RÉEL | APP -> Backend -> MQTT -> ESP32 -> ACK -> APP |
| SM-071-4C | États de commande APP | VALIDÉ | pending / success / error, prévention des doubles commandes |
| SM-071-4D | Restitution ACK/NACK | VALIDÉ IMPLÉMENTATION | ACK réel ; NACK réel physique reporté |
| SM-071-4E | get_status | VALIDÉ RÉEL | Diagnostic technique retourné à l'APP |
| SM-071-4F-1 | Stabilité MQTT backend | VALIDÉ RÉEL | Singleton protégé par advisory lock PostgreSQL |
| SM-071-4F-2 | Robustesse commandes | REPORTÉ PARTIELLEMENT | Branches couvertes, certains scénarios physiques non forcés |
| SM-071-4F-3 | Commandes multiples | VALIDÉ RÉEL | Rate limit 30 commandes / 60 s, HTTP 429 |
| SM-071-4F-4 | Sécurité d'accès | VALIDÉ RÉEL | Member réel, UI bloquée, API 403, aucun publish MQTT |
| SM-071-4F-5 | Reprise après incident | VALIDÉ RÉEL | Backend, broker et ESP32 |
| SM-071-4F-6 | Cohérence erreurs APP | VALIDÉ AVEC RÉSERVES | 502/504 manuels non forcés ; couverture automatisée |
| SM-071-4F-7 | Validation robustesse réelle | VALIDÉ RÉEL | Parcours owner + member après corrections |
| SM-071-4G | Validation verticale réelle | VALIDÉ RÉEL | Inclut refresh auth et cooldown 429 final |

**Conclusion de statut :** le périmètre fonctionnel SM-071 est validé pour la V1. Les reports restants sont connus, acceptés et explicitement conservés : NACK réel, SM-071-4F-2 complet, injections manuelles 502/504.

# Décisions d'architecture

## APP -> Backend uniquement

L'APP Flutter ne pilote pas directement le SmartMonitor en MQTT.

Flux autorisé :

```text
APP -> HTTPS -> FastAPI -> MQTT -> ESP32
ESP32 -> MQTT -> FastAPI -> HTTPS -> APP
```

Cette séparation permet de :

- centraliser les droits ;
- ne pas exposer les credentials MQTT dans l'APP ;
- disposer d'un audit serveur ;
- contrôler la fréquence des commandes ;
- gérer plusieurs utilisateurs sur un même appareil ;
- préparer un broker distant sans changer le contrat APP.

## Séparation des identités appareil

Deux identités ont des rôles différents :

- `device_uid` : identité matérielle stable, de forme `SM-XXXXXXXXXXXX` ;
- `mqtt_device_id` : identité de routage MQTT.

Le `device_uid` ne doit pas devenir un simple alias du `mqtt_device_id`. Cette séparation permet de modifier le routage MQTT sans modifier l'identité physique de l'appareil.

## Modèle owner/member

L'accès à un appareil repose sur `DeviceMembership` avec deux rôles :

- `owner` ;
- `member`.

Les diagnostics et commandes sont réservés à l'owner.

Politique backend :

- utilisateur non associé : 404 ;
- member associé essayant une commande owner-only : 403 ;
- owner : commande autorisée si les préconditions device/MQTT sont remplies.

L'implémentation V1 limite actuellement un appareil à **10 membres**.

## Backend MQTT singleton en V1

La V1 autorise une seule instance backend à être consommateur MQTT.

Un advisory lock PostgreSQL est acquis avant `mqtt_manager.start()` :

- une seule instance possède le rôle MQTT ;
- une seconde instance échoue au démarrage plutôt que de provoquer une collision de `client_id` ;
- le verrou est conservé par une connexion PostgreSQL dédiée pendant le lifespan ;
- arrêt normal : `mqtt_manager.stop()`, unlock, fermeture de la connexion ;
- crash : PostgreSQL libère automatiquement le verrou avec la connexion.

C'est un compromis V1 volontaire. Une future architecture multi-instance devra revoir la consommation MQTT et le rate limiter.

# SM-071-1 - Compte utilisateur et session

## Parcours fonctionnel

Le parcours d'authentification comprend :

1. inscription ;
2. vérification de l'email par code à 6 chiffres ;
3. renvoi du code ;
4. connexion ;
5. access token ;
6. refresh token ;
7. restauration automatique de session ;
8. refresh automatique si l'access token expire ;
9. logout ;
10. lecture de l'utilisateur courant.

Routes principales :

```text
POST /api/v1/auth/register
POST /api/v1/auth/verify-email
POST /api/v1/auth/resend-verification
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
```

## Stockage et rotation des tokens

Dans l'APP, les tokens sont stockés avec `flutter_secure_storage` sous une clé de session versionnée.

Le refresh token brut n'est pas conservé tel quel dans PostgreSQL : le backend stocke son hash SHA-256.

Le refresh est rotationnel : l'ancien refresh token est révoqué lorsqu'un nouveau est émis.

Le `SessionManager` protège aussi les refresh concurrents avec un unique `_refreshInFlight`, afin que plusieurs requêtes rencontrant simultanément une expiration ne déclenchent pas plusieurs rotations concurrentes.

## Logout

Le logout local est prioritaire. Même si le backend est indisponible, l'APP supprime les tokens locaux.

## Validation réelle du refresh pendant SM-071-4G

La validation verticale a montré :

```text
GET /api/v1/devices/1 -> 401 Unauthorized
POST /api/v1/auth/refresh -> 200 OK
GET /api/v1/devices/1 -> 200 OK
```

L'utilisateur n'a pas été déconnecté et l'APP a poursuivi son fonctionnement.

## Couverture de tests

Les sources SM-071 couvrent notamment :

- les 7 contrats auth ;
- la conservation du détail d'erreur backend ;
- `Retry-After` sur 429 ;
- login valide ;
- login d'un utilisateur non vérifié ;
- inscription puis vérification ;
- renvoi de vérification avec message anti-énumération ;
- persistance et restauration de session ;
- refresh ;
- fusion des refresh concurrents ;
- invalidation locale si le refresh est invalide ;
- logout local même si le backend est inaccessible.

# Infrastructure email de développement

Pour la validation réelle d'un second compte, le domaine de test Resend ne permettait pas l'envoi vers le destinataire externe utilisé pour le test et retournait HTTP 403.

Décision : mettre en place un domaine de développement dédié :

```text
smartmonitor-lab.com
```

Le domaine a été configuré dans le DNS puis vérifié dans Resend. L'envoi de vrais codes de vérification a ensuite fonctionné.

Ce domaine est un **outil de développement**, pas une décision de branding. Le domaine commercial de production pourra être différent.

# SM-071-2 - Association / provisioning

## Objectif

Associer un SmartMonitor physique au compte connecté sans permettre de prendre possession de l'appareil uniquement en connaissant son identifiant.

## Chaîne retenue

```text
Utilisateur devant le SmartMonitor
        |
        | ouverture de la fenêtre physique de pairing
        v
APP Flutter -- BLE --> ESP32
        |               |
        | Wi-Fi         | connexion Wi-Fi
        |               v
        |          MQTT claim-proof
        |               |
        v               v
        +------> FastAPI Backend
                   |
                   | POST /devices/claim
                   v
             Device + owner
```

## Preuve d'association

Le protocole utilise :

- `device_uid` canonique ;
- nonce brut de 32 caractères hexadécimaux majuscules ;
- hash SHA-256 de la preuve publié par le firmware via MQTT ;
- réservation puis commit de la preuve dans le backend ;
- preuve à usage unique.

Topic de provisioning :

```text
smartmonitor/provisioning/{device_uid}/claim-proof
```

Le backend associe ensuite l'identité matérielle `device_uid` à l'identité de routage `mqtt_device_id`.

## Propriétés de sécurité validées

Les tests backend de claim-proof vérifient que :

- un mauvais nonce ne consomme pas la bonne preuve ;
- une réservation bloque un claim concurrent ;
- une réservation peut être libérée après un échec ;
- une preuve commitée est à usage unique ;
- une republication identique ne rouvre pas une preuve déjà consommée ;
- une nouvelle fenêtre physique peut publier une nouvelle preuve valide.

## Revue consolidée APP + Firmware

La revue SM-071-2 a conduit aux corrections suivantes :

1. contrat Apply aligné : APP `0x01`, firmware `0x01`, compatibilité texte `apply` conservée ;
2. saisie Wi-Fi avant consommation de la fenêtre de pairing ;
3. arrêt du scan BLE dès détection pour ne pas gaspiller les 60 s ;
4. vérification de la fenêtre physique avant lecture du nonce ;
5. nouvelle vérification juste avant remise des credentials ;
6. suppression de tout log APP du nonce brut ;
7. lecture bornée du nonce et message d'expiration explicite ;
8. publication MQTT de la preuve uniquement après succès Wi-Fi ;
9. conservation du hash si le test dépasse la fenêtre physique ;
10. suppression du nonce brut à la fermeture ;
11. écritures BLE identity/status/nonce avec longueur explicite ;
12. nettoyage des valeurs GATT SSID/password/apply après traitement ;
13. SSID non `trim()` : les espaces réels sont conservés ;
14. claim avec token courant ;
15. un seul refresh de session en cas de 401 pendant le claim ;
16. retry 409 limité aux erreurs de preuve, pas à tous les conflits ;
17. broker, port et credentials MQTT firmware issus de la `Configuration` active ;
18. compatibilité Android ancienne avec `ACCESS_COARSE_LOCATION` jusqu'à API 28.

## Effets backend d'un claim valide

Lors d'un claim valide :

- le `Device` est retrouvé ou créé ;
- `device_uid` reste l'identité matérielle ;
- `mqtt_device_id` devient l'identité de routage ;
- l'utilisateur devient owner ;
- un conflit d'owner retourne 409 ;
- le canal `power_1` est créé s'il n'existe pas.

## Validation

SM-071-2 a été validé en **E2E réel** avec APP, BLE, firmware, Wi-Fi, MQTT, backend et association au compte.

# SM-071-3 - Liste et détail des appareils

## Parcours APP

Trois cas sont couverts :

### Aucun appareil

L'APP affiche le point d'entrée permettant d'associer un SmartMonitor.

### Un seul appareil

L'APP ouvre directement le dashboard.

### Plusieurs appareils

L'APP affiche une liste de sélection, puis ouvre l'appareil choisi.

## Informations affichées

Le dashboard présente notamment :

- `device_uid` ;
- disponibilité : En ligne, Hors ligne ou État inconnu ;
- rôle : Propriétaire ou Membre ;
- mesures électriques ;
- température et humidité si disponibles ;
- fraîcheur et âge des données ;
- canaux ;
- diagnostic.

Pour la télémétrie :

- donnée fraîche : `Mesures en direct` ;
- donnée ancienne/persistée : `Dernières mesures connues` ;
- âge de la mesure affiché explicitement.

## Politique offline

Quand le SmartMonitor est offline :

- les dernières mesures restent visibles ;
- elles ne sont plus présentées comme temps réel ;
- leur âge reste visible ;
- Ping et Diagnostic sont désactivés ;
- l'APP explique l'indisponibilité du diagnostic.

L'APP utilise actuellement un polling REST toutes les **3 secondes**. Ce choix est simple et robuste pour la V1.

# SM-071-4 - Contrat de commande

## Requête MQTT Backend -> Firmware

Le backend génère un UUID de corrélation puis publie :

```json
{
  "schema_version": 1,
  "request_id": "UUID",
  "command": "ping ou get_status",
  "parameters": {}
}
```

Topic :

```text
smartmonitor/{mqtt_device_id}/command
```

Implémentation actuelle : QoS 0, `retain=false`.

## Réponse Firmware -> Backend

Topic :

```text
smartmonitor/{mqtt_device_id}/response
```

Contrat de réponse :

```json
{
  "device_id": 1,
  "request_id": "UUID",
  "result": "ack ou nack",
  "error_code": null,
  "message": "texte",
  "data": {}
}
```

Le backend vérifie :

- objet JSON valide ;
- `request_id` identique à la requête ;
- topic correspondant au device attendu ;
- `result` égal à `ack` ou `nack` ;
- `message` de type chaîne ;
- réponse reçue avant le timeout.

Le timeout actuel est de **5 secondes**.

## Corrélation et concurrence

Le backend conserve les commandes en attente dans une structure indexée par `request_id`, protégée par lock.

Chaque entrée contient l'événement d'attente, le topic de réponse attendu et la réponse reçue. L'entrée est supprimée dans un `finally`, y compris en cas d'erreur.

## Événements

Une réponse valide génère :

- `command_ack` ;
- ou `command_nack`.

L'événement persiste au minimum `request_id` et `command`, ainsi que `error_code` lorsqu'il existe.

# SM-071-4B - Ping

Route :

```text
POST /api/v1/devices/{device_id}/commands/ping
```

La chaîne réelle a été validée :

```text
APP
 -> Backend authentifié
 -> MQTT command
 -> ESP32
 -> MQTT response
 -> command_ack
 -> HTTP 200
 -> APP affiche pong
```

Trace représentative :

```text
[MQTT] Command published: smartmonitor/.../command | request_id: ...
[MQTT] Message received: smartmonitor/.../response
[EVENT] Device event stored: type: command_ack
POST /api/v1/devices/1/commands/ping -> 200 OK
```

# SM-071-4C - États de commande dans l'APP

L'APP gère les états :

- `idle` ;
- `pending` ;
- `success` ;
- `error`.

Pendant une commande en cours :

- le bouton affiche un indicateur ;
- une seconde commande est bloquée ;
- la restitution finale affiche ACK, NACK ou une erreur traduite.

# SM-071-4D - ACK / NACK

L'APP restitue :

- ACK ou NACK ;
- `message` ;
- `request_id` ;
- `error_code` s'il existe ;
- `data` si présente.

Un NACK est une réponse de protocole valide. Il peut donc être retourné avec HTTP 200 et `result = nack`.

La couverture automatisée du NACK est validée.

**Report explicite :** un NACK physique réel provoqué volontairement sur l'ESP32 reste à effectuer.

# SM-071-4E - get_status

Route :

```text
POST /api/v1/devices/{device_id}/commands/get_status
```

Le diagnostic peut remonter notamment :

- uptime ;
- version firmware ;
- date de build ;
- commit Git si disponible ;
- révision hardware ;
- version de schéma.

Le parcours réel APP -> Backend -> MQTT -> ESP32 -> APP a été validé.

# SM-071-4F - Sécurité et robustesse

## 4F-1 - Stabilité MQTT backend

Risque traité : deux instances backend utilisant le même consommateur MQTT.

Correction : advisory lock PostgreSQL global avant le démarrage de MQTT.

Résultat : une seule instance backend peut devenir consommateur MQTT en V1.

## 4F-2 - Robustesse des commandes - report partiel

Le backend couvre les branches :

- MQTT backend indisponible -> 503 ;
- device indisponible -> 503 ;
- timeout device -> 504 ;
- réponse device invalide -> 502 ;
- configuration/état incompatible -> 409.

Ces chemins sont couverts par les tests automatisés et plusieurs incidents réels. Le point reste néanmoins **reporté partiellement** car tous les défauts physiques n'ont pas été reproduits individuellement de façon contrôlée.

Décision : ne pas modifier artificiellement le firmware de production uniquement pour forcer une erreur difficile à reproduire.

## 4F-3 - Commandes multiples et rate limiting

Politique actuelle :

```text
30 commandes / 60 secondes / utilisateur
```

En cas de dépassement :

```text
HTTP 429 Too Many Requests
Retry-After: <secondes>
```

Test réel par rafale de Ping :

- 429 effectivement retourné ;
- aucune publication MQTT supplémentaire pour les requêtes refusées ;
- reprise normale après expiration de la fenêtre.

Compromis V1 : le rate limiter est **en mémoire**. Il est cohérent avec le backend singleton. Une future architecture multi-instance devra utiliser un stockage partagé, par exemple Redis.

## 4F-4 - Sécurité d'accès owner/member

Un second compte réel a été créé depuis l'APP et ajouté comme `member`.

Validation :

- le SmartMonitor est visible ;
- le rôle Membre est affiché ;
- Ping et Diagnostic sont désactivés ;
- un appel API direct contournant l'UI retourne 403 ;
- aucun publish MQTT n'est effectué après le 403.

La sécurité est donc imposée par le backend et non uniquement par l'interface.

## 4F-5 - Reprise après incident

### Redémarrage backend

La reprise backend a été testée et la chaîne est redevenue opérationnelle.

### Broker Mosquitto OFF -> ON

À l'arrêt :

- device offline ;
- événement `device_offline` ;
- backend MQTT déconnecté ;
- statuts devices nettoyés ;
- commandes APP indisponibles.

Au redémarrage :

- reconnexion automatique ;
- subscriptions restaurées ;
- device online ;
- reprise télémétrie ;
- Ping post-incident -> `pong`.

### ESP32 débranché -> rebranché

À la déconnexion :

- événement `device_offline` ;
- APP Offline ;
- dernières mesures connues conservées ;
- commandes désactivées.

Au retour :

- événement `device_online` ;
- télémétrie reprise ;
- Ping et diagnostic de nouveau fonctionnels.

Le délai d'affichage Offline observé a été d'environ **25 secondes**. Ce délai a été explicitement accepté pour la V1.

### Garde-fous de qualité des mesures

Après redémarrage de l'ESP32, les traces ont aussi montré :

```text
State skipped: NTP not synchronized
State skipped: energy manager not OK | status: FAILED
```

Les états transitoires non fiables ne sont donc pas persistés comme mesures valides.

## 4F-6 - Cohérence des erreurs côté APP

L'objectif est que l'utilisateur comprenne l'erreur, pas seulement que le backend retourne le bon code HTTP.

| HTTP | Situation | Message APP |
|---|---|---|
| 403 | droits insuffisants | `Commande refusée : réservée au propriétaire.` |
| 404 | appareil absent/non associé | `SmartMonitor introuvable ou non associé à ce compte.` |
| 409 | état/configuration incompatible | `Commande impossible dans l'état actuel du SmartMonitor.` |
| 429 | trop de commandes | `Trop de commandes envoyées. Réessaie dans X s.` |
| 502 | réponse device invalide | `Commande échouée : réponse invalide du SmartMonitor.` |
| 503 | device offline | `Commande impossible : SmartMonitor hors ligne.` |
| 503 | MQTT backend indisponible | `Commande impossible : service MQTT indisponible.` |
| 504 | timeout | `Commande échouée : délai de réponse dépassé.` |

### Correction des anciens messages après changement online/offline

Anomalie : un ancien `ACK - pong` ou une ancienne erreur pouvait rester visible après une transition d'availability.

Correction : à chaque changement de disponibilité, reset des états et messages Ping/get_status.

Validation réelle : le précédent `pong` disparaît lorsque l'appareil passe Offline.

### Niveau de validation par code

- 403 : réel avec member + API directe ;
- 404 : cas réel sans appareil + widget test de commande ;
- 409 : widget test ;
- 429 : réel ;
- 502 : backend + widget test, pas de corruption MQTT forcée ;
- 503 : comportement réel via incidents ;
- 504 : backend + widget test, pas de retard artificiel forcé.

### Pourquoi 502/504 n'ont pas été forcés manuellement

Forcer 502/504 aurait demandé une instrumentation temporaire :

- fabriquer une réponse MQTT invalide ;
- ou retarder artificiellement une réponse au-delà de 5 secondes.

Décision : ne pas introduire de code de test risqué dans le firmware courant pour un scénario déjà couvert automatiquement.

## Tests APP finaux de 4F-6

Le fichier `devices_screen_test.dart` a atteint **9 tests passés** :

1. zéro appareil -> entrée association ;
2. un appareil -> dashboard direct ;
3. plusieurs appareils -> liste de sélection ;
4. offline -> diagnostics désactivés + explication ;
5. member -> diagnostic owner interdit ;
6. 504 -> message timeout ;
7. 502 -> message réponse invalide ;
8. 404 -> message appareil introuvable/non associé ;
9. 409 -> message état incompatible.

La suite `devices_api_test.dart` a également validé Ping, NACK, get_status et la propagation des erreurs HTTP.

# SM-071-4F-7 - Validation de robustesse réelle

Après les corrections, un parcours de non-régression réel a été effectué.

## Owner

Validé :

- SmartMonitor En ligne ;
- mesures temps réel ;
- Ping -> pong ;
- diagnostic -> OK ;
- aucun message résiduel.

## Member

Validé :

- connexion member ;
- SmartMonitor visible ;
- rôle Membre visible ;
- Ping/Diagnostic bloqués ;
- aucun message incohérent.

Résultat : **SM-071-4F-7 validé**.

# SM-071-4G - Validation verticale réelle

SM-071-4G a vérifié que les protections ajoutées séparément fonctionnent ensemble.

## Parcours nominal

Validé :

- SmartMonitor En ligne ;
- mesures temps réel ;
- Ping -> pong ;
- get_status -> valeurs techniques ;
- Uvicorn -> HTTP 200 ;
- MQTT -> command publish puis response ;
- événement -> `command_ack` ;
- APP -> aucun message résiduel.

## Refresh de session observé pendant le parcours

```text
GET device -> 401
POST auth/refresh -> 200
GET device -> 200
```

L'APP a continué sans intervention utilisateur.

## Offline / online observé pendant le parcours

Les événements `device_offline` et `device_online` ont été observés, puis la télémétrie et les commandes ont repris.

## Défaut UX 429 trouvé pendant 4G

Après expiration du rate-limit, le backend acceptait de nouveau les commandes mais l'APP conservait le message :

```text
Trop de commandes envoyées. Réessaie dans un instant.
```

L'information affichée devenait donc fausse.

## Correction finale 429

Deux niveaux ont été corrigés.

### `devices_api.dart`

Lecture du header HTTP `Retry-After` et transmission dans `ApiException.retryAfterSeconds`.

### `devices_screen.dart`

Ajout d'un cooldown :

- heure de fin calculée ;
- timer chaque seconde ;
- message `Réessaie dans X s` ;
- Ping et get_status désactivés pendant le délai ;
- suppression automatique du message à zéro ;
- réactivation automatique des boutons ;
- nettoyage du cooldown lors d'un changement d'availability.

## Validation finale réelle du cooldown

Résultat observé :

- `Réessaie dans X s` apparaît ;
- compte à rebours initial de **42 s** dans le test final ;
- décrément chaque seconde ;
- Ping et Diagnostic désactivés ;
- à zéro, message supprimé automatiquement ;
- boutons de nouveau actifs sans nouvelle commande.

Analyse statique finale :

```text
flutter analyze
No issues found!
```

Résultat : **SM-071-4G validé**.

# Matrice de validation

| Fonction / risque | Automatique | Réel | État |
|---|---|---|---|
| Register / verify / login | Oui | Oui via comptes réels | Validé |
| Refresh session | Oui | Oui, 401 -> refresh -> 200 | Validé |
| Logout local | Oui | Oui | Validé |
| Provisioning BLE/Wi-Fi | Contrats/tests | Oui E2E | Validé |
| Claim proof | 4 tests backend dédiés | Oui E2E | Validé |
| 0 / 1 / plusieurs appareils | Widgets | Oui | Validé |
| Online/offline | Widgets partiels | Oui | Validé |
| Ping ACK | API tests | Oui | Validé |
| NACK parsing | Oui | Non forcé physiquement | Réel reporté |
| get_status | API tests | Oui | Validé |
| 403 member | UI + backend | Oui | Validé |
| 404 device | Widget | Oui pour compte sans device | Validé |
| 409 état invalide | Backend/widget | Non forcé | Accepté |
| 429 rate limit | Backend + APP | Oui | Validé |
| 429 cooldown | À ajouter en régression auto | Oui | Validé réel |
| 502 réponse invalide | Backend + widget | Non forcé | Accepté |
| 503 MQTT/device indisponible | Backend | Oui via incidents | Validé |
| 504 timeout | Backend + widget | Non forcé | Accepté |
| Broker OFF/ON | Non | Oui | Validé |
| ESP32 unplug/replug | Non | Oui | Validé |
| Backend restart | Non | Oui | Validé |
| Singleton MQTT backend | Protection technique | Oui | Validé |

# Compromis V1 explicitement acceptés

## Backend MQTT singleton

Acceptable tant que la production utilise une seule instance consommateur MQTT. À revoir avant scaling horizontal.

## Rate limiter en mémoire

Acceptable avec le backend singleton. À migrer vers un stockage partagé en multi-instance.

## Polling REST toutes les 3 secondes

Choix simple et robuste pour la V1. SSE/WebSocket pourront être étudiés si le trafic HTTP devient significatif.

## Détection offline autour de 25 secondes

Acceptée pour la V1. Pas d'optimisation sans besoin produit clair.

## Pas d'instrumentation firmware pour forcer 502/504

La couverture automatique est jugée suffisante dans SM-071. Un harness de test dédié pourra être ajouté ultérieurement.

## NACK réel reporté

Le contrat et l'UI sont validés, mais un NACK matériel volontaire reste à reproduire proprement.

## Domaine email de laboratoire

`smartmonitor-lab.com` reste une infrastructure de développement temporaire.

# Anomalies détectées et corrections

## Provisioning

- contrat Apply APP/Firmware désaligné -> aligné ;
- fenêtre pairing consommée trop tôt -> saisie Wi-Fi avant pairing ;
- scan BLE trop long -> arrêt dès détection ;
- cycle nonce/preuve insuffisamment strict -> reserve/commit et usage unique ;
- nonce brut loggé -> logs supprimés ;
- SSID trimé -> conservation exacte ;
- paramètres MQTT compilés -> lecture depuis `Configuration` active.

## Commandes

- contrat ACK/NACK trop pauvre -> `DeviceCommandResponse` structuré ;
- corrélation -> UUID `request_id` ;
- diagnostic -> `get_status` ;
- concurrence -> pending map + lock ;
- abus -> rate limit ;
- réponse sur mauvais topic -> contrôle du topic attendu ;
- payload invalide -> validation result/message/request_id.

## Sécurité

- blocage UI seul insuffisant -> contrôle owner dans le backend ;
- validation avec member réel -> 403 et aucun publish MQTT ;
- sender Resend de test insuffisant -> domaine DEV vérifié.

## UX erreurs

- ancien ACK/erreur après changement online/offline -> reset à transition ;
- 429 sans délai exploitable -> `Retry-After` ;
- message 429 persistant après reprise -> compte à rebours + effacement automatique.

# Points reportés à conserver dans la roadmap

## NACK réel - SM-071-4D

À tester avec un scénario firmware/device produisant un NACK légitime, sans code de test permanent.

## SM-071-4F-2 - Robustesse physique complète

Les branches sont implémentées, mais les scénarios physiques non reproduits doivent rester marqués reportés jusqu'à une validation sûre et reproductible.

## 502 manuel réel

Réponse MQTT volontairement corrompue non injectée.

## 504 manuel réel

Réponse volontairement retardée au-delà du timeout de 5 s non injectée.

Ces points sont **reportés et acceptés**, pas oubliés.

# Améliorations recommandées après SM-071

## Priorité proche

1. **Broker distant de production + TLS** : port sécurisé, certificats, authentification, ACL, supervision, reconnexion.
2. **Conserver le NACK réel et SM-071-4F-2 dans la roadmap** jusqu'à validation propre.
3. **Ajouter les tests de régression du cooldown 429** :
   - `devices_api_test.dart` : lecture de `Retry-After` ;
   - `devices_screen_test.dart` : countdown, blocage des deux boutons, effacement à zéro.
4. **Faire une revue Git complète** avant commit/push des derniers changements SM-071.

## Avant exposition production

5. **Rotation des secrets de développement** : JWT, MQTT, PostgreSQL, Resend et secret de vérification email.
6. **Domaine email officiel** et sender production.
7. **Rate limiting partagé** si plusieurs processus backend deviennent nécessaires.
8. **Stratégie MQTT multi-instance** avant scaling horizontal.
9. **Observabilité production** : métriques ACK/NACK, timeouts, reconnexions, 429, offline, erreurs de persistance.
10. **Réévaluer le polling APP** si le nombre de devices/utilisateurs augmente fortement.

## Non requis immédiatement

- réduire le délai offline de 25 s ;
- forcer artificiellement 502/504 dans le firmware courant ;
- remplacer le domaine DEV avant que l'infrastructure production soit prête.

# Fichiers et zones techniques concernés

## APP Flutter

Principaux composants :

```text
lib/api/api_exception.dart
lib/auth/auth_api.dart
lib/auth/session_manager.dart
lib/auth/session_storage.dart
lib/auth/session_gate.dart
lib/auth/login_screen.dart
lib/auth/register_screen.dart
lib/auth/verify_email_screen.dart
lib/provisioning/provisioning_contract.dart
lib/provisioning/provisioning_screen.dart
lib/devices/devices_api.dart
lib/devices/devices_screen.dart
lib/devices/device_models.dart
```

Tests principaux :

```text
test/auth/*
test/provisioning/provisioning_contract_test.dart
test/devices/devices_api_test.dart
test/devices/devices_screen_test.dart
```

## Backend FastAPI

Principales zones :

```text
app/auth.py
app/auth_sessions.py
app/email_verification.py
app/email_service.py
app/security.py
app/settings.py
app/devices.py
app/commands.py
app/mqtt_client.py
app/rate_limit.py
app/models.py
app/schemas.py
app/main.py
```

Modèles/tables impliqués :

- users ;
- auth_sessions ;
- devices ;
- device_memberships ;
- device_channels ;
- device_events ;
- power_measurements et télémétrie persistée.

## Firmware ESP32

Zones impliquées :

```text
include/BleManager.h
src/BleManager.cpp
include/MqttManager.h
src/MqttManager.cpp
src/SmartMonitor.cpp
src/CommandEngine.cpp
```

Le firmware reste responsable :

- de la fenêtre physique de pairing ;
- du nonce BLE ;
- des credentials Wi-Fi ;
- de la publication claim-proof ;
- de la connexion MQTT ;
- de l'exécution Ping/get_status ;
- de la réponse ACK/NACK.

# Éléments de preuve conservés

Cette documentation a été consolidée à partir des artefacts SM-071 disponibles, notamment :

- `SM071_2_FULL_REVIEW.zip` ;
- `SM071_2_CORRECTIONS.zip` et `REVUE_SM071_2.txt` ;
- `SM071_2_backend_corrige` ;
- `SM071_3_FULL_REVIEW_CURRENT.zip` ;
- paquets `SM071_4B`, `SM071_4C`, `SM071_4D`, `SM071_4E`, `SM071_4E_APP`, `SM071_4F1` ;
- versions courantes des fichiers devices/MQTT/APP fournis pendant la validation ;
- logs Uvicorn/MQTT des tests réels ;
- validation finale Huawei + ESP32 du 22 août 2026.

# Règles à ne pas casser dans les futurs SM

1. **Ne pas remettre MQTT directement dans l'APP.**
2. **Ne pas confondre `device_uid` et `mqtt_device_id`.**
3. **Les droits sont contrôlés par le backend, jamais seulement par l'UI.**
4. **Chaque commande reste corrélée par `request_id`.**
5. **Un NACK est une réponse de protocole valide, différente d'une erreur HTTP/transport.**
6. **Les commandes restent bornées par timeout et rate limiting.**
7. **Les erreurs APP restent compréhensibles et cohérentes avec l'état réel.**
8. **Les dernières mesures connues peuvent rester visibles offline, mais jamais comme temps réel.**
9. **La preuve de pairing reste liée à une action physique et à usage unique.**
10. **Les reports NACK/4F-2/502/504 restent tracés tant qu'ils ne sont pas réellement effectués.**

# Conclusion

SM-071 constitue une étape majeure de l'architecture SmartMonitor : APP, backend, PostgreSQL, MQTT et firmware fonctionnent maintenant comme une chaîne cohérente plutôt que comme des blocs indépendants.

Les acquis structurants sont :

- authentification et session durable ;
- association physique sécurisée ;
- séparation identité matérielle / routage MQTT ;
- owner/member avec enforcement backend ;
- APP sans MQTT direct ;
- commandes corrélées et auditées ;
- Ping et get_status réels ;
- gestion ACK/NACK ;
- rate limiting + Retry-After ;
- reprise après panne backend/broker/ESP32 ;
- UX offline et erreurs cohérente ;
- singleton MQTT backend V1 ;
- validation verticale réelle sur Android et ESP32.

Le périmètre SM-071 est suffisamment stable pour passer à l'étape suivante de la roadmap : **infrastructure MQTT distante et TLS de production**, tout en conservant explicitement les validations physiques reportées.
