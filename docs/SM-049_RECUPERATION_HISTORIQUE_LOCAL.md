# SM-049 - Recuperation backend de l'historique local

## Probleme traite

Le firmware conservait les points indisponibles pendant une coupure et savait
les restituer par pages, mais le backend ne les recuperait pas. Les commandes
directes etant desormais protegees par SM-060-SEC, ce manque empechait une
reprise automatique conforme au modele de securite.

## Solution

`LocalHistoryRecoveryCoordinator` execute la recuperation hors des callbacks
MQTT et hors de la file d'ingestion temps reel. Une seule recuperation peut
etre active par appareil.

Pour chaque page :

1. le backend envoie `get_history` dans une enveloppe HMAC signee ;
2. il valide l'ordre, la taille maximale de deux points et le curseur ;
3. il conserve le point JSON complet dans `local_history_records` ;
4. il insere les valeurs electriques normalisees dans `power_measurements` ;
5. il reconstruit les resumes des jours termines touches par un retard ;
6. il valide la transaction SQL ;
7. seulement ensuite, il envoie `ack_history` signe pour ce curseur exact.

En cas de panne avant l'etape 7, la page sera rejouee. Les contraintes uniques
sur `(device_id, sequence)` et `(channel_id, measured_at)` rendent ce rejeu
idempotent.

## Securite

Le service technique `local_history_recovery` est explicitement distingue
d'un utilisateur. Son autorisation est fermee par defaut et limitee a
`get_history` et `ack_history`. Chaque reponse est inscrite dans le journal des
evenements avec `actor_service`, l'action et le niveau de risque. Aucun secret
ni contenu de configuration n'est journalise.

## Declenchement et usure

La recuperation est lancee au premier etat recu apres demarrage du backend,
puis seulement lorsqu'un compteur de recuperation Network ou MQTT change.
Elle n'acquitte donc pas chaque point produit en fonctionnement normal. Un
echec applique une temporisation avant nouvelle tentative.

## Schema SQL

La migration `f2a3b4c5d6e7` ajoute la table immuable
`local_history_records`. Le JSON complet y est conserve pour l'audit et pour
de futures exploitations, notamment environnementales. La table existante
`power_measurements` reste la source normalisee des graphiques electriques.

## Preuves du 15 septembre 2026

- migration Alembic appliquee de `e1f2a3b4c5d6` a `f2a3b4c5d6e7` ;
- 25 enregistrements importes, sequences 8 a 32 sans trou ;
- 13 pages lues et 13 acquittements signes acceptes ;
- aucune operation NACK dans ce cycle ;
- resume du 12 septembre recalcule a 305 points, identique aux mesures brutes ;
- 119 tests backend reussis ;
- API, PostgreSQL et MQTT operationnels apres redemarrage.

## Fichiers principaux

- `app/local_history_recovery.py` : orchestration, validation et persistance ;
- `app/command_security.py` : autorisation du service interne ;
- `app/mqtt_client.py` : declenchement, transport signe et audit de l'acteur ;
- `app/models.py` : journal immuable des points locaux ;
- `app/power_daily_summary.py` : reconstruction transactionnelle ;
- `alembic/versions/f2a3b4c5d6e7_add_local_history_records.py` : migration ;
- `tests/test_local_history_recovery.py` : pagination, curseurs et dedoublonnage.
