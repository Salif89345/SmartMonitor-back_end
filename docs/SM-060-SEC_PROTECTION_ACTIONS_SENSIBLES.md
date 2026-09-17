# SM-060-SEC — Protection des actions sensibles

## Objectif

Empêcher qu’une commande de diagnostic, de configuration, de maintenance ou
de mise à jour contourne la politique d’autorisation du backend.

## Chaîne de contrôle

1. Le backend authentifie l’utilisateur.
2. Il vérifie son association et son rôle pour l’appareil ciblé.
3. Une table unique classe chaque commande et refuse les commandes inconnues.
4. Les commandes critiques exigent une confirmation liée à la commande et à
   l’UID matériel de l’appareil.
5. Le backend enveloppe les commandes sensibles dans un message signé avec une
   clé HMAC-SHA256 unique par appareil.
6. L’ESP vérifie la clé, son UID, la durée de validité, le request_id et le MAC
   avant d’exécuter la commande interne.
7. Une commande sensible MQTT non signée est refusée.
8. Les événements de commande conservent l’utilisateur, l’action et le niveau
   de risque sans journaliser les paramètres ni les secrets.

## Décisions

- `ping` reste compatible avec le contrat V1 non signé pour le diagnostic de
  disponibilité minimal.
- Toutes les autres commandes actuellement supportées par le firmware sont
  protégées cryptographiquement.
- Les commandes futures `restart` et `factory_reset` sont réservées dans la
  politique mais aucun endpoint ni handler n’est ajouté par ce jalon.
- Le backend reste l’autorité des droits utilisateur. Le firmware vérifie une
  autorisation du backend, pas un jeton utilisateur.
- La clé de développement est générée localement, n’est jamais affichée et
  reste exclue de Git et des archives.

## Limites et suite production

La clé locale est stockée dans la flash de l’ESP de développement. Avant la
production, elle devra être injectée individuellement pendant le provisioning
et protégée par le dispositif de sécurité matérielle retenu, notamment le
Secure Boot et le chiffrement de flash. Le stockage backend local devra être
remplacé par un gestionnaire de secrets. Les ACL et certificats du broker
restent dans le périmètre de SM-062-SEC.

Ces limites sont explicites. Aucun eFuse de sécurité n’est programmé par ce
jalon et aucune opération irréversible n’est réalisée.

## Critères de validation

- rôle `member`, rôle inconnu et commande inconnue refusés ;
- confirmation incorrecte refusée pour chaque commande critique ;
- clé unique liée à l’UID matériel ;
- commande sensible directe refusée par l’ESP ;
- commande `get_status` autorisée via le backend ;
- secret et paramètres absents des logs et événements ;
- tests backend complets et compilation firmware réussis.
