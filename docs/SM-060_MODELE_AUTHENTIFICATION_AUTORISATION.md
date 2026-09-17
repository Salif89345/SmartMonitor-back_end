# SM-060 — Modèle d’authentification et d’autorisation

## Objectif

Définir une frontière de confiance unique entre l’utilisateur, le backend,
le broker MQTT et le SmartMonitor. Les décisions d’accès utilisateur sont
prises par le backend. Le firmware ne reçoit pas et ne traite pas de jeton
utilisateur.

## Chaîne de confiance V1

1. L’application authentifie l’utilisateur auprès du backend par une session
   HTTP avec jeton d’accès.
2. Le backend vérifie l’association entre l’utilisateur et l’appareil dans
   `device_memberships`.
3. Le rôle `owner` ou `member` est évalué par la matrice centralisée de
   `app/device_authorization.py`.
4. Le backend publie les commandes autorisées sur le topic MQTT de l’identité
   de routage de l’appareil.
5. Le SmartMonitor accepte uniquement son topic de commande. Les commandes
   persistantes utilisent le journal NVS et le `request_id` pour empêcher une
   réexécution silencieuse.

## Matrice V1

| Action | Propriétaire | Membre |
| --- | --- | --- |
| Lire l’appareil et sa télémétrie | Oui | Oui |
| Lire l’historique | Oui | Oui |
| Lire les alarmes et événements | Oui | Oui |
| Acquitter une alarme | Oui | Oui |
| Exécuter un diagnostic | Oui | Non |
| Modifier la configuration | Oui | Non |
| Lancer une opération de maintenance | Oui | Non |
| Gérer les membres | Oui | Non |

L’acquittement d’une alarme reste accessible aux membres afin de conserver le
comportement actuel de l’application. Cette action est traçable dans les
événements de l’appareil.

## Protections déjà actives

- sessions backend et vérification de l’utilisateur courant ;
- unicité du propriétaire par appareil en base de données ;
- preuve d’association physique à usage unique ;
- séparation entre `device_uid` matériel et `mqtt_device_id` de routage ;
- limitation de fréquence des commandes ;
- corrélation commande/réponse par `request_id` ;
- journal NVS anti-rejeu pour les commandes qui modifient durablement
  l’appareil ;
- transport MQTT TLS avec validation du certificat du broker.

## Décisions et limites

- Le backend est l’autorité d’accès des utilisateurs. Ajouter un jeton
  utilisateur dans le payload MQTT créerait un second système d’autorisation
  fragile et n’est pas retenu.
- Le `request_id` et le journal NVS assurent l’idempotence. Ils ne constituent
  pas une signature cryptographique.
- Les identifiants de broker actuels ne constituent pas encore une identité
  cryptographique individuelle de production.
- Les certificats par appareil, la rotation des secrets, les ACL du broker et
  la séparation stricte des environnements restent couverts par SM-057-SEC,
  SM-058-SEC, SM-060-SEC, SM-061-SEC et SM-062-SEC.

## Critères de validation

- toute action déclarée est présente dans une matrice unique ;
- un rôle inconnu est refusé par défaut ;
- les membres ne peuvent ni envoyer de commande, ni modifier la configuration,
  ni lancer une maintenance, ni gérer les membres ;
- les contrôles existants des routes appareil utilisent la matrice ;
- la suite de tests backend reste verte.
