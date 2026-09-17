# SM-062-SEC - Sécurisation du broker de production

## Objectif

Déployer un chemin MQTT de production distinct du laboratoire, chiffré,
authentifié, limité au moindre privilège et exploitable avec une rotation de
secrets traçable.

## Changements logiciels

Le backend refuse désormais de démarrer en `APP_ENV=prod` si :

- l'environnement MQTT n'est pas explicitement `prod` ;
- le port n'est pas `8883` ;
- l'hôte ne figure pas dans la liste de production autorisée ;
- le Client ID backend n'utilise pas le préfixe de production ;
- l'identifiant de rotation des credentials est absent ;
- TLS, l'authentification ou le certificat CA sont incomplets.

Le contexte TLS vérifie explicitement le certificat serveur et le nom d'hôte,
avec TLS 1.2 au minimum.

## Politique du broker

Le broker de production doit être un déploiement séparé de
`SmartMonitor-Lab`. Il utilise des identités distinctes :

- une identité backend de production ;
- une identité et un mot de passe uniques par appareil ;
- un Client ID appareil égal au `device_uid` issu de l'eFuse ;
- des ACL appareil limitées à l'alias MQTT réellement attribué ;
- un refus global final sur `#`.

Le backend conserve les droits transverses nécessaires à son rôle de relais.
L'application mobile ne reçoit aucun identifiant MQTT.

## Certificats et compromis V1

La V1 conserve TLS serveur avec validation CA et authentification MQTT par
identifiants uniques. Le mTLS par certificat client n'est pas simulé sur le
déploiement Serverless actuel. Il exige une offre compatible, une autorité
cliente privée, un certificat et une clé par appareil, une procédure de
révocation et un provisionnement industriel validé.

Secure Boot, Flash Encryption et NVS Encryption ne sont pas activés sur la
carte de développement. Ces mécanismes irréversibles doivent être éprouvés sur
des cartes réservées à la préproduction.

## Rotation

La rotation est réalisée en chevauchement : nouvelle identité et nouvelles
ACL, bascule contrôlée, validation positive et négative, puis révocation de
l'ancienne identité. Seuls l'identifiant de rotation, les dates et les preuves
de test sont archivés.

## Validation requise avant clôture

1. contrôles statiques et tests backend ;
2. démarrage DEV inchangé ;
3. refus d'une configuration PROD pointant hors liste blanche ;
4. broker de production distinct créé ;
5. authentification backend et appareil avec secrets distincts ;
6. matrice ACL positive et négative exécutée ;
7. ancienne identité refusée après un exercice de rotation ;
8. test bout en bout application, API, broker et ESP32 ;
9. archive de preuve sans secret.

Le jalon n'est pas clôturé tant que les points 4 à 9 n'ont pas été observés sur
le vrai broker de production.
